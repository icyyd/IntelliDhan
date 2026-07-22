"""PostgreSQL account-path verification run explicitly in CI."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from intellidhan_gateway.autotrade import AutomationMode, AutotradeManager
from intellidhan_gateway.terminal_store import TerminalStore


pytestmark = pytest.mark.integration


def _clean_accounts(store: TerminalStore) -> None:
    with store._connection() as connection:
        connection.execute(
            """TRUNCATE user_watchlist_members, user_watchlists,
                        user_saved_screens, user_capital_limits,
                        user_preferences, user_sessions, users CASCADE"""
        )


def _clean_autotrade_settings(store: TerminalStore) -> None:
    with store._connection() as connection:
        connection.execute("DELETE FROM autotrade_intent_events")
        connection.execute(
            "DELETE FROM runtime_settings WHERE setting_key IN (%s, %s)",
            ("autotrade_policy", "autotrade_intents"),
        )


def test_postgres_pre_codex_policy_is_disarmed_and_versioned():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    store = TerminalStore(url)
    store.init_schema()
    _clean_autotrade_settings(store)
    try:
        store.put_setting(
            "autotrade_policy",
            {
                "mode": "ARMED",
                "armed_until": "2099-01-01T00:00:00Z",
                "agent": "codex",
                "revision": 11,
            },
        )
        manager = AutotradeManager(state_store=store)
        assert manager.policy.mode == AutomationMode.SIMULATION
        assert manager.policy.contract_version == "2.0"
        assert manager.policy.revision == 12
        persisted = store.get_setting("autotrade_policy")
        assert persisted["mode"] == "SIMULATION"
        assert persisted["contract_version"] == "2.0"
        now = datetime.now(timezone.utc).isoformat()
        first = {
            "event_id": "postgres-event-1", "seq": 1, "at": now,
            "event": "TEST", "from_status": None, "to_status": "SHADOW",
            "detail": None,
        }
        second = {**first, "event_id": "postgres-event-2", "seq": 2}
        store.append_autotrade_event("intent-postgres", first)
        store.append_autotrade_event("intent-postgres", first)
        store.append_autotrade_event("intent-postgres", second)
        assert [item["event_id"] for item in store.list_autotrade_events(
            "intent-postgres"
        )] == ["postgres-event-1", "postgres-event-2"]
    finally:
        _clean_autotrade_settings(store)


def test_postgres_account_transactions_locking_and_personal_state():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    store = TerminalStore(url)
    store.init_schema()
    _clean_accounts(store)
    try:
        with pytest.raises(TypeError):
            store.create_user(
                email="rollback@example.com",
                display_name="Rollback Admin",
                password_hash="test-only-hash",
                role="ADMIN",
                preferences={"theme": "dark"},
                capital_limits={"SWING": {"invalid": object()}},
                default_watchlist="Research",
                require_first=True,
            )
        assert store.count_users() == 0

        ready = Barrier(2)

        def bootstrap(index: int):
            ready.wait()
            try:
                return store.create_user(
                    email=f"postgres-admin{index}@example.com",
                    display_name=f"Postgres Admin {index}",
                    password_hash="test-only-hash",
                    role="ADMIN",
                    preferences={"theme": "dark"},
                    capital_limits={
                        "SWING": {"daily_capital": 10_000, "risk_cap_pct": 0.1}
                    },
                    default_watchlist="Research",
                    require_first=True,
                )
            except ValueError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(bootstrap, (1, 2)))

        assert store.count_users() == 1
        users = [outcome for outcome in outcomes if isinstance(outcome, dict)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, str)]
        assert len(users) == 1
        assert failures == ["the initial administrator already exists"]
        user_id = users[0]["user_id"]

        preferences = {
            "theme": "system",
            "default_view": "analysis",
            "compact_cards": True,
            "alert_sound": False,
            "reduced_motion": True,
        }
        limits = {"SWING": {"daily_capital": 25_000, "risk_cap_pct": 0.08}}
        store.put_user_preferences(user_id, preferences)
        store.put_user_capital_limits(user_id, limits)
        ideas = store.create_user_watchlist(user_id, "Momentum ideas")
        store.add_user_watchlist_member(user_id, ideas["watchlist_id"], "NVDA")
        store.put_user_saved_screen(user_id, "Liquid leaders", {"min_price": 25})
        store.create_user_session(
            session_hash="postgres-test-session-hash",
            user_id=user_id,
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )

        reopened = TerminalStore(url)
        reopened.init_schema()
        assert reopened.get_user_session("postgres-test-session-hash")["user_id"] == user_id
        assert reopened.get_user_preferences(user_id) == preferences
        assert reopened.get_user_capital_limits(user_id)["SWING"] == limits["SWING"]
        watchlists = reopened.list_user_watchlists(user_id)
        assert {item["name"] for item in watchlists} == {"Research", "Momentum ideas"}
        assert next(
            item for item in watchlists if item["name"] == "Momentum ideas"
        )["symbols"] == ["NVDA"]
        assert reopened.list_user_saved_screens(user_id)[0]["filters"] == {
            "min_price": 25
        }
    finally:
        _clean_accounts(store)
