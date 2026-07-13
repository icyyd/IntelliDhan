"""FastAPI gateway — REST + WebSocket + static dashboard (doc 01 §2⑦).

Run: .venv/bin/uvicorn intellidhan_gateway.app:app --port 8321
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.stock_analysis import StockAnalysisService

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

loop = LiveLoop()
stock_analyzer = StockAnalysisService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(loop.run_forever())
    yield
    task.cancel()


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


@app.get("/api/health")
async def health():
    return {"ok": True, "started_at": loop.started_at, "last_poll": loop.last_poll}


@app.get("/api/state")
async def state():
    return JSONResponse(loop.snapshot())


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
    symbol: str,
    years: int = Query(5, ge=2, le=15),
    risk_budget: float | None = Query(None, gt=0, le=1_000_000),
    include_backtest: bool = True,
    cost_bps: float = Query(10.0, ge=0, le=100),
):
    """Corporate-action-adjusted daily trend analysis for an arbitrary ticker."""
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


@app.get("/api/budgets")
async def get_budgets():
    return loop.composer.budgets.as_dict()


@app.put("/api/budgets")
async def put_budgets(new_budgets: dict = Body(...)):
    """Persist edited budgets to config/budgets.yaml; sizing picks them up on
    the very next alert — no restart (doc 09 §3, hot-reload)."""
    try:
        loop.composer.budgets.update(new_budgets)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return loop.composer.budgets.as_dict()


@app.get("/api/autotrade")
async def get_autotrade():
    """Credential-free status for the dashboard; never returns broker data."""
    return loop.autotrade.status()


@app.put("/api/autotrade/policy")
async def put_autotrade_policy(request: Request, updates: dict = Body(...)):
    _require_token(request, "AUTOTRADE_CONTROL_TOKEN", control=True)
    try:
        return loop.autotrade.update_policy(updates).model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _autotrade_error(exc) from exc


@app.post("/api/autotrade/disarm")
async def disarm_autotrade(request: Request):
    _require_token(request, "AUTOTRADE_CONTROL_TOKEN", control=True)
    return loop.autotrade.update_policy({"mode": "OFF"}).model_dump(mode="json")


@app.post("/api/autotrade/intents/from-alert/{alert_id}")
async def create_intent_from_alert(alert_id: str, request: Request):
    """Explicitly evaluate an existing active alert after policy arming."""
    _require_token(request, "AUTOTRADE_CONTROL_TOKEN", control=True)
    alert = next((item for item in loop.alerts if item.alert_id == alert_id), None)
    if alert is None:
        raise HTTPException(status_code=404, detail="unknown alert")
    intent = loop.autotrade.on_alert(alert)
    if intent is None:
        raise HTTPException(status_code=409, detail="automation mode is OFF")
    return intent.model_dump(mode="json")


@app.post("/api/autotrade/intents/{intent_id}/approve")
async def approve_autotrade_intent(intent_id: str, request: Request):
    _require_token(request, "AUTOTRADE_CONTROL_TOKEN", control=True)
    try:
        return loop.autotrade.approve(intent_id).model_dump(mode="json")
    except (ValueError, KeyError) as exc:
        raise _autotrade_error(exc) from exc


@app.post("/api/autotrade/intents/{intent_id}/reject")
async def reject_autotrade_intent(
    intent_id: str, request: Request, payload: dict = Body(default={})
):
    _require_token(request, "AUTOTRADE_CONTROL_TOKEN", control=True)
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
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    loop.ws_subscribers.append(q)
    try:
        while True:
            msg = await q.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        loop.ws_subscribers.remove(q)


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")
