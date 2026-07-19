"""Guardrails and lifecycle tests for the Codex→Robinhood MCP intent bridge."""

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


def live_policy(mode="ARMED"):
    payload = {
        "mode": mode,
        "allowed_symbols": ["SPY"],
        "allowed_strategies": ["TEST_STRATEGY"],
        "allowed_modules": ["SWING"],
        "min_confidence": 0.75,
        "max_dollar_risk_per_order": 150,
        "max_daily_dollar_risk": 300,
    }
    if mode == "ARMED":
        payload["arm_for_minutes"] = 30
    return payload


def test_off_mode_creates_no_intent(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    assert manager.on_alert(make_alert()) is None
    assert manager.status()["effective_mode"] == "OFF"


def test_armed_mode_is_allowlisted_risk_capped_and_idempotent(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    alert = make_alert()
    intent = manager.on_alert(alert)
    assert intent.status == IntentStatus.READY
    assert intent.order_plan["account_scope"] == "ROBINHOOD_AGENTIC_ONLY"
    assert intent.order_plan["protection"]["must_be_established"] is True
    assert manager.on_alert(alert).intent_id == intent.intent_id
    assert len(manager.intents) == 1


def test_live_mode_requires_explicit_allowlists_and_time_limit(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    with pytest.raises(ValueError, match="arm_for_minutes"):
        manager.update_policy({"mode": "ARMED"})
    with pytest.raises(ValueError, match="symbol allowlist"):
        manager.update_policy({"mode": "SUPERVISED"})


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


def test_supervised_approval_claim_receipt_and_persistence(tmp_path, calibrated):
    policy = tmp_path / "policy.yaml"
    state = tmp_path / "state.json"
    manager = AutotradeManager(policy, state)
    manager.update_policy(live_policy("SUPERVISED"))
    intent = manager.on_alert(make_alert())
    assert intent.status == IntentStatus.AWAITING_APPROVAL
    assert manager.approve(intent.intent_id).status == IntentStatus.READY
    assert manager.claim(intent.intent_id, "codex").status == IntentStatus.CLAIMED
    executed = manager.record_receipt(
        intent.intent_id,
        {
            "status": "EXECUTED",
            "broker_order_id": "rh-order-1",
            "average_price": 500.1,
            "filled_quantity": 10,
            "pretrade_alerts": [],
        },
    )
    assert executed.status == IntentStatus.EXECUTED
    assert executed.receipt["broker_order_id"] == "rh-order-1"

    restored = AutotradeManager(policy, state)
    assert restored.intents[intent.intent_id].status == IntentStatus.EXECUTED
    assert restored.record_receipt(intent.intent_id, {"status": "CLOSED"}).status == (
        IntentStatus.CLOSED
    )


def test_symbol_data_gate_blocks_create_approve_claim_and_claimed_intents(
    tmp_path, calibrated
):
    health = {"reason": None}
    manager = AutotradeManager(
        tmp_path / "policy.yaml",
        tmp_path / "state.json",
        symbol_gate=lambda _symbol: health["reason"],
    )
    manager.update_policy(live_policy("SUPERVISED"))
    awaiting = manager.on_alert(make_alert())
    health["reason"] = "SPY market data is quarantined"
    assert manager.approve(awaiting.intent_id).status == IntentStatus.BLOCKED
    assert health["reason"] in awaiting.reasons

    health["reason"] = None
    second = manager.on_alert(make_alert(alert_id="alr_test_spy_2"))
    assert manager.approve(second.intent_id).status == IntentStatus.READY
    health["reason"] = "SPY market data is stale"
    assert manager.claim(second.intent_id, "codex").status == IntentStatus.BLOCKED

    health["reason"] = None
    third = manager.on_alert(make_alert(alert_id="alr_test_spy_3"))
    manager.approve(third.intent_id)
    manager.claim(third.intent_id, "codex")
    changed = manager.block_symbol("SPY", "SPY feed failed after claim")
    assert third in changed
    assert third.status == IntentStatus.CLAIMED
    assert third.claim["cancel_requested"] is True
    assert third.claim["revoked_reason"] == "SPY feed failed after claim"
    executed = manager.record_receipt(
        third.intent_id,
        {"status": "EXECUTED", "broker_order_id": "late-fill"},
    )
    assert executed.status == IntentStatus.EXECUTED
    executed.valid_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ValueError, match="only READY intents"):
        manager.claim(executed.intent_id, "codex")
    assert executed.status == IntentStatus.EXECUTED

    manager.block_symbol("SPY", "SPY feed remains unavailable")
    assert executed.status == IntentStatus.EXECUTED
    replayed = manager.on_alert(
        make_alert(alert_id="alr_test_spy_3", status="CANCELLED")
    )
    assert replayed.status == IntentStatus.EXECUTED
    closed = manager.record_receipt(third.intent_id, {"status": "CLOSED"})
    manager.block_symbol("SPY", "SPY feed remains unavailable")
    assert manager.on_alert(
        make_alert(alert_id="alr_test_spy_3", status="CANCELLED")
    ).status == IntentStatus.CLOSED
    assert closed.status == IntentStatus.CLOSED

    late = manager.on_alert(make_alert(alert_id="alr_test_spy_late_cancel"))
    manager.approve(late.intent_id)
    manager.claim(late.intent_id, "codex")
    manager.record_receipt(late.intent_id, {"status": "CANCELLED"})
    late_fill = manager.record_receipt(
        late.intent_id,
        {"status": "EXECUTED", "broker_order_id": "post-cancel-fill"},
    )
    assert late_fill.status == IntentStatus.EXECUTED

    health["reason"] = "SPY market data is unavailable"
    created_blocked = manager.on_alert(make_alert(alert_id="alr_test_spy_4"))
    assert created_blocked.status == IntentStatus.BLOCKED
    assert "symbol market data is stale" in created_blocked.order_plan["abort_if"][4]


def test_real_repo_policy_file_loads_and_defaults_to_off(tmp_path):
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
    assert manager.policy.mode == AutomationMode.OFF
    assert manager.status()["effective_mode"] == "OFF"
    assert manager.status()["agent"] == "codex"
    assert manager.status()["contract_version"] == "1.1"


def test_only_codex_can_claim_ready_intents(tmp_path, calibrated):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())

    with pytest.raises(ValueError, match="claim agent must be codex"):
        manager.claim(intent.intent_id, "retired-agent")

    assert intent.status == IntentStatus.READY
    claimed = manager.claim(intent.intent_id, "codex")
    assert claimed.status == IntentStatus.CLAIMED
    assert claimed.claim["agent"] == "codex"


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
    assert manager.policy.contract_version == "1.1"
    assert manager.policy.mode == AutomationMode.OFF
    assert manager.policy.armed_until is None
    assert manager.policy.revision == 10
    persisted = yaml.safe_load(policy.read_text())["autotrade"]
    assert persisted["agent"] == "codex"
    assert persisted["contract_version"] == "1.1"
    assert persisted["mode"] == "OFF"


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

    assert manager.policy.mode == AutomationMode.OFF
    assert manager.policy.contract_version == "1.1"
    assert manager.policy.revision == 5
    persisted = store.get_setting("autotrade_policy")
    assert persisted["mode"] == "OFF"
    assert persisted["contract_version"] == "1.1"


def test_retired_agent_claim_is_revoked_but_keeps_receipt_path(
    tmp_path, calibrated
):
    policy = tmp_path / "policy.yaml"
    state = tmp_path / "state.json"
    manager = AutotradeManager(policy, state)
    manager.update_policy(live_policy())
    intent = manager.on_alert(make_alert())
    manager.claim(intent.intent_id, "codex")
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
        {"status": "EXECUTED", "broker_order_id": "late-broker-truth"},
    )
    assert late_fill.status == IntentStatus.EXECUTED
