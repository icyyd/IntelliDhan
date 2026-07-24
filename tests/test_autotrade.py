"""Guardrails and lifecycle tests for the Codex→Robinhood MCP intent bridge."""

import json

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from intellidhan_engine.calibration import CalibrationMap
from intellidhan_gateway.autotrade import AutomationMode, AutotradeManager, IntentStatus
from intellidhan_gateway.terminal_store import TerminalStore
from intellidhan_schemas.signals import (
    Action,
    Alert,
    Module,
    TakeProfit,
    Vehicle,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_alert(**overrides) -> Alert:
    now = datetime.now(timezone.utc)
    data = {
        "alert_id": "alr_test_spy_1",
        "created_at": now,
        "module": Module.SWING,
        "strategy": "TEST_STRATEGY",
        "action": Action.EQUITY_BUY,
        "symbol": "SPY",
        "underlying_price": 500.0,
        "vehicle": Vehicle.EQUITY,
        "legs": [],
        "equity_qty": 10,
        "entry_limit": 500.0,
        "entry_zone": (499.5, 500.5),
        "stop_underlying": 490.0,
        "stop_est_vehicle": 490.0,
        "stop_rule": "close below 490",
        "take_profits": [
            TakeProfit(
                zone_low=None,
                zone_high=None,
                underlying=520.0,
                tranche=1.0,
                basis="target 1",
            )
        ],
        "contracts": None,
        "capital_required": 5000.0,
        "dollar_risk": 100.0,
        "reward_risk": 2.0,
        "budget_note": "test",
        "confidence": 0.80,
        "factors": {},
        "trend_matrix": {"D": "UP"},
        "thesis": "test thesis",
        "invalidation": "close below 490",
        "management": ["protect immediately"],
        "risks": [],
        "valid_until": now + timedelta(hours=1),
    }
    data.update(overrides)
    return Alert(**data)


@pytest.fixture
def calibrated(monkeypatch):
    def load(_cls, strategy):
        return CalibrationMap(
            strategy,
            {"0-100": {"n": 100, "wr": 0.80, "sufficient": True}},
            {"live_eligible": True, "evidence_status": "FORWARD_PAPER"},
        )

    monkeypatch.setattr(CalibrationMap, "load", classmethod(load))


def live_policy(mode="LIVE"):
    payload = {
        "mode": mode,
        "allowed_symbols": ["SPY"],
        "allowed_strategies": ["TEST_STRATEGY"],
        "allowed_modules": ["SWING"],
        "min_confidence": 0.75,
        "max_dollar_risk_per_order": 150,
        "max_daily_dollar_risk": 300,
    }
    if mode == "LIVE":
        payload["live_for_minutes"] = 30
    return payload


def pass_capital_review(manager, intent, *, buying_power=10_000.0):
    return manager.attest_capital(
        intent.intent_id,
        agent="codex",
        buying_power=buying_power,
        observed_at=datetime.now(timezone.utc),
        currency="USD",
        account_scope="ROBINHOOD_AGENTIC_ONLY",
    )


def claim_intent(manager, intent, *, agent="codex", underlying_price=500.0):
    return manager.claim(
        intent.intent_id,
        agent,
        underlying_price=underlying_price,
        observed_at=datetime.now(timezone.utc),
    )


def broker_receipt(status, *, price=500.0, quantity=10, **overrides):
    payload = {
        "status": status,
        "reason": f"test {status.lower()} receipt",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    if status in {"EXECUTED", "CLOSED"}:
        payload.update({
            "broker_order_id": f"rh-{status.lower()}",
            "average_price": price,
            "filled_quantity": quantity,
            "pretrade_alerts": [],
        })
    payload.update(overrides)
    return payload


def option_candidate(
    option_id: str,
    *,
    delta: float,
    ask: float,
    bid: float | None = None,
    expiry_days: int = 0,
    option_type: str = "call",
    volume: int = 1_000,
    open_interest: int = 5_000,
) -> dict:
    now = datetime.now(timezone.utc)
    bid = ask - 0.05 if bid is None else bid
    return {
        "option_id": option_id,
        "chain_symbol": "SPY",
        "expiration_date": (now.date() + timedelta(days=expiry_days)).isoformat(),
        "option_type": option_type,
        "strike_price": 500.0,
        "delta": delta,
        "bid_price": bid,
        "ask_price": ask,
        "mark_price": (bid + ask) / 2,
        "volume": volume,
        "open_interest": open_interest,
        "quote_at": now.isoformat(),
        "sellout_at": (now + timedelta(hours=2)).isoformat(),
        "tradable": True,
    }


def test_simulation_mode_creates_non_executable_intent(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy("SIMULATION"))
    intent = manager.on_alert(make_alert())
    assert intent.status == IntentStatus.SHADOW
    assert manager.status()["effective_mode"] == "SIMULATION"


def test_simulation_intent_blocks_when_signal_is_cancelled(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy("SIMULATION"))
    alert = make_alert(alert_id="alr_sim_cancel")
    manager.on_alert(alert)

    cancelled = manager.on_alert(alert.model_copy(update={"status": "CANCELLED"}))

    assert cancelled.status == IntentStatus.BLOCKED
    assert "alert status is cancelled" in cancelled.reasons


def test_live_mode_is_allowlisted_risk_capped_and_idempotent(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    alert = make_alert()
    intent = manager.on_alert(alert)
    assert intent.status == IntentStatus.READY
    assert intent.order_plan["account_scope"] == "ROBINHOOD_AGENTIC_ONLY"
    assert intent.order_plan["protection"]["must_be_established"] is True
    assert intent.order_plan["capital_policy"] == {
        "max_available_capital_fraction": 0.8,
        "capital_required": 5000.0,
        "fresh_buying_power_required": True,
        "use_maximum_within_all_caps": False,
        "premium_at_risk_counts_as_dollar_risk": False,
    }
    assert "80%" in intent.order_plan["abort_if"][-1]
    assert manager.on_alert(alert).intent_id == intent.intent_id
    assert len(manager.intents) == 1


def test_dynamic_option_selection_prefers_highest_feasible_delta_and_max_size(
    tmp_path, calibrated
):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy({
        "mode": "SIMULATION",
        "allowed_symbols": ["SPY", "QQQ"],
        "allowed_strategies": ["EMA9_MTF_0DTE"],
        "allowed_modules": ["0DTE"],
        "allow_options": True,
    })
    alert = make_alert(
        module=Module.ZDTE,
        strategy="EMA9_MTF_0DTE",
        status="SHADOW",
        research_only=True,
        capital_required=0,
    )
    intent = manager.on_alert(alert)
    assert intent.status == IntentStatus.SHADOW
    assert intent.order_plan["instrument"]["type"] == "OPTION_SELECTION_REQUIRED"

    selected = manager.attest_option_selection(
        intent.intent_id,
        agent="codex",
        buying_power=300,
        observed_at=datetime.now(timezone.utc),
        account_scope="ROBINHOOD_AGENTIC_ONLY",
        candidates=[
            option_candidate("too-expensive", delta=0.90, ask=2.50),
            option_candidate("highest-feasible", delta=0.70, ask=2.20),
            option_candidate("more-contracts-lower-delta", delta=0.50, ask=1.10),
            option_candidate("wide-market", delta=0.80, ask=2.00, bid=1.50),
        ],
    )

    assert selected.option_selection["option_id"] == "highest-feasible"
    assert selected.option_selection["quantity"] == 1
    assert selected.option_selection["capital_ceiling"] == 240.0
    assert selected.option_selection["planned_debit"] == 220.0
    assert selected.order_plan["capital_policy"]["capital_required"] == 220.0
    assert selected.order_plan["protection"]["exit_style"] == "TREND_BREAK_FULL_EXIT"

    refreshed = manager.attest_option_selection(
        intent.intent_id,
        agent="codex",
        buying_power=300,
        observed_at=datetime.now(timezone.utc),
        account_scope="ROBINHOOD_AGENTIC_ONLY",
        candidates=[option_candidate("fresh-selection", delta=0.75, ask=2.00)],
    )
    assert refreshed.option_selection["option_id"] == "fresh-selection"
    assert refreshed.order_plan["instrument"]["option_id"] == "fresh-selection"


def test_bearish_ema9_signal_selects_a_long_put_and_can_be_claimed(
    tmp_path, calibrated
):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy({
        "mode": "LIVE",
        "live_for_minutes": 30,
        "allowed_symbols": ["SPY"],
        "allowed_strategies": ["EMA9_MTF_0DTE"],
        "allowed_modules": ["0DTE"],
        "max_dollar_risk_per_order": 250,
        "max_daily_dollar_risk": 500,
    })
    intent = manager.on_alert(make_alert(
        module=Module.ZDTE,
        strategy="EMA9_MTF_0DTE",
        action=Action.EQUITY_SELL,
        stop_underlying=510.0,
        capital_required=0,
    ))

    assert intent.status == IntentStatus.READY
    selected = manager.attest_option_selection(
        intent.intent_id,
        agent="codex",
        buying_power=300,
        observed_at=datetime.now(timezone.utc),
        account_scope="ROBINHOOD_AGENTIC_ONLY",
        candidates=[
            option_candidate(
                "put-contract", delta=-0.70, ask=2.00, option_type="put"
            )
        ],
    )
    assert selected.order_plan["instrument"]["option_type"] == "put"
    assert selected.order_plan["entry"]["entry_zone"] == [499.5, 500.5]
    assert selected.option_selection["sellout_at"]
    pass_capital_review(manager, selected, buying_power=300)
    assert claim_intent(manager, selected).status == IntentStatus.CLAIMED
    entered = manager.record_receipt(
        selected.intent_id,
        broker_receipt(
            "EXECUTED", price=2.00, quantity=1, option_id="put-contract"
        ),
    )
    closed = manager.record_receipt(
        selected.intent_id,
        broker_receipt("CLOSED", price=2.50, quantity=1, option_id="put-contract"),
    )
    assert entered.trade_events[0]["option_price"] == 2.0
    assert closed.trade_events[-1]["realized_pnl"] == 50.0


def test_simulation_records_real_quote_entry_exit_and_reasoning(tmp_path, calibrated):
    health = {"reason": None}
    manager = AutotradeManager(
        tmp_path / "policy.yaml",
        tmp_path / "state.json",
        symbol_gate=lambda _symbol: health["reason"],
    )
    manager.update_policy({
        "mode": "SIMULATION",
        "allowed_symbols": ["SPY"],
        "allowed_strategies": ["EMA9_MTF_0DTE"],
        "allowed_modules": ["0DTE"],
        "allow_options": True,
    })
    intent = manager.on_alert(make_alert(
        module=Module.ZDTE,
        strategy="EMA9_MTF_0DTE",
        status="SHADOW",
        research_only=True,
        capital_required=0,
    ))
    manager.attest_option_selection(
        intent.intent_id,
        agent="codex",
        buying_power=300,
        observed_at=datetime.now(timezone.utc),
        account_scope="ROBINHOOD_AGENTIC_ONLY",
        candidates=[option_candidate("sim-contract", delta=0.65, ask=2.00)],
    )
    entered = manager.record_simulation(intent.intent_id, {
        "event": "ENTRY",
        "option_id": "sim-contract",
        "reason": "5-minute 9EMA reclaim with 15-minute trend aligned",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "bid_price": 1.95,
        "ask_price": 2.00,
        "underlying_price": 500.10,
        "volume": 1_000,
        "open_interest": 5_000,
    })
    assert entered.status == IntentStatus.EXECUTED
    health["reason"] = "SPY underlying feed is quarantined"
    exited = manager.record_simulation(intent.intent_id, {
        "event": "EXIT",
        "option_id": "sim-contract",
        "reason": "closed 5-minute candle broke the 9EMA trend",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "bid_price": 2.40,
        "ask_price": 2.45,
        "underlying_price": 501.25,
        "volume": 1_200,
        "open_interest": 5_100,
    })
    assert exited.status == IntentStatus.CLOSED
    assert [row["event"] for row in exited.trade_events] == ["ENTRY", "EXIT"]
    assert exited.trade_events[-1]["realized_pnl"] == 40.0
    assert "9EMA" in exited.trade_events[-1]["reason"]
    assert "quarantined" in exited.trade_events[-1]["data_quality_reason"]


def test_simulation_rejects_stale_or_illiquid_entry_observations(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy({
        "mode": "SIMULATION",
        "allowed_symbols": ["SPY"],
        "allowed_strategies": ["EMA9_MTF_0DTE"],
        "allowed_modules": ["0DTE"],
    })
    intent = manager.on_alert(make_alert(
        module=Module.ZDTE,
        strategy="EMA9_MTF_0DTE",
        status="SHADOW",
        research_only=True,
        capital_required=0,
    ))
    manager.attest_option_selection(
        intent.intent_id,
        agent="codex",
        buying_power=300,
        observed_at=datetime.now(timezone.utc),
        account_scope="ROBINHOOD_AGENTIC_ONLY",
        candidates=[option_candidate("sim-stale", delta=0.65, ask=2.00)],
    )
    base = {
        "event": "ENTRY",
        "option_id": "sim-stale",
        "reason": "test quote",
        "bid_price": 1.95,
        "ask_price": 2.00,
        "underlying_price": 500.0,
        "volume": 1_000,
        "open_interest": 5_000,
    }
    with pytest.raises(ValueError, match="does not match selection"):
        manager.record_simulation(intent.intent_id, {
            **base,
            "option_id": "different-contract",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })
    with pytest.raises(ValueError, match="simulation quote is stale"):
        manager.record_simulation(intent.intent_id, {
            **base,
            "observed_at": (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).isoformat(),
        })
    with pytest.raises(ValueError, match="spread exceeds"):
        manager.record_simulation(intent.intent_id, {
            **base,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "bid_price": 1.00,
        })


def test_claim_requires_fresh_machine_enforced_capital_review(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())

    with pytest.raises(ValueError, match="buying-power review is required"):
        claim_intent(manager, intent)

    blocked = pass_capital_review(manager, intent, buying_power=6_000.0)
    assert blocked.status == IntentStatus.BLOCKED
    assert blocked.capital_check["max_capital"] == 4_800.0
    assert blocked.capital_check["passed"] is False
    assert "exceeds 80% buying-power ceiling" in blocked.reasons[-1]


def test_capital_review_rejects_stale_or_wrong_account_observations(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    with pytest.raises(ValueError, match="fresh buying power"):
        manager.attest_capital(
            intent.intent_id,
            agent="codex",
            buying_power=10_000,
            observed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            currency="USD",
            account_scope="ROBINHOOD_AGENTIC_ONLY",
        )
    with pytest.raises(ValueError, match="dedicated Agentic account"):
        manager.attest_capital(
            intent.intent_id,
            agent="codex",
            buying_power=10_000,
            observed_at=datetime.now(timezone.utc),
            currency="USD",
            account_scope="OTHER_ACCOUNT",
        )


def test_expired_claim_lease_requires_a_new_capital_review(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    claim_intent(manager, intent)
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    intent.claim["lease_until"] = old.isoformat()
    intent.capital_check["observed_at"] = old.isoformat()

    with pytest.raises(ValueError, match="buying-power review is stale"):
        claim_intent(manager, intent)

    pass_capital_review(manager, intent)
    reclaimed = claim_intent(manager, intent)
    assert reclaimed.status == IntentStatus.CLAIMED
    assert datetime.fromisoformat(reclaimed.capital_check["observed_at"]) > old


def test_claim_revalidates_stale_option_selection_and_current_risk_caps(
    tmp_path, calibrated
):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy({
        "mode": "LIVE",
        "live_for_minutes": 30,
        "allowed_symbols": ["SPY"],
        "allowed_strategies": ["EMA9_MTF_0DTE"],
        "allowed_modules": ["0DTE"],
        "max_dollar_risk_per_order": 250,
        "max_daily_dollar_risk": 500,
    })
    intent = manager.on_alert(make_alert(
        module=Module.ZDTE,
        strategy="EMA9_MTF_0DTE",
        capital_required=0,
    ))
    manager.attest_option_selection(
        intent.intent_id,
        agent="codex",
        buying_power=300,
        observed_at=datetime.now(timezone.utc),
        account_scope="ROBINHOOD_AGENTIC_ONLY",
        candidates=[option_candidate("risk-contract", delta=0.70, ask=2.00)],
    )
    pass_capital_review(manager, intent, buying_power=300)
    intent.option_selection["quote_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    with pytest.raises(ValueError, match="option selection quote is stale"):
        claim_intent(manager, intent)

    intent.option_selection["quote_at"] = datetime.now(timezone.utc).isoformat()
    manager.update_policy({
        "mode": "LIVE",
        "live_for_minutes": 30,
        "max_dollar_risk_per_order": 150,
    })
    with pytest.raises(ValueError, match="current per-order risk cap"):
        claim_intent(manager, intent)


def test_closed_trade_still_consumes_the_daily_risk_budget(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy({
        **live_policy(),
        "max_dollar_risk_per_order": 100,
        "max_daily_dollar_risk": 150,
    })
    first = manager.on_alert(make_alert())
    pass_capital_review(manager, first)
    claim_intent(manager, first)
    manager.record_receipt(first.intent_id, broker_receipt("EXECUTED"))
    manager.record_receipt(first.intent_id, broker_receipt("CLOSED", price=501.0))

    second = manager.on_alert(make_alert(alert_id="alr_second_daily_trade"))
    assert second.status == IntentStatus.BLOCKED
    assert "daily automation risk cap exceeded" in second.reasons


def test_intent_audit_log_preserves_lifecycle_events(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    claim_intent(manager, intent)
    manager.record_receipt(
        intent.intent_id,
        broker_receipt(
            "EXECUTED", broker_order_id="rh-audit-1", reason="entry filled"
        ),
    )

    restored = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    events = list(reversed(restored.audit_log()))
    assert [row["event"] for row in events] == [
        "INTENT_CREATED", "CAPITAL_PREFLIGHT",
        "CLAIM_ACQUIRED", "BROKER_RECEIPT"
    ]
    assert events[-1]["detail"] == "broker order rh-audit-1"
    assert events[-1]["to_status"] == "EXECUTED"


def test_durable_event_rows_survive_replica_last_writer_wins(tmp_path, calibrated):
    store = TerminalStore(tmp_path / "shared.sqlite3")
    store.init_schema()
    first = AutotradeManager(
        tmp_path / "policy.yaml", tmp_path / "state.json", state_store=store
    )
    first.update_policy(live_policy())
    intent = first.on_alert(make_alert())
    second = AutotradeManager(
        tmp_path / "policy.yaml", tmp_path / "state.json", state_store=store
    )

    first.reject(intent.intent_id, "replica one")
    second.reject(intent.intent_id, "replica two")

    events = store.list_autotrade_events(intent.intent_id)
    assert [item["event"] for item in events] == [
        "INTENT_CREATED", "OPERATOR_REJECTED", "OPERATOR_REJECTED"
    ]
    assert {item["detail"] for item in events[-2:]} == {"replica one", "replica two"}
    assert len({item["event_id"] for item in events}) == 3


def test_trade_details_restore_from_immutable_event_rows(tmp_path, calibrated):
    store = TerminalStore(tmp_path / "trade-events.sqlite3")
    store.init_schema()
    manager = AutotradeManager(
        tmp_path / "policy.yaml", tmp_path / "state.json", state_store=store
    )
    manager.update_policy({
        "mode": "SIMULATION",
        "allowed_symbols": ["SPY"],
        "allowed_strategies": ["EMA9_MTF_0DTE"],
        "allowed_modules": ["0DTE"],
    })
    intent = manager.on_alert(make_alert(
        module=Module.ZDTE,
        strategy="EMA9_MTF_0DTE",
        status="SHADOW",
        research_only=True,
        capital_required=0,
    ))
    manager.attest_option_selection(
        intent.intent_id,
        agent="codex",
        buying_power=300,
        observed_at=datetime.now(timezone.utc),
        account_scope="ROBINHOOD_AGENTIC_ONLY",
        candidates=[option_candidate("durable-option", delta=0.65, ask=2.00)],
    )
    manager.record_simulation(intent.intent_id, {
        "event": "ENTRY",
        "option_id": "durable-option",
        "reason": "durable simulated entry",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "bid_price": 1.95,
        "ask_price": 2.00,
        "underlying_price": 500.0,
        "volume": 1_000,
        "open_interest": 5_000,
    })

    restored = AutotradeManager(
        tmp_path / "policy.yaml", tmp_path / "state.json", state_store=store
    )
    restored_intent = restored.intents[intent.intent_id]
    assert restored_intent.trade_events[0]["option_id"] == "durable-option"
    assert restored_intent.trade_events[0]["option_price"] == 2.0
    assert restored_intent.receipt["reason"] == "durable simulated entry"


def test_orphaned_replica_event_remains_visible_with_immutable_metadata(
    tmp_path, calibrated
):
    store = TerminalStore(tmp_path / "replicas.sqlite3")
    store.init_schema()
    first = AutotradeManager(
        tmp_path / "policy.yaml", tmp_path / "state.json", state_store=store
    )
    first.update_policy(live_policy())
    second = AutotradeManager(
        tmp_path / "policy.yaml", tmp_path / "state.json", state_store=store
    )

    lost_from_snapshot = first.on_alert(make_alert(alert_id="alr_replica_one"))
    retained_in_snapshot = second.on_alert(make_alert(alert_id="alr_replica_two"))
    reopened = AutotradeManager(
        tmp_path / "policy.yaml", tmp_path / "state.json", state_store=store
    )

    assert lost_from_snapshot.intent_id not in reopened.intents
    assert retained_in_snapshot.intent_id in reopened.intents
    log = reopened.audit_log()
    by_id = {item["intent_id"]: item for item in log}
    assert {lost_from_snapshot.intent_id, retained_in_snapshot.intent_id} <= set(by_id)
    orphan = by_id[lost_from_snapshot.intent_id]
    assert orphan["alert_id"] == "alr_replica_one"
    assert orphan["symbol"] == "SPY"
    assert orphan["strategy"] == "TEST_STRATEGY"
    assert orphan["mode"] == "LIVE"


def test_live_mode_requires_explicit_allowlists_and_time_limit(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    with pytest.raises(ValueError, match="live_for_minutes"):
        manager.update_policy({"mode": "LIVE"})
    with pytest.raises(ValueError, match="symbol allowlist"):
        manager.update_policy({
            "mode": "LIVE",
            "live_for_minutes": 30,
            "allowed_symbols": [],
            "allowed_strategies": [],
            "allowed_modules": [],
        })


def test_ineligible_alert_is_recorded_blocked_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(
        CalibrationMap,
        "load",
        classmethod(
            lambda _cls, strategy: CalibrationMap(
                strategy,
                {"0-100": {"n": 100, "wr": 0.80, "sufficient": True}},
                {"live_eligible": False},
            )
        ),
    )
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    assert intent.status == IntentStatus.BLOCKED
    assert "strategy is not explicitly live eligible" in intent.reasons


def test_cancelled_alert_cannot_become_ready_after_data_recovers(
    tmp_path, calibrated
):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())

    intent = manager.on_alert(make_alert(status="CANCELLED"))

    assert intent.status == IntentStatus.BLOCKED
    assert "alert status is cancelled" in intent.reasons

    active = make_alert(alert_id="alr_test_spy_existing")
    existing = manager.on_alert(active)
    assert existing.status == IntentStatus.READY
    retired = manager.on_alert(active.model_copy(update={"status": "CANCELLED"}))
    assert retired.status == IntentStatus.BLOCKED
    assert "alert status is cancelled" in retired.reasons


def test_live_claim_receipt_and_persistence(tmp_path, calibrated):
    policy = tmp_path / "policy.yaml"
    state = tmp_path / "state.json"
    manager = AutotradeManager(policy, state)
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    assert intent.status == IntentStatus.READY
    pass_capital_review(manager, intent)
    assert claim_intent(manager, intent).status == IntentStatus.CLAIMED
    executed = manager.record_receipt(
        intent.intent_id,
        broker_receipt(
            "EXECUTED",
            broker_order_id="rh-order-1",
            price=500.1,
            reason="reviewed trend entry filled",
        ),
    )
    assert executed.status == IntentStatus.EXECUTED
    assert executed.receipt["broker_order_id"] == "rh-order-1"

    restored = AutotradeManager(policy, state)
    assert restored.intents[intent.intent_id].status == IntentStatus.EXECUTED
    assert restored.record_receipt(
        intent.intent_id,
        broker_receipt("CLOSED", price=501.0, reason="trend broke below the 9EMA"),
    ).status == (
        IntentStatus.CLOSED
    )


def test_failed_exit_keeps_exposure_open_until_closed_receipt(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    claim_intent(manager, intent)
    manager.record_receipt(intent.intent_id, broker_receipt("EXECUTED"))

    failed_exit = manager.record_receipt(
        intent.intent_id,
        broker_receipt("FAILED", reason="protective exit was rejected"),
    )

    assert failed_exit.status == IntentStatus.EXECUTED
    assert failed_exit.events[-1].event == "BROKER_EXIT_FAILED"
    assert manager.status()["open_trades"] == 1
    assert manager.record_receipt(
        intent.intent_id,
        broker_receipt("CLOSED", price=495.0, reason="replacement exit filled"),
    ).status == IntentStatus.CLOSED


def test_claim_revalidates_current_allowlist_after_policy_change(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    manager.update_policy({
        "mode": "LIVE",
        "live_for_minutes": 30,
        "allowed_symbols": ["QQQ"],
    })

    blocked = claim_intent(manager, intent)

    assert blocked.status == IntentStatus.BLOCKED
    assert "no longer allowlisted" in blocked.reasons[-1]


def test_claim_rejects_static_option_plan_after_options_are_disabled(
    tmp_path, calibrated
):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert(alert_id="alr_legacy_static_option"))
    intent.order_plan["instrument"] = {
        "type": "OPTION",
        "contracts": 1,
        "legs": [{"occ_symbol": "SPY_TEST_CALL"}],
    }
    pass_capital_review(manager, intent)
    manager.update_policy({
        "mode": "LIVE",
        "live_for_minutes": 30,
        "allow_options": False,
    })

    blocked = claim_intent(manager, intent)

    assert blocked.status == IntentStatus.BLOCKED
    assert "options automation is currently disabled" in blocked.reasons[-1]


def test_symbol_data_gate_blocks_create_claim_and_claimed_intents(
    tmp_path, calibrated
):
    health = {"reason": None}
    manager = AutotradeManager(
        tmp_path / "policy.yaml",
        tmp_path / "state.json",
        symbol_gate=lambda _symbol: health["reason"],
    )
    manager.update_policy(live_policy())
    awaiting = manager.on_alert(make_alert())
    health["reason"] = "SPY market data is quarantined"
    assert claim_intent(manager, awaiting).status == IntentStatus.BLOCKED
    assert health["reason"] in awaiting.reasons

    health["reason"] = None
    second = manager.on_alert(make_alert(alert_id="alr_test_spy_2"))
    assert second.status == IntentStatus.READY
    health["reason"] = "SPY market data is stale"
    assert claim_intent(manager, second).status == IntentStatus.BLOCKED

    health["reason"] = None
    third = manager.on_alert(make_alert(alert_id="alr_test_spy_3"))
    pass_capital_review(manager, third)
    claim_intent(manager, third)
    changed = manager.block_symbol("SPY", "SPY feed failed after claim")
    assert third in changed
    assert third.status == IntentStatus.CLAIMED
    assert third.claim["cancel_requested"] is True
    assert third.claim["revoked_reason"] == "SPY feed failed after claim"
    executed = manager.record_receipt(
        third.intent_id,
        broker_receipt(
            "EXECUTED", broker_order_id="late-fill", reason="late entry fill"
        ),
    )
    assert executed.status == IntentStatus.EXECUTED
    executed.valid_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ValueError, match="only READY intents"):
        claim_intent(manager, executed)
    assert executed.status == IntentStatus.EXECUTED

    manager.block_symbol("SPY", "SPY feed remains unavailable")
    assert executed.status == IntentStatus.EXECUTED
    replayed = manager.on_alert(
        make_alert(alert_id="alr_test_spy_3", status="CANCELLED")
    )
    assert replayed.status == IntentStatus.EXECUTED
    closed = manager.record_receipt(
        third.intent_id,
        broker_receipt("CLOSED", price=495.0, reason="risk exit reconciled"),
    )
    manager.block_symbol("SPY", "SPY feed remains unavailable")
    assert manager.on_alert(
        make_alert(alert_id="alr_test_spy_3", status="CANCELLED")
    ).status == IntentStatus.CLOSED
    assert closed.status == IntentStatus.CLOSED

    late = manager.on_alert(make_alert(alert_id="alr_test_spy_late_cancel"))
    pass_capital_review(manager, late)
    claim_intent(manager, late)
    manager.record_receipt(late.intent_id, broker_receipt("CANCELLED"))
    late_fill = manager.record_receipt(
        late.intent_id,
        broker_receipt(
            "EXECUTED",
            broker_order_id="post-cancel-fill",
            reason="fill raced cancellation",
        ),
    )
    assert late_fill.status == IntentStatus.EXECUTED

    health["reason"] = "SPY market data is unavailable"
    created_blocked = manager.on_alert(make_alert(alert_id="alr_test_spy_4"))
    assert created_blocked.status == IntentStatus.BLOCKED
    assert "symbol market data is stale" in created_blocked.order_plan["abort_if"][4]


def test_real_repo_policy_file_loads_and_defaults_to_simulation(tmp_path):
    """Regression test for a YAML 1.1 boolean-coercion bug: PyYAML's
    safe_load parses a bare (unquoted) `mode: OFF` as the Python boolean
    False, not the string "OFF". Because AutotradeManager() is constructed
    at FastAPI module-import time, that bug crashed the entire gateway on
    startup, not just the autotrade feature. This loads the real
    config/autotrade.yaml (read-only; state_path is redirected to tmp_path
    so the test never writes to the repo) to catch a recurrence."""
    policy_path = REPO_ROOT / "config" / "autotrade.yaml"
    assert policy_path.exists()
    manager = AutotradeManager(policy_path, tmp_path / "state.json")
    assert manager.policy.mode == AutomationMode.SIMULATION
    assert manager.status()["effective_mode"] == "SIMULATION"
    assert manager.status()["agent"] == "codex"
    assert manager.status()["contract_version"] == "2.0"


def test_only_codex_can_claim_ready_intents(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())

    with pytest.raises(ValueError, match="claim agent must be codex"):
        claim_intent(manager, intent, agent="retired-agent")

    assert intent.status == IntentStatus.READY
    pass_capital_review(manager, intent)
    claimed = claim_intent(manager, intent)
    assert claimed.status == IntentStatus.CLAIMED
    assert claimed.claim["agent"] == "codex"


def test_switch_to_simulation_blocks_unclaimed_live_intent(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    manager.update_policy({"mode": "SIMULATION"})

    blocked = claim_intent(manager, intent)

    assert blocked.status == IntentStatus.BLOCKED
    assert "switched to Simulation" in blocked.reasons[-1]


def test_switch_to_simulation_revokes_claim_and_lease_is_live_window_bounded(
    tmp_path, calibrated
):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy({**live_policy(), "live_for_minutes": 1})
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    claimed = claim_intent(manager, intent)
    assert datetime.fromisoformat(claimed.claim["lease_until"]) <= manager.policy.live_until

    manager.update_policy({"mode": "SIMULATION"})

    assert claimed.status == IntentStatus.CLAIMED
    assert claimed.claim["cancel_requested"] is True
    assert "switched to Simulation" in claimed.claim["revoked_reason"]


def test_broker_receipt_rejects_account_and_credential_fields(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    claim_intent(manager, intent)

    with pytest.raises(ValueError, match="not allowlisted"):
        manager.record_receipt(
            intent.intent_id,
            broker_receipt("EXECUTED", access_token="must-not-be-persisted"),
        )

    assert intent.status == IntentStatus.CLAIMED


def test_pre_codex_policy_is_disarmed_and_normalized(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """autotrade:
  mode: ARMED
  armed_until: 2099-01-01T00:00:00Z
  agent: codex
  revision: 9
"""
    )

    manager = AutotradeManager(policy, tmp_path / "state.json")

    assert manager.policy.agent == "codex"
    assert manager.policy.contract_version == "2.0"
    assert manager.policy.mode == AutomationMode.SIMULATION
    assert manager.policy.live_until is None
    assert manager.policy.revision == 10
    persisted = yaml.safe_load(policy.read_text())["autotrade"]
    assert persisted["agent"] == "codex"
    assert persisted["contract_version"] == "2.0"
    assert persisted["mode"] == "SIMULATION"


def test_pre_codex_policy_in_settings_store_is_disarmed(tmp_path):
    store = TerminalStore(tmp_path / "settings.sqlite3")
    store.init_schema()
    store.put_setting(
        "autotrade_policy",
        {
            "mode": "ARMED",
            "armed_until": "2099-01-01T00:00:00Z",
            "agent": "codex",
            "revision": 4,
        },
    )

    manager = AutotradeManager(
        tmp_path / "policy.yaml",
        tmp_path / "state.json",
        state_store=store,
    )

    assert manager.policy.mode == AutomationMode.SIMULATION
    assert manager.policy.contract_version == "2.0"
    assert manager.policy.revision == 5
    persisted = store.get_setting("autotrade_policy")
    assert persisted["mode"] == "SIMULATION"
    assert persisted["contract_version"] == "2.0"


def test_retired_agent_claim_is_revoked_but_keeps_receipt_path(
    tmp_path, calibrated
):
    policy = tmp_path / "policy.yaml"
    state = tmp_path / "state.json"
    manager = AutotradeManager(policy, state)
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    claim_intent(manager, intent)
    intent.claim["agent"] = "retired-agent"
    manager._persist_state()

    restored = AutotradeManager(policy, state)
    migrated = restored.intents[intent.intent_id]
    assert migrated.status == IntentStatus.CLAIMED
    assert migrated.claim["cancel_requested"] is True
    assert migrated.claim["revoked_at"]
    assert "no longer authorized" in migrated.claim["revoked_reason"]

    late_fill = restored.record_receipt(
        intent.intent_id,
        broker_receipt(
            "EXECUTED",
            broker_order_id="late-broker-truth",
            reason="broker confirmed late fill",
        ),
    )
    assert late_fill.status == IntentStatus.EXECUTED


def test_pre_v2_codex_claim_is_revoked_and_keeps_only_receipt_path(
    tmp_path, calibrated
):
    policy = tmp_path / "policy.yaml"
    state = tmp_path / "state.json"
    manager = AutotradeManager(policy, state)
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    pass_capital_review(manager, intent)
    claim_intent(manager, intent)
    raw = json.loads(state.read_text())
    raw["intents"][0]["mode"] = "ARMED"
    state.write_text(json.dumps(raw))

    restored = AutotradeManager(policy, state)
    migrated = restored.intents[intent.intent_id]

    assert migrated.status == IntentStatus.CLAIMED
    assert migrated.claim["cancel_requested"] is True
    assert "pre-v2 claim is receipt-only" in migrated.claim["revoked_reason"]
    with pytest.raises(ValueError, match="claim was revoked"):
        claim_intent(restored, migrated)
    assert restored.record_receipt(
        migrated.intent_id,
        broker_receipt(
            "EXECUTED",
            broker_order_id="legacy-late-fill",
            reason="legacy broker truth reconciled",
        ),
    ).status == IntentStatus.EXECUTED
