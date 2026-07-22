"""Endpoint-level auth/fail-closed tests for the autotrade REST surface.

The AutotradeManager unit tests (test_autotrade.py) cover the state machine.
This file covers the actual security perimeter: the FastAPI routes and their
token checks. Every mutating test monkeypatches `app.loop.autotrade` to a
fresh manager on tmp paths so tests never touch the real
config/autotrade.yaml or data/autotrade_state.json in the working tree.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from intellidhan_engine.calibration import CalibrationMap
from intellidhan_gateway.app import app, loop
from intellidhan_gateway.autotrade import AutotradeManager
from intellidhan_schemas.signals import (
    Action,
    Alert,
    Module,
    TakeProfit,
    Vehicle,
)

CONTROL_HEADER = "x-autotrade-token"


def make_alert(**overrides) -> Alert:
    now = datetime.now(timezone.utc)
    data = {
        "alert_id": "alr_api_test_1", "created_at": now, "module": Module.SWING,
        "strategy": "TEST_STRATEGY", "action": Action.EQUITY_BUY, "symbol": "SPY",
        "underlying_price": 500.0, "vehicle": Vehicle.EQUITY, "legs": [],
        "equity_qty": 10, "entry_limit": 500.0, "entry_zone": (499.5, 500.5),
        "stop_underlying": 490.0, "stop_est_vehicle": 490.0, "stop_rule": "close below 490",
        "take_profits": [TakeProfit(zone_low=None, zone_high=None, underlying=520.0,
                                    tranche=1.0, basis="target 1")],
        "contracts": None, "capital_required": 5000.0, "dollar_risk": 100.0,
        "reward_risk": 2.0, "budget_note": "test", "confidence": 0.80, "factors": {},
        "trend_matrix": {"D": "UP"}, "thesis": "test thesis",
        "invalidation": "close below 490", "management": ["protect immediately"],
        "risks": [], "valid_until": now + timedelta(hours=1),
    }
    data.update(overrides)
    return Alert(**data)


@pytest.fixture
def calibrated(monkeypatch):
    def load(_cls, strategy):
        return CalibrationMap(
            strategy, {"0-100": {"n": 100, "wr": 0.80, "sufficient": True}},
            {"live_eligible": True, "evidence_status": "FORWARD_PAPER"},
        )
    monkeypatch.setattr(CalibrationMap, "load", classmethod(load))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated manager on tmp paths, swapped onto the real app's loop."""
    isolated = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")
    monkeypatch.setattr(loop, "autotrade", isolated)
    monkeypatch.delenv("AUTOTRADE_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("AUTOTRADE_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("AUTOTRADE_CODEX_AGENT_TOKEN", raising=False)
    return TestClient(app)


def live_policy(mode="LIVE"):
    payload = {
        "mode": mode, "allowed_symbols": ["SPY"], "allowed_strategies": ["TEST_STRATEGY"],
        "allowed_modules": ["SWING"], "min_confidence": 0.75,
        "max_dollar_risk_per_order": 150, "max_daily_dollar_risk": 300,
    }
    if mode == "LIVE":
        payload["live_for_minutes"] = 30
    return payload


# ---------- fail-closed when unconfigured ----------

def test_control_endpoints_503_when_token_unset(client):
    assert client.put("/api/autotrade/policy", json=live_policy("SIMULATION")).status_code == 503
    assert client.post("/api/autotrade/disarm").status_code == 503
    assert client.post("/api/autotrade/intents/from-alert/x").status_code == 503
    assert client.post("/api/autotrade/intents/x/approve").status_code == 503
    assert client.post("/api/autotrade/intents/x/reject").status_code == 503


def test_agent_endpoints_503_when_token_unset(client):
    assert client.get("/api/autotrade/intents").status_code == 503
    assert client.post("/api/autotrade/intents/x/claim").status_code == 503
    assert client.post("/api/autotrade/intents/x/capital-review").status_code == 503
    assert client.post("/api/autotrade/intents/x/option-selection").status_code == 503
    assert client.post("/api/autotrade/intents/x/simulation-receipt", json={}).status_code == 503
    assert client.post("/api/autotrade/intents/x/receipt", json={"status": "EXECUTED"}).status_code == 503


def test_status_endpoint_requires_owner_and_hides_secrets(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_CONTROL_TOKEN", "ctrl-secret-value")
    monkeypatch.setenv("AUTOTRADE_CODEX_AGENT_TOKEN", "agent-secret-value")
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", "owner-token-that-is-long-enough")
    assert client.get("/api/autotrade").status_code == 401
    r = client.get(
        "/api/autotrade",
        headers={"Authorization": "Bearer owner-token-that-is-long-enough"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["credentials_in_app"] is False
    assert "ctrl-secret-value" not in r.text
    assert "agent-secret-value" not in r.text
    assert "policy" in body and "counts" in body


def test_trade_log_requires_account_and_returns_signal_paper_and_execution_history(
    client, monkeypatch, calibrated
):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", "owner-token-that-is-long-enough")
    assert client.get("/api/trade-log").status_code == 401

    alert = make_alert(alert_id="alr_trade_log")
    loop.alerts.append(alert)
    loop.autotrade.update_policy(live_policy("SIMULATION"))
    intent = loop.autotrade.on_alert(alert)
    response = client.get(
        "/api/trade-log?limit=20",
        headers={"Authorization": "Bearer owner-token-that-is-long-enough"},
    )

    assert response.status_code == 200
    body = response.json()
    assert any(item["alert_id"] == alert.alert_id for item in body["signals"])
    assert any(item["intent_id"] == intent.intent_id for item in body["execution_events"])
    assert body["broker_history_visible"] is True
    assert body["live_execution_enabled"] is False


# ---------- token correctness ----------

def test_control_endpoint_rejects_wrong_or_missing_token(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_CONTROL_TOKEN", "correct-control-token")
    r = client.put("/api/autotrade/policy", json=live_policy("SIMULATION"))
    assert r.status_code == 401  # missing header
    r = client.put("/api/autotrade/policy", json=live_policy("SIMULATION"),
                   headers={CONTROL_HEADER: "wrong-token"})
    assert r.status_code == 401
    r = client.put("/api/autotrade/policy", json=live_policy("SIMULATION"),
                   headers={CONTROL_HEADER: "correct-control-token"})
    assert r.status_code == 200
    assert r.json()["mode"] == "SIMULATION"


def test_agent_endpoint_requires_bearer_scheme(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_CODEX_AGENT_TOKEN", "correct-agent-token")
    r = client.get("/api/autotrade/intents")
    assert r.status_code == 401  # no Authorization header
    r = client.get("/api/autotrade/intents",
                   headers={"Authorization": "correct-agent-token"})  # missing "Bearer "
    assert r.status_code == 401
    r = client.get("/api/autotrade/intents",
                   headers={"Authorization": "Bearer correct-agent-token"})
    assert r.status_code == 200
    assert r.json()["contract_version"] == "2.0"


def test_retired_agent_token_variable_cannot_authenticate(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_AGENT_TOKEN", "retired-token")
    unavailable = client.get(
        "/api/autotrade/intents",
        headers={"Authorization": "Bearer retired-token"},
    )
    assert unavailable.status_code == 503
    assert "AUTOTRADE_CODEX_AGENT_TOKEN" in unavailable.json()["detail"]

    monkeypatch.setenv("AUTOTRADE_CODEX_AGENT_TOKEN", "new-codex-token")
    assert client.get(
        "/api/autotrade/intents",
        headers={"Authorization": "Bearer retired-token"},
    ).status_code == 401
    assert client.get(
        "/api/autotrade/intents",
        headers={"Authorization": "Bearer new-codex-token"},
    ).status_code == 200


def test_control_token_does_not_grant_agent_access(client, monkeypatch):
    """The two token types must not be interchangeable, even if an operator
    accidentally reuses the same secret value for both env vars."""
    monkeypatch.setenv("AUTOTRADE_CONTROL_TOKEN", "shared-value")
    monkeypatch.setenv("AUTOTRADE_CODEX_AGENT_TOKEN", "different-value")
    r = client.get("/api/autotrade/intents", headers={"Authorization": "Bearer shared-value"})
    assert r.status_code == 401
    r = client.put("/api/autotrade/policy", json=live_policy("SIMULATION"),
                   headers={CONTROL_HEADER: "different-value"})
    assert r.status_code == 401


# ---------- lifecycle through the real routes ----------

def test_live_lifecycle_end_to_end_via_api(client, monkeypatch, calibrated):
    monkeypatch.setenv("AUTOTRADE_CONTROL_TOKEN", "ctrl")
    monkeypatch.setenv("AUTOTRADE_CODEX_AGENT_TOKEN", "agent")
    ctrl = {CONTROL_HEADER: "ctrl"}
    agent = {"Authorization": "Bearer agent"}

    r = client.put("/api/autotrade/policy", json=live_policy(), headers=ctrl)
    assert r.status_code == 200 and r.json()["mode"] == "LIVE"

    alert = make_alert()
    loop.alerts.append(alert)
    intent = loop.autotrade.on_alert(alert)
    assert intent.status.value == "READY"

    r = client.get("/api/autotrade/intents?status=READY", headers=agent)
    assert len(r.json()["intents"]) == 1

    r = client.post(f"/api/autotrade/intents/{intent.intent_id}/claim",
                    json={}, headers=agent)
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == "missing"

    r = client.post(f"/api/autotrade/intents/{intent.intent_id}/claim",
                    json={
                        "agent": "retired-agent",
                        "underlying_price": 500.0,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }, headers=agent)
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == "literal_error"

    r = client.post(f"/api/autotrade/intents/{intent.intent_id}/claim",
                    json={
                        "agent": "codex",
                        "underlying_price": 500.0,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "unexpected": True,
                    }, headers=agent)
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == "extra_forbidden"

    r = client.post(f"/api/autotrade/intents/{intent.intent_id}/claim",
                    json={
                        "agent": "codex",
                        "underlying_price": 500.0,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }, headers=agent)
    assert r.status_code == 422
    assert "buying-power review is required" in r.json()["detail"]

    r = client.post(
        f"/api/autotrade/intents/{intent.intent_id}/capital-review",
        json={
            "agent": "codex",
            "buying_power": 10_000,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "currency": "USD",
            "account_scope": "ROBINHOOD_AGENTIC_ONLY",
        },
        headers=agent,
    )
    assert r.status_code == 200
    assert r.json()["capital_check"]["passed"] is True

    r = client.post(f"/api/autotrade/intents/{intent.intent_id}/claim",
                    json={
                        "agent": "codex",
                        "underlying_price": 500.0,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }, headers=agent)
    assert r.status_code == 200 and r.json()["status"] == "CLAIMED"

    r = client.post(f"/api/autotrade/intents/{intent.intent_id}/receipt",
                    json={"status": "EXECUTED", "broker_order_id": "rh-1",
                          "average_price": 500.1, "filled_quantity": 10,
                          "observed_at": datetime.now(timezone.utc).isoformat(),
                          "pretrade_alerts": [],
                          "reason": "reviewed trend entry filled"},
                    headers=agent)
    assert r.status_code == 200 and r.json()["status"] == "EXECUTED"


def test_claim_openapi_requires_exact_codex_body(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/autotrade/intents/{intent_id}/claim"]["post"]
    request_body = operation["requestBody"]
    assert request_body["required"] is True
    body_schema = request_body["content"]["application/json"]["schema"]
    component_name = body_schema["$ref"].rsplit("/", 1)[-1]
    component = schema["components"]["schemas"][component_name]
    assert set(component["required"]) == {"agent", "underlying_price", "observed_at"}
    assert component["additionalProperties"] is False
    agent_schema = component["properties"]["agent"]
    assert agent_schema.get("const") == "codex" or agent_schema.get("enum") == [
        "codex"
    ]


def test_from_alert_endpoint_404_for_unknown_alert(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_CONTROL_TOKEN", "ctrl")
    r = client.post("/api/autotrade/intents/from-alert/does-not-exist",
                    headers={CONTROL_HEADER: "ctrl"})
    assert r.status_code == 404


def test_reject_and_disarm_via_api(client, monkeypatch, calibrated):
    monkeypatch.setenv("AUTOTRADE_CONTROL_TOKEN", "ctrl")
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", "owner-token-that-is-long-enough")
    ctrl = {CONTROL_HEADER: "ctrl"}
    client.put("/api/autotrade/policy", json=live_policy(), headers=ctrl)
    intent = loop.autotrade.on_alert(make_alert())
    r = client.post(f"/api/autotrade/intents/{intent.intent_id}/reject",
                    json={"reason": "operator declined"}, headers=ctrl)
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"

    r = client.post("/api/autotrade/disarm", headers=ctrl)
    assert r.status_code == 200 and r.json()["mode"] == "SIMULATION"
    status = client.get(
        "/api/autotrade",
        headers={"Authorization": "Bearer owner-token-that-is-long-enough"},
    )
    assert status.json()["effective_mode"] == "SIMULATION"


def test_bad_policy_returns_422_not_500(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_CONTROL_TOKEN", "ctrl")
    r = client.put("/api/autotrade/policy", json={"mode": "LIVE"},
                   headers={CONTROL_HEADER: "ctrl"})
    assert r.status_code == 422
