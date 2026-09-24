"""Reviewer tests for PR #1: owner auth, terminal store, executor restore.

Store tests run on tmp-path SQLite; endpoint tests monkeypatch the app's
loop.store so nothing touches the repo-local dev database.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from intellidhan_gateway.app import app, loop
from intellidhan_gateway.terminal_store import TerminalStore
from intellidhan_learning.paper import Outcome, PaperExecutor, PaperTrade
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Direction, Module

OWNER = "test-owner-token-0123456789abcdef"  # >= 24 chars


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "store", TerminalStore(tmp_path / "state.sqlite3"))
    monkeypatch.delenv("INTELLIDHAN_OWNER_TOKEN", raising=False)
    # fresh limiter buckets per test: the module-level limiter is keyed by
    # client+surface, and TestClient always presents the same client
    from intellidhan_gateway import app as app_module
    monkeypatch.setattr(app_module, "rate_limiter", type(app_module.rate_limiter)())
    return TestClient(app)


def make_trade(outcome=Outcome.PENDING, alert_id="alr_1", entry=100.0):
    now = datetime.now(timezone.utc)
    return PaperTrade(
        alert_id=alert_id, symbol="QQQ", module=Module.ZDTE, strategy="T",
        direction=Direction.LONG, confidence=0.8, entry=entry, initial_stop=99.0,
        targets=[101.0, 102.0, 103.0], outcome=outcome,
        valid_until=now + timedelta(hours=1),
    )


# ---------- owner auth endpoints ----------

def test_auth_fails_closed_when_owner_token_unset(client):
    assert client.get("/api/auth/session").json() == {
        "configured": False, "authenticated": False}
    assert client.post("/api/auth/session", json={"token": "anything"}).status_code == 503
    assert client.get("/api/watchlists").status_code == 503


def test_owner_login_cookie_flow(client, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", OWNER)
    assert client.post("/api/auth/session", json={"token": "wrong"}).status_code == 401
    assert client.get("/api/watchlists").status_code == 401  # not signed in

    r = client.post("/api/auth/session", json={"token": OWNER})
    assert r.status_code == 200 and r.json()["authenticated"] is True
    assert "intellidhan_owner" in r.cookies
    # the raw owner token must never be stored in the cookie itself
    assert OWNER not in r.cookies["intellidhan_owner"]

    assert client.get("/api/auth/session").json()["authenticated"] is True
    assert client.get("/api/watchlists").status_code == 200

    client.delete("/api/auth/session")
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_bearer_header_also_grants_owner(client, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", OWNER)
    r = client.get("/api/watchlists", headers={"Authorization": f"Bearer {OWNER}"})
    assert r.status_code == 200


def test_login_rate_limits_after_5_attempts(client, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", OWNER)
    for _ in range(5):
        assert client.post("/api/auth/session", json={"token": "bad"}).status_code == 401
    assert client.post("/api/auth/session", json={"token": "bad"}).status_code == 429


def test_watchlist_crud_via_api(client, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", OWNER)
    client.post("/api/auth/session", json={"token": OWNER})
    r = client.post("/api/watchlists", json={"name": "Focus"})
    assert r.status_code == 200
    wl_id = r.json()["watchlist_id"]
    assert client.post(f"/api/watchlists/{wl_id}/symbols/AAPL", json={}).status_code == 200
    lists = client.get("/api/watchlists").json()["watchlists"]
    focus = next(w for w in lists if w["watchlist_id"] == wl_id)
    assert any(m["symbol"] == "AAPL" for m in focus["members"])
    assert client.delete(f"/api/watchlists/{wl_id}/symbols/AAPL").status_code == 200


# ---------- terminal store round-trips ----------

def test_store_settings_alerts_trades_roundtrip(tmp_path):
    store = TerminalStore(tmp_path / "s.sqlite3")
    store.init_schema()
    assert store.backend == "sqlite"

    store.put_setting("budgets", {"0DTE": 1000})
    assert store.get_setting("budgets") == {"0DTE": 1000}
    assert store.get_setting("missing") is None

    alert = {"alert_id": "alr_x", "symbol": "SPY",
             "created_at": datetime.now(timezone.utc).isoformat(), "note": "n"}
    store.upsert_alert(alert)
    store.upsert_alert({**alert, "note": "updated"})  # idempotent upsert
    rows = store.list_alerts()
    assert len(rows) == 1 and rows[0]["note"] == "updated"

    trade = make_trade().model_dump(mode="json")
    store.upsert_paper_trade(trade)
    restored = store.list_paper_trades()
    assert len(restored) == 1
    # full pydantic round-trip fidelity (datetimes/enums)
    assert PaperTrade.model_validate(restored[0]).alert_id == "alr_1"


def test_store_survives_reopen(tmp_path):
    path = tmp_path / "s.sqlite3"
    first = TerminalStore(path)
    first.init_schema()
    first.upsert_alert({"alert_id": "a1", "symbol": "QQQ",
                        "created_at": datetime.now(timezone.utc).isoformat()})
    second = TerminalStore(path)
    second.init_schema()  # idempotent
    assert [r["alert_id"] for r in second.list_alerts()] == ["a1"]


# ---------- executor restore ----------

def test_executor_restore_reactivates_only_undecided_trades():
    # Exercise restoration during a real session, independent of wall-clock
    # test time. After-hours bars correctly follow the unresolved-data path.
    now = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
    done = make_trade(Outcome.STOPPED, alert_id="alr_done")
    done.realized_r = -1.0
    pending = make_trade(Outcome.PENDING, alert_id="alr_pend")
    open_t = make_trade(Outcome.OPEN, alert_id="alr_open")
    open_t.filled_at = now - timedelta(minutes=5)
    pending.valid_until = now + timedelta(hours=1)
    open_t.valid_until = now + timedelta(hours=1)

    ex = PaperExecutor()
    ex.restore([done, pending, open_t])
    assert len(ex.trades) == 3
    assert {t.alert_id for t in ex._active["QQQ"]} == {"alr_pend", "alr_open"}

    # a restored OPEN trade still settles per its plan
    stop_bar = Bar(symbol="QQQ", timeframe=Timeframe.M5, ts_close=now, open=99.5,
                   high=99.6, low=98.5, close=98.8, volume=1e6, source="fx")
    settled = ex.on_bar(stop_bar)
    settled_ids = {t.alert_id for t in settled}
    assert "alr_open" in settled_ids
    assert open_t.outcome == Outcome.STOPPED
    assert "alr_done" not in settled_ids  # settled trades never double-settle
