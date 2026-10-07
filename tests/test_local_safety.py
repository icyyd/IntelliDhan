"""A loopback research profile cannot accidentally inherit cloud/live authority."""

from datetime import datetime, timedelta, timezone

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from intellidhan_gateway import app as gateway
from intellidhan_gateway import live
from intellidhan_gateway.autotrade import AutomationMode, AutotradeManager, IntentStatus
from test_autotrade import make_alert, live_policy


def test_local_profile_rejects_live_updates_and_coerces_persisted_live(monkeypatch, tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(yaml.safe_dump({"autotrade": {
        "contract_version": "2.0", "agent": "codex", "mode": "LIVE", "revision": 7,
        "live_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }}))
    monkeypatch.setenv("INTELLIDHAN_LOCAL_ONLY", "1")
    monkeypatch.setenv("AUTOTRADE_POLICY_PATH", str(policy))
    manager = AutotradeManager(state_path=tmp_path / "intents.json")
    assert manager.policy_path == policy
    assert manager.policy.mode == AutomationMode.SIMULATION
    assert manager.policy.live_until is None
    assert manager.policy.revision == 8
    assert manager.status()["local_only"] is True
    with pytest.raises(ValueError, match="only permits SIMULATION"):
        manager.update_policy({"mode": "LIVE", "live_for_minutes": 30})
    # Even an in-memory accidental policy edit cannot grant placement authority.
    manager.policy.mode = AutomationMode.LIVE
    assert manager.effective_mode() == AutomationMode.SIMULATION
    explicit = AutotradeManager(tmp_path / "explicit.yaml", tmp_path / "other.json")
    assert explicit.policy_path == tmp_path / "explicit.yaml"


def test_local_profile_revokes_ready_authority_but_preserves_open_exposure(monkeypatch, tmp_path):
    manager = AutotradeManager(tmp_path / "policy.yaml", tmp_path / "intents.json")
    manager.update_policy(live_policy("SIMULATION"))
    ready = manager.on_alert(make_alert(alert_id="ready"))
    exposed = manager.on_alert(make_alert(alert_id="exposed"))
    ready.mode, ready.status = AutomationMode.LIVE, IntentStatus.READY
    exposed.mode, exposed.status = AutomationMode.LIVE, IntentStatus.EXECUTED
    manager._persist_state()
    monkeypatch.setenv("INTELLIDHAN_LOCAL_ONLY", "1")
    recovered = AutotradeManager(manager.policy_path, manager.state_path)
    assert recovered.intents[ready.intent_id].status == IntentStatus.BLOCKED
    assert recovered.intents[exposed.intent_id].status == IntentStatus.EXECUTED
    assert recovered.effective_mode() == AutomationMode.SIMULATION


def test_local_dotenv_loader_never_reads_repository_secrets(monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_LOCAL_ONLY", "true")
    monkeypatch.setattr(live.Path, "exists", lambda _self: pytest.fail("dotenv was accessed"))
    live._load_dotenv()


def test_local_http_and_websocket_reject_foreign_hosts_and_origins(monkeypatch):
    monkeypatch.setattr(gateway.loop.autotrade, "local_only", True)
    client = TestClient(gateway.app, base_url="http://127.0.0.1:8321")
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/", headers={"Origin": "http://127.0.0.1:8321"}).status_code == 200
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws", headers={"Origin": "https://attacker.example"}):
            pass
    assert rejected.value.code == 1008
