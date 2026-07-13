"""Trustworthy-terminal foundation: durability, auth, DQ, discovery, parity."""

import asyncio
import time as wall_time
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import intellidhan_gateway.app as gateway
import intellidhan_gateway.auth as owner_auth
import intellidhan_gateway.discovery as discovery_module
from intellidhan_engine.scoring import composite
from intellidhan_engine.strategies import DailyBreakout, OrbBreakout
from intellidhan_gateway.discovery import (
    DiscoveryService,
    _completed_session_boundary,
    screen_row,
)
from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.terminal_store import TerminalStore
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_learning.paper import PaperExecutor, PaperTrade
from intellidhan_schemas import Bar, SessionState, Timeframe
from intellidhan_schemas.signals import Direction, Module


def daily_bar(index: int, price: float, symbol: str = "TEST") -> Bar:
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
    return Bar(
        symbol=symbol,
        timeframe=Timeframe.D1,
        ts_close=ts,
        open=price - 0.5,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1_000_000 + index * 100,
        source="fixture",
    )


def test_terminal_store_round_trip_and_normalized_workflow(tmp_path):
    store = TerminalStore(tmp_path / "terminal.sqlite3")
    store.init_schema()
    store.seed_universe(
        [
            {
                "symbol": "AAPL",
                "name": "Apple",
                "asset_type": "EQUITY",
                "source": "fixture",
            }
        ]
    )
    assert store.get_security("aapl")["name"] == "Apple"

    store.put_setting("budgets", {"SWING": {"daily_capital": 1000}})
    assert store.get_setting("budgets")["SWING"]["daily_capital"] == 1000

    store.create_watchlist("Research")
    store.add_watchlist_member("research", "aapl", "wait for earnings")
    watchlist = store.list_watchlists()[0]
    assert watchlist["symbols"] == ["AAPL"]
    assert store.watchlists_for_symbol("AAPL") == ["research"]

    store.put_saved_screen("Trend Leaders", {"above_sma200": True})
    assert store.list_saved_screens()[0]["filters"] == {"above_sma200": True}
    assert store.readiness()["connected"] is True


def test_paper_executor_restores_open_and_decided_trades():
    now = datetime.now(timezone.utc)
    pending = PaperTrade(
        alert_id="a1",
        symbol="QQQ",
        module=Module.ZDTE,
        strategy="TEST",
        direction=Direction.LONG,
        confidence=0.8,
        entry=100,
        initial_stop=99,
        targets=[101, 102, 103],
        valid_until=now + timedelta(hours=1),
    )
    executor = PaperExecutor()
    executor.restore([pending])
    assert executor.trades == [pending]
    assert executor._active["QQQ"] == [pending]


def test_owner_session_protects_watchlist_mutations(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", "owner-token-that-is-long-enough-123")
    monkeypatch.setattr(gateway, "rate_limiter", type(gateway.rate_limiter)())
    store = TerminalStore(tmp_path / "api.sqlite3")
    store.init_schema()
    monkeypatch.setattr(gateway.loop, "store", store)
    client = TestClient(gateway.app)

    assert client.get("/api/watchlists").status_code == 401
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/autotrade").status_code == 401
    assert client.get("/api/briefing").status_code == 401
    assert client.post("/api/auth/session", json={"token": "wrong"}).status_code == 401
    response = client.post(
        "/api/auth/session", json={"token": "owner-token-that-is-long-enough-123"}
    )
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()

    assert client.post("/api/watchlists", json={"name": "Research"}).status_code == 200
    assert client.post("/api/watchlists/research/symbols/aapl", json={}).status_code == 200
    assert client.get("/api/watchlists").json()["watchlists"][0]["symbols"] == ["AAPL"]
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/autotrade").status_code == 200
    assert client.get("/api/briefing").status_code == 200


def test_owner_session_cookie_expires_on_the_server(tmp_path, monkeypatch):
    token = "owner-token-that-is-long-enough-123"
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", token)
    monkeypatch.setattr(owner_auth.time, "time", lambda: 1_000_000)
    store = TerminalStore(tmp_path / "expired-session.sqlite3")
    store.init_schema()
    store.create_watchlist("Research")
    monkeypatch.setattr(gateway.loop, "store", store)
    client = TestClient(gateway.app)
    client.cookies.set(owner_auth.OWNER_COOKIE, owner_auth.session_cookie_value())
    assert client.get("/api/watchlists").status_code == 200

    monkeypatch.setattr(
        owner_auth.time,
        "time",
        lambda: 1_000_000 + owner_auth.SESSION_MAX_AGE_SECONDS + 1,
    )
    assert client.get("/api/watchlists").status_code == 401


def test_owner_websocket_closes_when_session_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", "owner-token-that-is-long-enough-123")
    monkeypatch.setattr(owner_auth, "SESSION_MAX_AGE_SECONDS", 1)
    store = TerminalStore(tmp_path / "ws-expiry.sqlite3")
    store.init_schema()
    monkeypatch.setattr(gateway.loop, "store", store)
    client = TestClient(gateway.app)
    client.cookies.set(owner_auth.OWNER_COOKIE, owner_auth.session_cookie_value())

    started = wall_time.monotonic()
    with client.websocket_connect("/ws") as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 4401
    assert wall_time.monotonic() - started <= 2


def test_health_is_503_until_every_readiness_plane_passes(tmp_path, monkeypatch):
    store = TerminalStore(tmp_path / "health.sqlite3")
    store.init_schema()
    monkeypatch.setattr(gateway.loop, "store", store)
    monkeypatch.setattr(gateway.loop, "persistence_ready", True)
    monkeypatch.setattr(gateway.loop, "boot_state", "READY")
    monkeypatch.setattr(gateway.loop, "loop_state", "RUNNING")
    monkeypatch.setattr(gateway.loop, "last_heartbeat", datetime.now(timezone.utc))
    monkeypatch.setattr(gateway.loop, "provider_state", "DEGRADED")
    client = TestClient(gateway.app)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["ok"] is False

    monkeypatch.setattr(gateway.loop, "provider_state", "READY")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True

    monkeypatch.setattr(
        gateway.loop,
        "last_heartbeat",
        datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["heartbeat_age_seconds"] >= 300

    monkeypatch.setattr(gateway.loop, "last_heartbeat", datetime.now(timezone.utc))
    monkeypatch.delenv("INTELLIDHAN_PERSISTENT_STATE", raising=False)
    monkeypatch.setenv("INTELLIDHAN_REQUIRE_DURABLE_STATE", "true")
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["durability_required"] is True
    assert response.json()["durability_ready"] is False


def test_websocket_subscriber_queue_is_bounded_to_newest_event(tmp_path):
    loop = LiveLoop(["QQQ"], store=TerminalStore(tmp_path / "ws.sqlite3"))
    queue = asyncio.Queue(maxsize=1)
    loop.ws_subscribers.append(queue)
    loop.publish_ws({"type": "old"})
    loop.publish_ws({"type": "new"})
    assert queue.qsize() == 1
    assert queue.get_nowait() == {"type": "new"}


@pytest.mark.asyncio
async def test_live_ingest_rejects_out_of_order_bars(tmp_path):
    loop = LiveLoop(["QQQ"], store=TerminalStore(tmp_path / "dq.sqlite3"))
    now = datetime.now(timezone.utc)
    newer = Bar(
        symbol="QQQ",
        timeframe=Timeframe.M5,
        ts_close=now - timedelta(minutes=5),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1000,
        source="fixture",
    )
    older = newer.model_copy(update={"ts_close": now - timedelta(minutes=10)})

    class Provider:
        async def get_bars(self, *args, **kwargs):
            return [newer, older]

    loop.provider = Provider()
    with pytest.raises(RuntimeError, match="data quality rejected"):
        await loop._ingest_recent(days=1)


def test_discovery_is_technical_only_and_future_claims_stay_explicit():
    bars = [daily_bar(index, 100 + index * 0.2) for index in range(300)]
    row = screen_row("TEST", bars)
    assert row["technical_score"] > 50
    assert row["evidence_coverage"] == {
        "available_pillars": 2,
        "total_pillars": 6,
        "label": "TECHNICAL_ONLY",
    }
    assert row["pillars"]["quality"] is None
    assert row["pillars"]["valuation"] is None


def completed_daily_bars(symbol: str, final_session: date, count: int = 300) -> list[Bar]:
    sessions: list[date] = []
    current = final_session
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current -= timedelta(days=1)
    sessions.reverse()
    return [
        Bar(
            symbol=symbol,
            timeframe=Timeframe.D1,
            ts_close=datetime.combine(session, time(16), tzinfo=timezone.utc),
            open=100 + index * 0.1,
            high=101 + index * 0.1,
            low=99 + index * 0.1,
            close=100.5 + index * 0.1,
            volume=1_000_000,
            source="adjusted-fixture",
        )
        for index, session in enumerate(sessions)
    ]


@pytest.mark.asyncio
async def test_discovery_requests_adjusted_settled_daily_history():
    expected_session = date(2026, 7, 10)

    class Provider:
        def __init__(self):
            self.calls = []

        async def get_bars(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return completed_daily_bars("TEST", expected_session)

    provider = Provider()
    service = DiscoveryService(provider=provider)
    end = datetime(2026, 7, 11, 4, tzinfo=timezone.utc)
    row = await service._load_row(
        "TEST", end - timedelta(days=550), end, expected_session
    )
    assert row["as_of"].startswith("2026-07-10")
    assert provider.calls[0][1]["adjusted"] is True
    assert provider.calls[0][0][1] == Timeframe.D1

    clock = MarketClock()
    session, boundary = _completed_session_boundary(
        datetime(2026, 7, 13, 12, tzinfo=timezone.utc), clock
    )
    assert session == expected_session
    assert boundary > datetime(2026, 7, 10, 16, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_partial_discovery_scan_is_explicit_and_not_cached(monkeypatch):
    monkeypatch.setattr(discovery_module, "load_live_symbols", lambda: ["GOOD", "BAD"])
    service = DiscoveryService(provider=SimpleNamespace(), cache_seconds=900)
    calls = []

    async def load(symbol, *_args):
        calls.append(symbol)
        if symbol == "BAD":
            raise LookupError("provider unavailable")
        return {"symbol": symbol, "technical_score": 75.0, "as_of": "2026-07-10"}

    monkeypatch.setattr(service, "_load_row", load)
    first = await service.universe_scan()
    second = await service.universe_scan()
    assert first["complete"] is False
    assert first["configured_count"] == first["attempted_count"] == 2
    assert first["succeeded_count"] == 1
    assert first["failed_count"] == 1
    assert first["errors"] == {"BAD": "provider unavailable"}
    assert second["complete"] is False
    assert calls == ["GOOD", "BAD", "GOOD", "BAD"]


@pytest.mark.asyncio
async def test_explicit_false_filter_overrides_a_preset(monkeypatch):
    service = DiscoveryService(provider=SimpleNamespace())
    below_200 = {
        "symbol": "TEST",
        "as_of": "2026-07-10",
        "price": 100.0,
        "technical_score": 50.0,
        "average_dollar_volume_20d": 10_000_000,
        "distance_from_sma_200_pct": -1.0,
        "return_6m_pct": 10.0,
        "realized_volatility_20d_pct": 20.0,
        "distance_from_52w_high_pct": -5.0,
    }

    async def scan(refresh=False):
        return {
            "rows": [below_200],
            "configured_count": 1,
            "attempted_count": 1,
            "succeeded_count": 1,
            "failed_count": 0,
            "errors": {},
            "complete": True,
            "completed_session": "2026-07-10",
        }

    monkeypatch.setattr(service, "universe_scan", scan)
    result = await service.screen(preset="trend_leaders", above_sma200=False)
    assert result["filters"]["above_sma200"] is False
    assert result["match_count"] == 1


@pytest.mark.asyncio
async def test_briefing_failure_degrades_but_does_not_escape_poll_iteration(tmp_path):
    loop = LiveLoop(["QQQ"], store=TerminalStore(tmp_path / "brief.sqlite3"))
    loop.boot_state = "READY"
    loop.provider_state = "READY"
    loop.persistence_ready = True
    loop.clock = SimpleNamespace(session_state=lambda _now: SessionState.CLOSED)

    async def fail(_now):
        raise RuntimeError("delivery unavailable")

    loop.maybe_brief = fail
    await loop.poll_once(datetime.now(timezone.utc))
    assert loop.loop_state == "DEGRADED"
    assert loop.last_error == "briefing: delivery unavailable"
    assert loop.last_heartbeat is not None


def test_frontend_does_not_override_readiness_with_unconditional_live():
    source = Path("web/index.html").read_text()
    refresh_block = source[source.index("async function refresh()") : source.index(
        "async function refreshCalibration()"
    )]
    assert 'setHealth("live")' not in refresh_block
    assert 'if(!ownerAuthenticated){ setHealth("auth"); return; }' in refresh_block


def test_frontend_clears_personal_state_on_logout_401_and_ws_expiry():
    source = Path("web/index.html").read_text()
    clear_block = source[source.index("function clearPersonalState") : source.index(
        "function renderAll()"
    )]
    assert "lastState=null" in clear_block
    assert "socket.onclose=null; socket.close()" in clear_block
    assert 'document.getElementById("briefBody").replaceChildren()' in clear_block
    assert 'clearPersonalState("Signed out.")' in source
    assert 'r.status===401 && url!=="/api/auth/session"' in source
    assert 'event.code===4401' in source
    assert 'fetchJSON("/api/discover/presets")' in source


def test_missing_factor_weights_are_renormalized_not_rewarded():
    # Only the explicitly available 22% + 18% factors participate.
    assert composite({"F1_trend": 100.0, "F2_setup": 0.0}) == 55.0
    with pytest.raises(ValueError, match="known factor"):
        composite({"F6_unavailable": 100.0})


def test_orb_volume_and_daily_two_close_contracts_are_enforced():
    assert OrbBreakout().min_relvol == 1.5
    strategy = DailyBreakout()
    pivot = SimpleNamespace(price=100.0, kind=SimpleNamespace(value="HIGH"))
    engine = SimpleNamespace(structure=SimpleNamespace(pivots=[pivot]))
    indicators = SimpleNamespace(close=102.0, atr14=2.0, rel_volume=2.0)
    trend = SimpleNamespace(score=50.0, state=SimpleNamespace(value="UP"))

    class State:
        def __init__(self, closes):
            self.recent_daily = [SimpleNamespace(close=close) for close in closes]
            self.trend = {Timeframe.D1: engine}

        def indicators(self, _timeframe):
            return indicators

        def trend_snap(self, _timeframe):
            return trend

    assert strategy.evaluate(State([99.0, 102.0])) is None
    signal = strategy.evaluate(State([101.0, 102.0]))
    assert signal is not None
    assert "Two daily closes" in signal.explain
