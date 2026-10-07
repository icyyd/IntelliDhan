"""Outages must back off, preserve sessions, and stop execution without hiding data loss."""

import asyncio
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from intellidhan_gateway import app as gateway
from intellidhan_gateway import terminal_store as storage
from intellidhan_gateway.auth import RateLimiter, SESSION_COOKIE
from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.terminal_store import StoreUnavailable, TerminalStore


@pytest.mark.parametrize("message,delay,reason", [
    ("Your project has exceeded the active time quota. host=private-host", 1800, "quota"),
    ("password=private-password host=private-host connection refused", 60, "connection"),
])
def test_database_failure_is_bounded_sanitized_and_shared_across_requests(
    monkeypatch, message, delay, reason,
):
    store = TerminalStore("postgresql://redacted.invalid/db")
    connect = MagicMock(side_effect=psycopg.OperationalError(message))
    monkeypatch.setattr(storage.psycopg, "connect", connect)
    monkeypatch.setattr(gateway.loop, "store", store)
    monkeypatch.setattr(gateway.loop, "loop_state", "DEGRADED")
    client = TestClient(gateway.app)
    client.cookies.set(SESSION_COOKIE, "existing-opaque-session")
    for _ in range(4):
        response = client.get("/api/auth/session")
        assert response.status_code == 503
        assert response.json()["code"] == "DATABASE_UNAVAILABLE"
        assert response.json()["reason"] == reason
        assert 1 <= int(response.headers["retry-after"]) <= delay
        assert response.headers["cache-control"] == "no-store"
        assert "private-host" not in response.text
        assert "private-password" not in response.text
        assert "set-cookie" not in response.headers
    assert client.cookies.get(SESSION_COOKIE) == "existing-opaque-session"
    assert connect.call_count == 1
    assert connect.call_args.kwargs["connect_timeout"] == 5
    assert "statement_timeout=5000" in connect.call_args.kwargs["options"]
    assert client.post("/api/autotrade/intents/test/claim", json={}).status_code == 503
    assert client.get("/api/liveness").status_code == 200
    assert connect.call_count == 1  # process probe never consumes DB quota


def test_recovery_probe_reopens_database_after_cooldown(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(storage.time, "monotonic", lambda: clock[0])
    store = TerminalStore("postgresql://redacted.invalid/db")
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = [{"count": 1}]
    context = MagicMock()
    context.__enter__.return_value = connection
    connect = MagicMock(side_effect=[psycopg.OperationalError("offline"), context, context])
    monkeypatch.setattr(storage.psycopg, "connect", connect)
    with pytest.raises(StoreUnavailable):
        store.count_users()
    clock[0] += 59
    with pytest.raises(StoreUnavailable):
        store.count_users()
    assert connect.call_count == 1
    clock[0] += 2
    assert store.count_users() == 1
    assert store.readiness()["connected"] is True
    assert store.retry_after_seconds == 0


def test_runtime_outage_revokes_health_and_symbol_eligibility(monkeypatch):
    store = TerminalStore("postgresql://redacted.invalid/db")
    store.initialized = True
    live = LiveLoop(["SPY"], store=store)
    live.boot_state = "READY"
    live.loop_state = "RUNNING"
    live.provider_state = "READY"
    live.persistence_ready = True
    live.last_heartbeat = datetime.now(timezone.utc)
    live.symbol_health["SPY"]["actionable"] = True
    assert live.health()["ok"] is True
    monkeypatch.setattr(storage.psycopg, "connect", MagicMock(
        side_effect=psycopg.OperationalError("offline"),
    ))
    with pytest.raises(StoreUnavailable):
        store.count_users()
    health = live.health()
    assert health["ok"] is False
    assert health["persistence"]["connected"] is False
    assert health["symbols"]["SPY"]["actionable"] is False
    assert health["actionable_symbols"] == []
    assert live.symbol_block_reason("SPY")


@pytest.mark.asyncio
@pytest.mark.parametrize("browser_recovers_before_poll_returns", [False, True])
async def test_browser_recovery_cannot_skip_engine_reconciliation(
    monkeypatch, tmp_path, browser_recovers_before_poll_returns,
):
    clock = [100.0]
    monkeypatch.setattr(storage.time, "monotonic", lambda: clock[0])
    store = TerminalStore(str(tmp_path / "state.db"))
    store.init_schema()
    live = LiveLoop(["SPY"], store=store)
    live.started_at = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    live.boot_state = "READY"
    live.loop_state = "RUNNING"
    live.provider_state = "READY"
    live.persistence_ready = True
    live.symbol_health["SPY"]["actionable"] = True
    monkeypatch.setattr(live, "maybe_brief", AsyncMock())

    def browser_probe():
        clock[0] += 61
        assert store.count_users() == 0
        assert store.last_error is None

    async def failed_ingest(**_kwargs):
        # A failed persistence step may already have changed in-memory dedup.
        live._alert_ids.add("not-persisted")
        failure = store._record_connection_failure(psycopg.OperationalError("offline"))
        if browser_recovers_before_poll_returns:
            browser_probe()
        raise failure

    monkeypatch.setattr(live, "_ingest_recent", failed_ingest)
    await live.poll_once(live.started_at)
    assert live.persistence_ready is False
    if not browser_recovers_before_poll_returns:
        browser_probe()
    assert store.readiness()["connected"] is True
    assert live.persistence_recovery_required is True
    assert live.health()["persistence_restored"] is False
    assert live.health()["actionable_symbols"] == []
    assert live.symbol_block_reason("SPY")

    # The next supervisor cycle must restore, not just resume another poll.
    boot = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(live, "boot", boot)
    with pytest.raises(asyncio.CancelledError):
        await live.run_forever()
    boot.assert_awaited_once()

    live._restore_operational_state(live.started_at)
    assert "not-persisted" not in live._alert_ids
    assert live.persistence_recovery_required is False
    assert live.persistence_ready is True


def test_websocket_outage_is_retryable_not_session_expiry(monkeypatch):
    store = TerminalStore("postgresql://redacted.invalid/db")
    monkeypatch.setattr(storage.psycopg, "connect", MagicMock(
        side_effect=psycopg.OperationalError("offline"),
    ))
    monkeypatch.setattr(gateway.loop, "store", store)
    client = TestClient(gateway.app)
    client.cookies.set(SESSION_COOKIE, "existing-opaque-session")
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws"):
            pass
    assert closed.value.code == 1013


def test_explicit_logout_clears_cookie_even_during_database_cooldown(monkeypatch):
    store = TerminalStore("postgresql://redacted.invalid/db")
    connect = MagicMock(side_effect=psycopg.OperationalError("active time quota"))
    monkeypatch.setattr(storage.psycopg, "connect", connect)
    monkeypatch.setattr(gateway.loop, "store", store)
    client = TestClient(gateway.app)
    client.cookies.set(SESSION_COOKIE, "session", domain="testserver.local", path="/")
    assert client.get("/api/auth/session").status_code == 503
    response = client.delete("/api/auth/session")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["session_revoked"] is False
    assert SESSION_COOKIE not in client.cookies
    assert connect.call_count == 1


def test_integrity_error_does_not_open_outage_breaker(monkeypatch):
    store = TerminalStore("postgresql://redacted.invalid/db")
    store.initialized = True
    monkeypatch.setattr(storage.psycopg, "connect", MagicMock(
        side_effect=psycopg.IntegrityError("duplicate record"),
    ))
    with pytest.raises(psycopg.IntegrityError):
        store.count_users()
    assert store.last_error is None
    assert store.retry_after_seconds == 0


def test_successful_schema_init_cannot_erase_newer_concurrent_failure(monkeypatch):
    store = TerminalStore("postgresql://redacted.invalid/db")
    schema_started, release_schema = Event(), Event()
    connection = MagicMock()

    def schema_statement(_sql):
        schema_started.set()
        assert release_schema.wait(2)

    connection.execute.side_effect = schema_statement
    context = MagicMock()
    context.__enter__.return_value = connection
    monkeypatch.setattr(storage.psycopg, "connect", MagicMock(return_value=context))
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(store.init_schema)
        try:
            assert schema_started.wait(2)
            store._record_connection_failure(psycopg.OperationalError("offline"))
        finally:
            release_schema.set()
        pending.result(timeout=2)
    assert store.readiness()["connected"] is False
    assert store.readiness()["failure_reason"] == "connection"
    assert store.last_error is not None


def test_threaded_auth_rate_limit_still_admits_only_its_allowance():
    limiter = RateLimiter()

    def attempt(_index):
        try:
            limiter.check("same-login", limit=5, window_seconds=60)
            return True
        except HTTPException as exc:
            assert exc.status_code == 429
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        assert sum(executor.map(attempt, range(40))) == 5
