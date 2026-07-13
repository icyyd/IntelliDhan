"""Guardrails and lifecycle tests for the Claude→Robinhood MCP intent bridge."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from intellidhan_engine.calibration import CalibrationMap
from intellidhan_gateway.autotrade import AutomationMode, AutotradeManager, IntentStatus
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


def test_supervised_approval_claim_receipt_and_persistence(tmp_path, calibrated):
    policy = tmp_path / "policy.yaml"
    state = tmp_path / "state.json"
    manager = AutotradeManager(policy, state)
    manager.update_policy(live_policy("SUPERVISED"))
    intent = manager.on_alert(make_alert())
    assert intent.status == IntentStatus.AWAITING_APPROVAL
    assert manager.approve(intent.intent_id).status == IntentStatus.READY
    assert manager.claim(intent.intent_id).status == IntentStatus.CLAIMED
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
