"""FastAPI gateway — REST + WebSocket + static dashboard (doc 01 §2⑦).

Run: .venv/bin/uvicorn intellidhan_gateway.app:app --port 8321
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse

from intellidhan_gateway.auth import (
    OWNER_COOKIE,
    SESSION_COOKIE,
    RateLimiter,
    SESSION_MAX_AGE_SECONDS,
    create_account_session,
    hash_password,
    invite_configured,
    owner_configured,
    request_principal,
    require_owner,
    require_user,
    session_cookie_value,
    session_token_hash,
    verify_invite_code,
    verify_owner_token,
    verify_password,
    websocket_principal,
)
from intellidhan_gateway.discovery import DiscoveryService, PRESETS
from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.stock_analysis import StockAnalysisService

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

loop = LiveLoop()
stock_analyzer = StockAnalysisService()
discovery = DiscoveryService()
rate_limiter = RateLimiter()

DEFAULT_PREFERENCES = {
    "theme": "dark",
    "default_view": "signals",
    "compact_cards": False,
    "alert_sound": False,
    "reduced_motion": False,
}
VALID_ROLES = {"ADMIN", "TRADER", "VIEWER"}
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-password")
WS_SESSION_RECHECK_SECONDS = 30.0


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
    principal = request_principal(request, loop.store)
    if principal and principal.role == "ADMIN":
        return
    if principal:
        raise HTTPException(status_code=403, detail="administrator access is required")
    _require_token(request, "AUTOTRADE_CONTROL_TOKEN", control=True)


def _require_personal(request: Request, *, roles: set[str] | None = None):
    """Use accounts when present and retain fail-closed legacy behavior."""
    if loop.store.count_users() == 0 and not owner_configured():
        require_owner(request)
    return require_user(request, loop.store, roles=roles)


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https" or os.getenv(
        "INTELLIDHAN_SECURE_COOKIE", ""
    ).lower() in {"1", "true", "yes"}


def _account_response(request: Request, user: dict, token: str) -> JSONResponse:
    response = JSONResponse({"authenticated": True, "user": user})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
    )
    response.delete_cookie(OWNER_COOKIE, path="/")
    return response


def _validate_email(value: str) -> str:
    value = value.strip().lower()
    local, separator, domain = value.partition("@")
    if (
        not separator
        or not local
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or len(value) > 254
    ):
        raise ValueError("enter a valid email address")
    return value


def _public_user(user: dict) -> dict:
    return {key: user.get(key) for key in (
        "user_id", "email", "display_name", "role", "active", "created_at",
        "updated_at", "last_login_at",
    ) if key in user}


def _validate_capital_limits(payload: dict) -> dict[str, dict]:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("capital limits must include at least one trading module")
    allowed = set(loop.composer.budgets.as_dict())
    clean: dict[str, dict] = {}
    for module, raw in payload.items():
        if module not in allowed or not isinstance(raw, dict):
            raise ValueError(f"unknown trading module {module!r}")
        cap_key = "daily_capital" if "daily_capital" in raw else "standing_capital"
        if cap_key not in raw:
            raise ValueError(f"{module}: capital limit is required")
        capital = float(raw[cap_key])
        risk_cap = float(raw.get("risk_cap_pct", 0))
        if not 0 < capital <= 100_000_000:
            raise ValueError(f"{module}: capital must be between 0 and 100,000,000")
        if not 0 < risk_cap <= 1:
            raise ValueError(f"{module}: risk limit must be between 0% and 100%")
        item = {cap_key: capital, "risk_cap_pct": risk_cap}
        if "max_alerts_per_day" in raw:
            max_alerts = int(raw["max_alerts_per_day"])
            if not 1 <= max_alerts <= 100:
                raise ValueError(f"{module}: daily alert limit must be 1–100")
            item["max_alerts_per_day"] = max_alerts
        clean[module] = item
    return clean


@app.get("/api/auth/session")
async def auth_session(request: Request):
    account_count = loop.store.count_users()
    principal = request_principal(request, loop.store)
    configured = account_count > 0 or owner_configured()
    if not configured:
        return {"configured": False, "authenticated": False}
    return {
        "configured": True,
        "authenticated": principal is not None,
        "accounts_enabled": account_count > 0,
        "registration_enabled": (
            owner_configured()
            if account_count == 0
            else invite_configured()
        ),
        "user": principal.public() if principal else None,
    }


@app.post("/api/auth/session")
async def create_auth_session(request: Request, payload: dict = Body(...)):
    rate_limiter.check(_client_key(request, "login"), limit=5, window_seconds=60)
    if payload.get("email") is not None:
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))
        user = loop.store.get_user_by_email(email, include_password=True)
        password_hash = user.get("password_hash", "") if user else _DUMMY_PASSWORD_HASH
        password_valid = verify_password(password, password_hash)
        if not user or not user["active"] or not password_valid:
            raise HTTPException(status_code=401, detail="email or password is incorrect")
        loop.store.mark_user_login(user["user_id"])
        token, _ = create_account_session(loop.store, user["user_id"])
        return _account_response(request, _public_user(user), token)
    if not owner_configured():
        raise HTTPException(status_code=503, detail="account sign-in is not configured")
    if not verify_owner_token(str(payload.get("token", ""))):
        raise HTTPException(status_code=401, detail="invalid admin setup code")
    account_token = request.cookies.get(SESSION_COOKIE, "")
    if account_token:
        loop.store.revoke_user_session(session_token_hash(account_token))
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        OWNER_COOKIE,
        session_cookie_value(),
        httponly=True,
        secure=_secure_cookie(request),
        samesite="strict",
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
    )
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.post("/api/auth/register")
async def register_account(request: Request, payload: dict = Body(...)):
    rate_limiter.check(_client_key(request, "register"), limit=3, window_seconds=300)
    first_account = loop.store.count_users() == 0
    if first_account and not owner_configured():
        raise HTTPException(
            status_code=503, detail="initial administrator setup is not configured"
        )
    if not first_account and not invite_configured():
        raise HTTPException(status_code=503, detail="account registration is not configured")
    if not verify_invite_code(
        str(payload.get("invite_code", "")), first_account=first_account
    ):
        raise HTTPException(status_code=401, detail="invite or admin setup code is incorrect")
    try:
        user = loop.store.create_user(
            email=_validate_email(str(payload.get("email", ""))),
            display_name=str(payload.get("display_name", "")),
            password_hash=hash_password(str(payload.get("password", ""))),
            role="ADMIN" if first_account else "TRADER",
            preferences=dict(DEFAULT_PREFERENCES),
            capital_limits=loop.composer.budgets.as_dict(),
            default_watchlist="Research",
            require_first=first_account,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token, _ = create_account_session(loop.store, user["user_id"])
    return _account_response(request, _public_user(user), token)


@app.get("/api/accounts")
async def list_accounts(request: Request):
    _require_personal(request, roles={"ADMIN"})
    return {"accounts": loop.store.list_users()}


@app.post("/api/accounts")
async def create_account(request: Request, payload: dict = Body(...)):
    _require_personal(request, roles={"ADMIN"})
    try:
        role = str(payload.get("role", "TRADER")).upper()
        if role not in VALID_ROLES:
            raise ValueError("role must be ADMIN, TRADER, or VIEWER")
        user = loop.store.create_user(
            email=_validate_email(str(payload.get("email", ""))),
            display_name=str(payload.get("display_name", "")),
            password_hash=hash_password(str(payload.get("password", ""))),
            role=role,
            preferences=dict(DEFAULT_PREFERENCES),
            capital_limits=loop.composer.budgets.as_dict(),
            default_watchlist="Research",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _public_user(user)


@app.delete("/api/auth/session")
async def delete_auth_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        loop.store.revoke_user_session(session_token_hash(token))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(OWNER_COOKIE, path="/")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/account/preferences")
async def get_preferences(request: Request):
    principal = _require_personal(request)
    if principal.legacy:
        return DEFAULT_PREFERENCES
    return {**DEFAULT_PREFERENCES, **loop.store.get_user_preferences(principal.user_id)}


@app.put("/api/account/preferences")
async def put_preferences(request: Request, updates: dict = Body(...)):
    principal = _require_personal(request)
    if principal.legacy:
        raise HTTPException(
            status_code=409, detail="create an account before saving personal preferences"
        )
    allowed = set(DEFAULT_PREFERENCES)
    if not isinstance(updates, dict) or set(updates) - allowed:
        raise HTTPException(status_code=422, detail="one or more preferences are unsupported")
    preferences = {
        **DEFAULT_PREFERENCES,
        **loop.store.get_user_preferences(principal.user_id),
        **updates,
    }
    if preferences["theme"] not in {"dark", "light", "system"}:
        raise HTTPException(status_code=422, detail="theme must be dark, light, or system")
    if preferences["default_view"] not in {"signals", "discover", "analysis"}:
        raise HTTPException(status_code=422, detail="default view is unsupported")
    for key in ("compact_cards", "alert_sound", "reduced_motion"):
        if not isinstance(preferences[key], bool):
            raise HTTPException(status_code=422, detail=f"{key} must be true or false")
    loop.store.put_user_preferences(principal.user_id, preferences)
    return preferences


@app.get("/api/health")
async def health():
    payload = loop.health()
    return JSONResponse(jsonable_encoder(payload), status_code=200 if payload["ok"] else 503)


@app.get("/api/liveness")
async def liveness():
    """Process-level probe; strict signal readiness remains at ``/api/health``."""
    alive = loop.loop_state != "STOPPED"
    return JSONResponse(
        {"ok": alive, "service": "intellidhan-gateway", "loop_state": loop.loop_state},
        status_code=200 if alive else 503,
    )


@app.get("/api/state")
async def state(request: Request):
    _require_personal(request)
    return JSONResponse(jsonable_encoder(loop.snapshot()))


@app.get("/api/calibration")
async def calibration():
    out = {}
    for f in sorted(Path("config/calibration").glob("*.json")):
        out[f.stem] = json.loads(f.read_text())
    return out


@app.get("/api/briefing")
async def briefing(request: Request):
    _require_personal(request)
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
    principal = request_principal(request, loop.store)
    if principal and principal.legacy:
        watchlists = loop.store.watchlists_for_symbol(symbol)
    elif principal:
        watchlists = loop.store.user_watchlists_for_symbol(principal.user_id, symbol)
    else:
        watchlists = []
    return {
        "security": loop.store.get_security(symbol) or {
            "symbol": symbol.upper(),
            "name": symbol.upper(),
            "source": "on-demand",
        },
        "watchlists": watchlists,
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
    principal = _require_personal(request)
    watchlists = (
        loop.store.list_watchlists()
        if principal.legacy
        else loop.store.list_user_watchlists(principal.user_id)
    )
    return {"watchlists": watchlists}


@app.post("/api/watchlists")
async def create_watchlist(request: Request, payload: dict = Body(...)):
    principal = _require_personal(request)
    try:
        if principal.legacy:
            return loop.store.create_watchlist(str(payload.get("name", "")))
        return loop.store.create_user_watchlist(
            principal.user_id, str(payload.get("name", ""))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/watchlists/{watchlist_id}/symbols/{symbol}")
async def add_watchlist_symbol(
    watchlist_id: str, symbol: str, request: Request, payload: dict = Body(default={})
):
    principal = _require_personal(request)
    try:
        if principal.legacy:
            loop.store.add_watchlist_member(
                watchlist_id, symbol, str(payload.get("note", ""))
            )
        else:
            loop.store.add_user_watchlist_member(
                principal.user_id, watchlist_id, symbol, str(payload.get("note", ""))
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"watchlist_id": watchlist_id, "symbol": symbol.upper(), "saved": True}


@app.delete("/api/watchlists/{watchlist_id}/symbols/{symbol}")
async def remove_watchlist_symbol(watchlist_id: str, symbol: str, request: Request):
    principal = _require_personal(request)
    if principal.legacy:
        loop.store.remove_watchlist_member(watchlist_id, symbol)
    else:
        loop.store.remove_user_watchlist_member(principal.user_id, watchlist_id, symbol)
    return {"watchlist_id": watchlist_id, "symbol": symbol.upper(), "saved": False}


@app.get("/api/screens")
async def list_saved_screens(request: Request):
    principal = _require_personal(request)
    screens = (
        loop.store.list_saved_screens()
        if principal.legacy
        else loop.store.list_user_saved_screens(principal.user_id)
    )
    return {"screens": screens}


@app.post("/api/screens")
async def save_screen(request: Request, payload: dict = Body(...)):
    principal = _require_personal(request)
    try:
        name = str(payload.get("name", ""))
        filters = dict(payload.get("filters") or {})
        if principal.legacy:
            return loop.store.put_saved_screen(name, filters)
        return loop.store.put_user_saved_screen(principal.user_id, name, filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/budgets")
async def get_budgets(request: Request):
    principal = _require_personal(request)
    if principal.legacy:
        return loop.composer.budgets.as_dict()
    limits = loop.store.get_user_capital_limits(principal.user_id)
    if not limits:
        limits = loop.composer.budgets.as_dict()
        loop.store.put_user_capital_limits(principal.user_id, limits)
    return limits


@app.put("/api/budgets")
async def put_budgets(request: Request, new_budgets: dict = Body(...)):
    """Save personal capital and risk limits for the signed-in account.

    The legacy administrator route retains its original shared-engine behavior
    until that installation is migrated to accounts.
    """
    principal = _require_personal(request)
    try:
        clean = _validate_capital_limits(new_budgets)
        if principal.legacy:
            loop.composer.budgets.update(clean)
            loop.store.put_setting("budgets", clean)
        else:
            loop.store.put_user_capital_limits(principal.user_id, clean)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return clean


@app.get("/api/autotrade")
async def get_autotrade(request: Request):
    """Account-only policy/status; broker credentials never enter this app."""
    _require_personal(request)
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
    principal = websocket_principal(websocket, loop.store)
    if principal is None:
        await websocket.close(code=4401, reason="sign-in required")
        return
    expires_at = principal.expires_at
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    loop.ws_subscribers.append(q)
    queue_task = asyncio.create_task(q.get())
    receive_task = asyncio.create_task(websocket.receive())
    expiry_task = asyncio.create_task(
        asyncio.sleep(max(0.0, expires_at - time.time()))
    )
    session_check_task = asyncio.create_task(
        asyncio.sleep(WS_SESSION_RECHECK_SECONDS)
    )
    try:
        while True:
            done, _ = await asyncio.wait(
                {queue_task, receive_task, expiry_task, session_check_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if expiry_task in done:
                await websocket.close(code=4401, reason="account session expired")
                break
            if session_check_task in done:
                current = websocket_principal(websocket, loop.store)
                if current is None or current.user_id != principal.user_id:
                    await websocket.close(
                        code=4401, reason="account session revoked or expired"
                    )
                    break
                session_check_task = asyncio.create_task(
                    asyncio.sleep(WS_SESSION_RECHECK_SECONDS)
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
        expiry_task.cancel()
        session_check_task.cancel()
        if q in loop.ws_subscribers:
            loop.ws_subscribers.remove(q)


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")
