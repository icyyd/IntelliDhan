"""Simulation fills honor current caps without changing Live risk accounting."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from intellidhan_gateway.autotrade import AutotradeManager, AutomationMode, IntentStatus
from test_autotrade import option_candidate
from test_simulation_admission import research_alert


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.delenv("INTELLIDHAN_LOCAL_ONLY", raising=False)
    return AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")


def select(manager, name="first", *, ask=2.0, buying_power=1000.0):
    intent = manager.on_alert(research_alert(alert_id=name))
    candidate = option_candidate(name, delta=0.6, ask=ask)
    now = datetime.now(timezone.utc)
    candidate["expiration_date"] = now.astimezone(ZoneInfo("America/New_York")).date()
    return manager.attest_option_selection(
        intent.intent_id, agent="codex", buying_power=buying_power,
        observed_at=now, account_scope="ROBINHOOD_AGENTIC_ONLY", candidates=[candidate],
    )


def observation(intent, *, event="ENTRY", ask=2.0):
    return {
        "event": event, "option_id": intent.option_selection["option_id"],
        "reason": "Synthetic test observation, not market evidence",
        "observed_at": datetime.now(timezone.utc),
        "bid_price": ask - 0.05, "ask_price": ask, "underlying_price": 500.1,
        "volume": 1000, "open_interest": 5000,
    }


def test_fresh_ask_cannot_exceed_selected_cap_and_does_not_silently_resize(manager):
    intent = select(manager)
    before = intent.model_dump(mode="json")
    with pytest.raises(ValueError, match="debit exceeds.*select again"):
        manager.record_simulation(intent.intent_id, observation(intent, ask=3.0))
    assert intent.model_dump(mode="json") == before
    assert intent.option_selection["quantity"] == 1
    assert intent.status == IntentStatus.SHADOW


@pytest.mark.parametrize("updates", [
    {"max_dollar_risk_per_order": 150},
    {"max_available_capital_fraction": 0.1},
])
def test_entry_rechecks_current_lowered_order_and_buying_power_caps(manager, updates):
    intent = select(manager)
    manager.update_policy(updates)
    with pytest.raises(ValueError, match="debit exceeds"):
        manager.record_simulation(intent.intent_id, observation(intent))
    assert intent.status == IntentStatus.SHADOW


def test_raising_policy_caps_does_not_expand_previous_selection(manager):
    intent = select(manager)
    manager.update_policy({"max_dollar_risk_per_order": 500})
    with pytest.raises(ValueError, match="debit exceeds"):
        manager.record_simulation(intent.intent_id, observation(intent, ask=3.0))
    assert intent.option_selection["capital_ceiling"] == 250


def test_fresh_selection_cannot_hide_old_buying_power_evidence(manager):
    intent = select(manager)
    intent.option_selection["observed_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=121)
    ).isoformat()
    with pytest.raises(ValueError, match="buying-power observation is stale"):
        manager.record_simulation(intent.intent_id, observation(intent))
    assert intent.status == IntentStatus.SHADOW


def test_pending_plans_do_not_self_count_but_second_open_exposure_is_blocked(manager):
    first = select(manager, "first")
    second = select(manager, "second")
    manager.record_simulation(first.intent_id, observation(first))
    with pytest.raises(ValueError, match="open-position cap"):
        manager.record_simulation(second.intent_id, observation(second))
    with pytest.raises(ValueError, match="open-position cap"):
        select(manager, "third")
    manager.record_simulation(first.intent_id, observation(first, event="EXIT"))
    manager.record_simulation(second.intent_id, observation(second))
    assert first.status == IntentStatus.CLOSED
    assert second.status == IntentStatus.EXECUTED


def test_daily_cap_uses_actual_entry_debit_even_after_exit_and_before_reselection(manager):
    manager.update_policy({"max_daily_dollar_risk": 400, "max_open_intents": 2})
    first = select(manager, "first")
    second = select(manager, "second")
    # $220 still fits the first selected/current $250 ceiling.
    manager.record_simulation(first.intent_id, observation(first, ask=2.2))
    manager.record_simulation(first.intent_id, observation(first, event="EXIT"))
    now = datetime.now(timezone.utc)
    assert manager._remaining_simulation_daily_risk(now) == 180
    assert manager._remaining_daily_risk(now) == 400  # Live capacity is untouched.
    with pytest.raises(ValueError, match="debit exceeds"):
        manager.record_simulation(second.intent_id, observation(second))
    with pytest.raises(ValueError, match="capital"):
        select(manager, "third")


def test_simulation_daily_budget_uses_entry_day_and_ignores_live_exposure(manager):
    first = select(manager, "first")
    manager.record_simulation(first.intent_id, observation(first))
    first.created_at -= timedelta(days=1)
    now = datetime.now(timezone.utc)
    assert manager._remaining_simulation_daily_risk(now) == 300
    first.mode = AutomationMode.LIVE
    assert manager._remaining_simulation_daily_risk(now) == 500
    # A Live exposure does not consume the separate Simulation concurrency slot.
    second = select(manager, "second")
    manager.record_simulation(second.intent_id, observation(second))
    assert second.status == IntentStatus.EXECUTED


def test_nonfinite_entry_ask_is_never_a_simulated_fill(manager):
    intent = select(manager)
    with pytest.raises(ValueError, match="capital evidence"):
        manager.record_simulation(intent.intent_id, observation(intent, ask=float("inf")))
    assert intent.status == IntentStatus.SHADOW


def test_filled_simulation_does_not_consume_actual_live_admission_capacity(manager):
    manager.update_policy({"max_daily_dollar_risk": 250})
    simulated = select(manager)
    manager.record_simulation(simulated.intent_id, observation(simulated))
    assert simulated.status == IntentStatus.EXECUTED
    assert simulated.dollar_risk == 100
    manager.update_policy({"mode": "LIVE", "live_for_minutes": 30})
    candidate = manager.on_alert(research_alert(
        alert_id="separate-live-candidate", research_only=False,
        confidence=0.8, status="ACTIVE", dollar_risk=200,
    ))
    assert candidate.mode == AutomationMode.LIVE
    assert "daily automation risk cap exceeded" not in candidate.reasons
    assert "open-intent cap reached" not in candidate.reasons
    assert candidate.status == IntentStatus.BLOCKED
    assert "no calibration evidence on file" in candidate.reasons
    assert "strategy is not explicitly live eligible" in candidate.reasons


def test_simulation_daily_risk_resets_on_eastern_date_not_utc_midnight(manager):
    intent = select(manager)
    manager.record_simulation(intent.intent_id, observation(intent))
    # 03:59 UTC is still the prior Eastern date in September.
    intent.trade_events[0]["observed_at"] = "2026-09-25T03:59:00+00:00"
    before = datetime(2026, 9, 25, 3, 59, tzinfo=timezone.utc)
    after = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)
    assert manager._remaining_simulation_daily_risk(before) == 300
    assert manager._remaining_simulation_daily_risk(after) == 500


def test_restart_retains_actual_entry_debit_instead_of_original_selection_debit(manager):
    intent = select(manager)
    manager.record_simulation(intent.intent_id, observation(intent, ask=2.2))
    manager.record_simulation(intent.intent_id, observation(intent, event="EXIT"))
    recovered = AutotradeManager(manager.policy_path, manager.state_path)
    restored = recovered.intents[intent.intent_id]
    assert restored.status == IntentStatus.CLOSED
    assert restored.option_selection["planned_debit"] == 200
    assert restored.trade_events[0]["option_price"] == 2.2
    assert recovered._remaining_simulation_daily_risk(datetime.now(timezone.utc)) == 280
    assert recovered._remaining_daily_risk(datetime.now(timezone.utc)) == 500
