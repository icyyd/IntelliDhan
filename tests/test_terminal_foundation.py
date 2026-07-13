"""Trustworthy-terminal foundation: durability, auth, DQ, discovery, parity."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import intellidhan_gateway.app as gateway
from intellidhan_engine.scoring import composite
from intellidhan_engine.strategies import DailyBreakout, OrbBreakout
from intellidhan_gateway.discovery import screen_row
from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.terminal_store import TerminalStore
from intellidhan_learning.paper import PaperExecutor, PaperTrade
from intellidhan_schemas import Bar, Timeframe
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
    assert client.post("/api/auth/session", json={"token": "wrong"}).status_code == 401
    response = client.post(
        "/api/auth/session", json={"token": "owner-token-that-is-long-enough-123"}
    )
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()

    assert client.post("/api/watchlists", json={"name": "Research"}).status_code == 200
    assert client.post("/api/watchlists/research/symbols/aapl", json={}).status_code == 200
    assert client.get("/api/watchlists").json()["watchlists"][0]["symbols"] == ["AAPL"]


def test_health_is_503_until_every_readiness_plane_passes(tmp_path, monkeypatch):
    store = TerminalStore(tmp_path / "health.sqlite3")
    store.init_schema()
    monkeypatch.setattr(gateway.loop, "store", store)
    monkeypatch.setattr(gateway.loop, "persistence_ready", True)
    monkeypatch.setattr(gateway.loop, "boot_state", "READY")
    monkeypatch.setattr(gateway.loop, "provider_state", "DEGRADED")
    client = TestClient(gateway.app)
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["ok"] is False

    monkeypatch.setattr(gateway.loop, "provider_state", "READY")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


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
