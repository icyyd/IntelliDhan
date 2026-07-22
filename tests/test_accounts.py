"""Account security, durable preferences, and per-user isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from intellidhan_gateway import app as gateway
from intellidhan_gateway.auth import (
    SESSION_COOKIE,
    hash_password,
    session_token_hash,
    verify_password,
)
from intellidhan_gateway.autotrade import AutotradeManager
from intellidhan_gateway.terminal_store import TerminalStore


ADMIN_CODE = "admin-setup-code-that-is-long-enough"
INVITE_CODE = "invite-code-long-enough"


@pytest.fixture
def account_store(tmp_path, monkeypatch):
    store = TerminalStore(tmp_path / "accounts.sqlite3")
    store.init_schema()
    monkeypatch.setattr(gateway.loop, "store", store)
    monkeypatch.setattr(gateway, "rate_limiter", type(gateway.rate_limiter)())
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", ADMIN_CODE)
    monkeypatch.setenv("INTELLIDHAN_INVITE_CODE", INVITE_CODE)
    return store


@pytest.fixture
def client(account_store):
    return TestClient(gateway.app)


def register(client: TestClient, *, email: str = "admin@example.com"):
    return client.post(
        "/api/auth/register",
        json={
            "display_name": "Ada Admin",
            "email": email,
            "password": "a long test password 123",
            "invite_code": ADMIN_CODE,
        },
    )


def test_password_hash_is_salted_and_verified():
    first = hash_password("a long test password 123")
    second = hash_password("a long test password 123")
    assert first != second
    assert verify_password("a long test password 123", first)
    assert not verify_password("not the correct password", first)
    assert not verify_password("anything", "malformed")
    assert not verify_password("anything", "scrypt$999999$8$1$bad$bad")


def test_first_account_is_admin_and_session_token_is_not_stored(
    client, account_store
):
    response = register(client)
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "ADMIN"
    assert SESSION_COOKIE in response.cookies
    raw_token = response.cookies[SESSION_COOKIE]

    rows = account_store._fetchall("SELECT session_hash FROM user_sessions")
    assert rows == [{"session_hash": session_token_hash(raw_token)}]
    assert raw_token not in rows[0]["session_hash"]

    stored = account_store.get_user_by_email(
        "ADMIN@example.com", include_password=True
    )
    assert stored and verify_password("a long test password 123", stored["password_hash"])
    assert stored["password_hash"] != "a long test password 123"
    assert client.get("/api/auth/session").json()["user"]["email"] == "admin@example.com"


def test_registration_requires_invite_and_validates_password(client):
    bad_invite = client.post(
        "/api/auth/register",
        json={
            "display_name": "Ada",
            "email": "ada@example.com",
            "password": "a long test password 123",
            "invite_code": INVITE_CODE,
        },
    )
    assert bad_invite.status_code == 401

    short_password = client.post(
        "/api/auth/register",
        json={
            "display_name": "Ada",
            "email": "ada@example.com",
            "password": "short",
            "invite_code": ADMIN_CODE,
        },
    )
    assert short_password.status_code == 422


def test_invite_creates_trader_only_after_initial_admin(client, monkeypatch):
    assert register(client).status_code == 200
    trader = TestClient(gateway.app)
    invited = trader.post(
        "/api/auth/register",
        json={
            "display_name": "Terry Trader",
            "email": "trader@example.com",
            "password": "a trader password 789",
            "invite_code": INVITE_CODE,
        },
    )
    assert invited.status_code == 200
    assert invited.json()["user"]["role"] == "TRADER"
    assert trader.put("/api/autotrade/policy", json={"mode": "SIMULATION"}).status_code == 403

    monkeypatch.setattr(
        gateway.loop.autotrade,
        "audit_log",
        lambda _limit: [{"broker_order_id": "sensitive-global-order"}],
    )
    trader_log = trader.get("/api/trade-log")
    assert trader_log.status_code == 200
    assert trader_log.json()["execution_events"] == []
    assert trader_log.json()["broker_history_visible"] is False

    admin_log = client.get("/api/trade-log")
    assert admin_log.status_code == 200
    assert admin_log.json()["execution_events"] == [
        {"broker_order_id": "sensitive-global-order"}
    ]
    assert admin_log.json()["broker_history_visible"] is True


def test_durable_admin_can_update_shared_automation_policy(
    client, tmp_path, monkeypatch
):
    assert register(client).status_code == 200
    isolated = AutotradeManager(
        tmp_path / "autotrade-policy.yaml", tmp_path / "autotrade-state.json"
    )
    monkeypatch.setattr(gateway.loop, "autotrade", isolated)
    response = client.put("/api/autotrade/policy", json={"mode": "SIMULATION"})
    assert response.status_code == 200
    assert response.json()["mode"] == "SIMULATION"


def test_registration_availability_uses_validated_invite(client, monkeypatch):
    assert register(client).status_code == 200
    monkeypatch.setenv("INTELLIDHAN_INVITE_CODE", "too-short")
    assert client.get("/api/auth/session").json()["registration_enabled"] is False
    attempted = TestClient(gateway.app).post(
        "/api/auth/register",
        json={
            "display_name": "Terry Trader",
            "email": "trader@example.com",
            "password": "a trader password 789",
            "invite_code": "too-short",
        },
    )
    assert attempted.status_code == 503


def test_login_logout_revokes_database_session(client, account_store):
    assert register(client).status_code == 200
    assert client.delete("/api/auth/session").status_code == 200
    assert client.get("/api/state").status_code == 401
    assert account_store._fetchall(
        "SELECT revoked_at FROM user_sessions WHERE revoked_at IS NOT NULL"
    )

    login = client.post(
        "/api/auth/session",
        json={"email": "admin@example.com", "password": "a long test password 123"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["display_name"] == "Ada Admin"
    wrong = TestClient(gateway.app).post(
        "/api/auth/session",
        json={"email": "admin@example.com", "password": "wrong password"},
    )
    assert wrong.status_code == 401


def test_login_modes_clear_and_revoke_the_other_cookie(client, account_store):
    assert register(client).status_code == 200
    assert client.delete("/api/auth/session").status_code == 200

    assert client.post("/api/auth/session", json={"token": ADMIN_CODE}).status_code == 200
    assert client.cookies.get("intellidhan_owner")
    account_login = client.post(
        "/api/auth/session",
        json={"email": "admin@example.com", "password": "a long test password 123"},
    )
    assert account_login.status_code == 200
    assert client.cookies.get(SESSION_COOKIE)
    assert client.cookies.get("intellidhan_owner") is None

    legacy_login = client.post("/api/auth/session", json={"token": ADMIN_CODE})
    assert legacy_login.status_code == 200
    assert client.cookies.get(SESSION_COOKIE) is None
    assert client.get("/api/auth/session").json()["user"]["legacy"] is True
    assert account_store._fetchall(
        "SELECT revoked_at FROM user_sessions WHERE revoked_at IS NOT NULL"
    )


def test_expired_database_session_is_rejected(client, account_store):
    assert register(client).status_code == 200
    raw_token = client.cookies.get(SESSION_COOKIE)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    account_store._execute(
        "UPDATE user_sessions SET expires_at=? WHERE session_hash=?",
        (expired, session_token_hash(raw_token)),
    )
    assert client.get("/api/state").status_code == 401
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws"):
            pass
    assert closed.value.code == 4401


def test_database_session_authenticates_websocket(client):
    assert register(client).status_code == 200
    with client.websocket_connect("/ws"):
        pass


def test_live_websocket_closes_when_database_session_is_revoked(
    client, account_store, monkeypatch
):
    assert register(client).status_code == 200
    raw_token = client.cookies.get(SESSION_COOKIE)
    monkeypatch.setattr(gateway, "WS_SESSION_RECHECK_SECONDS", 0.01)
    with client.websocket_connect("/ws") as socket:
        account_store.revoke_user_session(session_token_hash(raw_token))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 4401


def test_account_creation_rolls_back_all_defaults_and_can_be_retried(account_store):
    account_store._execute(
        """CREATE TRIGGER fail_capital_seed
           BEFORE INSERT ON user_capital_limits
           BEGIN SELECT RAISE(ABORT, 'seed failure'); END"""
    )
    account = {
        "email": "atomic@example.com",
        "display_name": "Atomic Admin",
        "password_hash": hash_password("an atomic password 123"),
        "role": "ADMIN",
        "preferences": {"theme": "dark"},
        "capital_limits": {
            "SWING": {"daily_capital": 10_000, "risk_cap_pct": 0.1}
        },
        "default_watchlist": "Research",
        "require_first": True,
    }
    with pytest.raises(RuntimeError, match="defaults could not be initialized"):
        account_store.create_user(**account)
    assert account_store.count_users() == 0
    assert account_store._fetchall("SELECT * FROM user_preferences") == []
    assert account_store._fetchall("SELECT * FROM user_capital_limits") == []
    assert account_store._fetchall("SELECT * FROM user_watchlists") == []

    account_store._execute("DROP TRIGGER fail_capital_seed")
    created = account_store.create_user(**account)
    assert created["role"] == "ADMIN"
    assert account_store.get_user_preferences(created["user_id"]) == {"theme": "dark"}
    assert account_store.get_user_capital_limits(created["user_id"])["SWING"] == {
        "daily_capital": 10_000,
        "risk_cap_pct": 0.1,
    }
    assert account_store.list_user_watchlists(created["user_id"])[0]["name"] == "Research"
    with pytest.raises(ValueError, match="already exists"):
        account_store.create_user(
            **{**account, "email": "second@example.com"}
        )


def test_concurrent_bootstrap_allows_only_one_initial_admin(account_store):
    ready = Barrier(2)

    def bootstrap(index: int):
        ready.wait()
        try:
            return account_store.create_user(
                email=f"admin{index}@example.com",
                display_name=f"Admin {index}",
                password_hash="test-only-hash",
                role="ADMIN",
                preferences={"theme": "dark"},
                capital_limits={},
                default_watchlist="Research",
                require_first=True,
            )
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(bootstrap, (1, 2)))

    assert account_store.count_users() == 1
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert sum("initial administrator already exists" in outcome for outcome in outcomes if isinstance(outcome, str)) == 1


def test_preferences_and_capital_limits_survive_reopen(client, account_store):
    assert register(client).status_code == 200
    preferences = {
        "theme": "system",
        "default_view": "discover",
        "compact_cards": True,
        "alert_sound": True,
        "reduced_motion": True,
    }
    assert client.put("/api/account/preferences", json=preferences).json() == preferences

    limits = client.get("/api/budgets").json()
    limits["SWING"]["daily_capital"] = 23_000
    limits["SWING"]["risk_cap_pct"] = 0.12
    assert client.put("/api/budgets", json=limits).status_code == 200

    reopened = TerminalStore(account_store.location)
    reopened.init_schema()
    user = reopened.get_user_by_email("admin@example.com")
    assert reopened.get_user_preferences(user["user_id"]) == preferences
    assert reopened.get_user_capital_limits(user["user_id"])["SWING"] == {
        "daily_capital": 23_000.0,
        "risk_cap_pct": 0.12,
    }


def test_accounts_are_isolated_and_admin_can_create_users(client, account_store):
    assert register(client).status_code == 200
    created = client.post(
        "/api/accounts",
        json={
            "display_name": "Vera Viewer",
            "email": "viewer@example.com",
            "password": "another long password 456",
            "role": "VIEWER",
        },
    )
    assert created.status_code == 200
    assert created.json()["role"] == "VIEWER"

    viewer = TestClient(gateway.app)
    assert viewer.post(
        "/api/auth/session",
        json={"email": "viewer@example.com", "password": "another long password 456"},
    ).status_code == 200
    assert viewer.put(
        "/api/account/preferences", json={"default_view": "analysis"}
    ).status_code == 200
    assert viewer.post("/api/watchlists", json={"name": "Viewer ideas"}).status_code == 200
    assert viewer.post(
        "/api/screens", json={"name": "Viewer screen", "filters": {"min_price": 25}}
    ).status_code == 200
    viewer_limits = viewer.get("/api/budgets").json()
    viewer_limits["0DTE"]["daily_capital"] = 777
    assert viewer.put("/api/budgets", json=viewer_limits).status_code == 200

    admin_lists = client.get("/api/watchlists").json()["watchlists"]
    viewer_lists = viewer.get("/api/watchlists").json()["watchlists"]
    assert {item["name"] for item in admin_lists} == {"Research"}
    assert {item["name"] for item in viewer_lists} == {"Research", "Viewer ideas"}
    assert client.get("/api/screens").json()["screens"] == []
    assert [screen["name"] for screen in viewer.get("/api/screens").json()["screens"]] == [
        "Viewer screen"
    ]
    assert client.get("/api/account/preferences").json()["default_view"] == "signals"
    assert client.get("/api/budgets").json()["0DTE"]["daily_capital"] != 777

    assert viewer.post("/api/accounts", json={}).status_code == 403
    assert viewer.put("/api/autotrade/policy", json={"mode": "SIMULATION"}).status_code == 403


def test_capital_limit_validation_does_not_mutate_shared_engine(client):
    assert register(client).status_code == 200
    shared_before = gateway.loop.composer.budgets.as_dict()
    bad = client.put(
        "/api/budgets",
        json={"SWING": {"daily_capital": -1, "risk_cap_pct": 0.2}},
    )
    assert bad.status_code == 422
    assert gateway.loop.composer.budgets.as_dict() == shared_before
