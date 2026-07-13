"""FastAPI gateway — REST + WebSocket + static dashboard (doc 01 §2⑦).

Run: .venv/bin/uvicorn intellidhan_gateway.app:app --port 8321
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse

from intellidhan_gateway.auth import (
    OWNER_COOKIE,
    RateLimiter,
    owner_configured,
    request_is_owner,
    require_owner,
    session_cookie_value,
    verify_owner_token,
    websocket_is_owner,
)
from intellidhan_gateway.discovery import DiscoveryService, PRESETS
from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.stock_analysis import StockAnalysisService

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

loop = LiveLoop()
stock_analyzer = StockAnalysisService()
discovery = DiscoveryService()
rate_limiter = RateLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        loop.store.init_schema()
        if not loop.store.list_watchlists():
            loop.store.create_watchlist("Research")
    except Exception as exc:
        loop.last_error = f"operational store initialization failed: {exc}"
    task = asyncio.create_task(loop.run_forever())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="IntelliDhan", lifespan=lifespan)


def _require_token(request: Request, env_name: str, *, control: bool = False) -> None:
    expected = os.getenv(env_name)
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"{env_name} is not configured; auto-trade control is fail-closed",
        )
    if control:
        provided = request.headers.get("x-autotrade-token", "")
    else:
        auth = request.headers.get("authorization", "")
        provided = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid auto-trade token")


def _autotrade_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _client_key(request: Request, surface: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{surface}:{host}"


def _require_control(request: Request) -> None:
    if request_is_owner(request):
        return
    _require_token(request, "AUTOTRADE_CONTROL_TOKEN", control=True)


@app.get("/api/auth/session")
async def auth_session(request: Request):
    return {
        "configured": owner_configured(),
        "authenticated": request_is_owner(request),
    }


@app.post("/api/auth/session")
async def create_auth_session(request: Request, payload: dict = Body(...)):
    rate_limiter.check(_client_key(request, "login"), limit=5, window_seconds=60)
    if not owner_configured():
        raise HTTPException(status_code=503, detail="owner authentication is not configured")
    if not verify_owner_token(str(payload.get("token", ""))):
        raise HTTPException(status_code=401, detail="invalid owner token")
    response = JSONResponse({"authenticated": True})
    secure = request.url.scheme == "https" or os.getenv(
        "INTELLIDHAN_SECURE_COOKIE", ""
    ).lower() in {"1", "true", "yes"}
    response.set_cookie(
        OWNER_COOKIE,
        session_cookie_value(),
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=60 * 60 * 12,
        path="/",
    )
    return response


@app.delete("/api/auth/session")
async def delete_auth_session():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(OWNER_COOKIE, path="/")
    return response


@app.get("/api/health")
async def health():
    payload = loop.health()
    return JSONResponse(jsonable_encoder(payload), status_code=200 if payload["ok"] else 503)


@app.get("/api/state")
async def state():
    return JSONResponse(jsonable_encoder(loop.snapshot()))


@app.get("/api/calibration")
async def calibration():
    out = {}
    for f in sorted(Path("config/calibration").glob("*.json")):
        out[f.stem] = json.loads(f.read_text())
    return out


@app.get("/api/briefing")
async def briefing():
    return loop.last_briefing or {"status": "not generated yet (8:30 ET on trading days)"}


@app.get("/api/analyze/{symbol}")
async def analyze_stock(
    request: Request,
    symbol: str,
    years: int = Query(10, ge=2, le=15),
    risk_budget: float | None = Query(None, gt=0, le=1_000_000),
    include_backtest: bool = True,
    cost_bps: float = Query(10.0, ge=0, le=100),
):
    """Corporate-action-adjusted daily trend analysis for an arbitrary ticker."""
    rate_limiter.check(_client_key(request, "analyze"), limit=30, window_seconds=60)
    try:
        return await asyncio.wait_for(
            stock_analyzer.analyze(
                symbol,
                years=years,
                risk_budget=risk_budget,
                include_backtest=include_backtest,
                cost_bps=cost_bps,
            ),
            timeout=30,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="stock analysis provider timed out") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="stock analysis provider failed") from exc


@app.get("/api/discover/presets")
async def discovery_presets():
    return {
        "presets": [
            {"key": key, "filters": filters}
            for key, filters in PRESETS.items()
        ],
        "coverage": "TECHNICAL_ONLY",
    }


@app.get("/api/discover")
async def discover_stocks(
    request: Request,
    preset: str | None = Query(None),
    min_price: float | None = Query(None, ge=0),
    min_adv_dollars: float | None = Query(None, ge=0),
    above_sma200: bool | None = Query(None),
    min_return_6m: float | None = Query(None, ge=-100, le=1000),
    max_volatility_pct: float | None = Query(None, gt=0, le=500),
    max_distance_from_high_pct: float | None = Query(None, ge=0, le=100),
    refresh: bool = Query(False),
):
    if preset and preset not in PRESETS:
        raise HTTPException(status_code=422, detail="unknown discovery preset")
    rate_limiter.check(_client_key(request, "discover"), limit=12, window_seconds=60)
    try:
        return await asyncio.wait_for(
            discovery.screen(
                preset=preset,
                min_price=min_price,
                min_adv_dollars=min_adv_dollars,
                above_sma200=above_sma200,
                min_return_6m=min_return_6m,
                max_volatility_pct=max_volatility_pct,
                max_distance_from_high_pct=max_distance_from_high_pct,
                refresh=refresh,
            ),
            timeout=45,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="discovery provider timed out") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="discovery provider failed") from exc


@app.get("/api/dossier/{symbol}")
async def stock_dossier(
    request: Request,
    symbol: str,
    years: int = Query(10, ge=2, le=15),
    risk_budget: float | None = Query(None, gt=0, le=1_000_000),
    include_backtest: bool = True,
    cost_bps: float = Query(10.0, ge=0, le=100),
):
    rate_limiter.check(_client_key(request, "dossier"), limit=30, window_seconds=60)
    try:
        analysis = await asyncio.wait_for(
            stock_analyzer.analyze(
                symbol,
                years=years,
                risk_budget=risk_budget,
                include_backtest=include_backtest,
                cost_bps=cost_bps,
            ),
            timeout=30,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="stock analysis provider timed out") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="stock analysis provider failed") from exc
    return {
        "security": loop.store.get_security(symbol) or {
            "symbol": symbol.upper(),
            "name": symbol.upper(),
            "source": "on-demand",
        },
        "watchlists": (
            loop.store.watchlists_for_symbol(symbol) if request_is_owner(request) else []
        ),
        "analysis": analysis,
        "coverage": {
            "technicals": "AVAILABLE",
            "forward_outlook": "AVAILABLE",
            "fundamentals": "NOT_CONNECTED",
            "estimates": "NOT_CONNECTED",
            "events": "NOT_CONNECTED",
        },
    }


@app.get("/api/watchlists")
async def list_watchlists(request: Request):
    require_owner(request)
    return {"watchlists": loop.store.list_watchlists()}


@app.post("/api/watchlists")
async def create_watchlist(request: Request, payload: dict = Body(...)):
    require_owner(request)
    try:
        return loop.store.create_watchlist(str(payload.get("name", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/watchlists/{watchlist_id}/symbols/{symbol}")
async def add_watchlist_symbol(
    watchlist_id: str, symbol: str, request: Request, payload: dict = Body(default={})
):
    require_owner(request)
    try:
        loop.store.add_watchlist_member(watchlist_id, symbol, str(payload.get("note", "")))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"watchlist_id": watchlist_id, "symbol": symbol.upper(), "saved": True}


@app.delete("/api/watchlists/{watchlist_id}/symbols/{symbol}")
async def remove_watchlist_symbol(watchlist_id: str, symbol: str, request: Request):
    require_owner(request)
    loop.store.remove_watchlist_member(watchlist_id, symbol)
    return {"watchlist_id": watchlist_id, "symbol": symbol.upper(), "saved": False}


@app.get("/api/screens")
async def list_saved_screens(request: Request):
    require_owner(request)
    return {"screens": loop.store.list_saved_screens()}


@app.post("/api/screens")
async def save_screen(request: Request, payload: dict = Body(...)):
    require_owner(request)
    try:
        return loop.store.put_saved_screen(
            str(payload.get("name", "")), dict(payload.get("filters") or {})
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/budgets")
async def get_budgets(request: Request):
    require_owner(request)
    return loop.composer.budgets.as_dict()


@app.put("/api/budgets")
async def put_budgets(request: Request, new_budgets: dict = Body(...)):
    """Persist edited budgets to config/budgets.yaml; sizing picks them up on
    the very next alert — no restart (doc 09 §3, hot-reload)."""
    require_owner(request)
    try:
        loop.composer.budgets.update(new_budgets)
        loop.store.put_setting("budgets", loop.composer.budgets.as_dict())
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return loop.composer.budgets.as_dict()


@app.get("/api/autotrade")
async def get_autotrade():
    """Credential-free status for the dashboard; never returns broker data."""
    return loop.autotrade.status()


@app.put("/api/autotrade/policy")
async def put_autotrade_policy(request: Request, updates: dict = Body(...)):
    _require_control(request)
    try:
        return loop.autotrade.update_policy(updates).model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _autotrade_error(exc) from exc


@app.post("/api/autotrade/disarm")
async def disarm_autotrade(request: Request):
    _require_control(request)
    return loop.autotrade.update_policy({"mode": "OFF"}).model_dump(mode="json")


@app.post("/api/autotrade/intents/from-alert/{alert_id}")
async def create_intent_from_alert(alert_id: str, request: Request):
    """Explicitly evaluate an existing active alert after policy arming."""
    _require_control(request)
    alert = next((item for item in loop.alerts if item.alert_id == alert_id), None)
    if alert is None:
        raise HTTPException(status_code=404, detail="unknown alert")
    intent = loop.autotrade.on_alert(alert)
    if intent is None:
        raise HTTPException(status_code=409, detail="automation mode is OFF")
    return intent.model_dump(mode="json")


@app.post("/api/autotrade/intents/{intent_id}/approve")
async def approve_autotrade_intent(intent_id: str, request: Request):
    _require_control(request)
    try:
        return loop.autotrade.approve(intent_id).model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _autotrade_error(exc) from exc


@app.post("/api/autotrade/intents/{intent_id}/reject")
async def reject_autotrade_intent(
    intent_id: str, request: Request, payload: dict = Body(default={})
):
    _require_control(request)
    try:
        return loop.autotrade.reject(intent_id, payload.get("reason", "")).model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _autotrade_error(exc) from exc


@app.get("/api/autotrade/intents")
async def list_autotrade_intents(request: Request, status: str | None = None):
    _require_token(request, "AUTOTRADE_AGENT_TOKEN")
    try:
        intents = loop.autotrade.list_intents(status)
    except ValueError as exc:
        raise _autotrade_error(exc) from exc
    return {
        "contract_version": "1.0",
        "effective_mode": loop.autotrade.effective_mode().value,
        "intents": [item.model_dump(mode="json") for item in intents],
    }


@app.post("/api/autotrade/intents/{intent_id}/claim")
async def claim_autotrade_intent(
    intent_id: str, request: Request, payload: dict = Body(default={})
):
    _require_token(request, "AUTOTRADE_AGENT_TOKEN")
    try:
        return loop.autotrade.claim(intent_id, payload.get("agent", "claude")).model_dump(
            mode="json"
        )
    except (ValueError, KeyError) as exc:
        raise _autotrade_error(exc) from exc


@app.post("/api/autotrade/intents/{intent_id}/receipt")
async def record_autotrade_receipt(
    intent_id: str, request: Request, payload: dict = Body(...)
):
    _require_token(request, "AUTOTRADE_AGENT_TOKEN")
    try:
        return loop.autotrade.record_receipt(intent_id, payload).model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _autotrade_error(exc) from exc


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    if not websocket_is_owner(websocket):
        await websocket.close(code=4401, reason="owner sign-in required")
        return
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    loop.ws_subscribers.append(q)
    queue_task = asyncio.create_task(q.get())
    receive_task = asyncio.create_task(websocket.receive())
    try:
        while True:
            done, _ = await asyncio.wait(
                {queue_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if receive_task in done:
                incoming = receive_task.result()
                if incoming.get("type") == "websocket.disconnect":
                    break
                receive_task = asyncio.create_task(websocket.receive())
            if queue_task in done:
                await websocket.send_json(queue_task.result())
                queue_task = asyncio.create_task(q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        queue_task.cancel()
        receive_task.cancel()
        if q in loop.ws_subscribers:
            loop.ws_subscribers.remove(q)


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")
