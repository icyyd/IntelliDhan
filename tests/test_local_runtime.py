"""The local launcher must isolate credentials/state and only stop its own process."""

import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest


spec = importlib.util.spec_from_file_location(
    "local_runtime", Path(__file__).resolve().parents[1] / "scripts/local_runtime.py",
)
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


@pytest.fixture
def runtime(tmp_path):
    repo = tmp_path / "source"
    (repo / "config").mkdir(parents=True)
    (repo / "config/autotrade.yaml").write_text("autotrade:\n  mode: SIMULATION\n")
    data = tmp_path / "private"
    values = local.prepare_runtime(data, repo)
    return data, repo, values


def test_local_environment_drops_cloud_secrets_and_uses_correct_source(runtime, monkeypatch):
    data, repo, values = runtime
    for name in ("DATABASE_URL", "INTELLIDHAN_DATABASE_URL", "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CHATGPT_WORKSPACE_AGENT_TOKEN", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "must-not-cross-boundary")
    monkeypatch.setenv("PYTHONPATH", "/wrong/checkout")
    env = local.runtime_environment(data, values, repo)
    assert "must-not-cross-boundary" not in env.values()
    assert "DATABASE_URL" not in env and "INTELLIDHAN_DATABASE_URL" not in env
    assert env["INTELLIDHAN_LOCAL_ONLY"] == "1"
    assert env["INTELLIDHAN_REQUIRE_DURABLE_STATE"] == "1"
    assert env["INTELLIDHAN_STATE_DB"] == str(data / "state.sqlite3")
    assert env["AUTOTRADE_POLICY_PATH"] == str(data / "autotrade.yaml")
    assert str(repo / "services/gateway") in env["PYTHONPATH"]
    assert "/wrong/checkout" not in env["PYTHONPATH"]
    assert all(env[key] == values[key] for key in local.SECRET_NAMES)


def test_secrets_and_policy_are_private_persistent_and_not_overwritten(runtime):
    data, repo, values = runtime
    policy = data / "autotrade.yaml"
    policy.write_text("operator-owned policy\n")
    assert local.prepare_runtime(data, repo) == values
    assert policy.read_text() == "operator-owned policy\n"
    assert data.stat().st_mode & 0o777 == 0o700
    assert (data / "secrets.json").stat().st_mode & 0o777 == 0o600
    assert len(set(values.values())) == 3
    assert all(len(value) >= 40 for value in values.values())


def test_invalid_or_public_secrets_are_not_replaced(runtime):
    data, repo, _values = runtime
    path = data / "secrets.json"
    path.write_text("operator-owned invalid content")
    with pytest.raises(local.RuntimeErrorSafe, match="not replaced"):
        local.prepare_runtime(data, repo)
    assert path.read_text() == "operator-owned invalid content"
    path.chmod(0o644)
    with pytest.raises(local.RuntimeErrorSafe, match="permissions"):
        local.prepare_runtime(data, repo)


def test_data_in_checkout_and_symlink_files_are_rejected(runtime):
    data, repo, _values = runtime
    with pytest.raises(local.RuntimeErrorSafe, match="outside"):
        local.prepare_runtime(repo / "data", repo)
    with pytest.raises(local.RuntimeErrorSafe, match="absolute"):
        local.prepare_runtime(Path("relative-data"), repo)
    target = data / "other"
    target.write_text("do not replace")
    (data / "secrets.json").unlink()
    (data / "secrets.json").symlink_to(target)
    with pytest.raises(local.RuntimeErrorSafe, match="permissions"):
        local.prepare_runtime(data, repo)
    assert target.read_text() == "do not replace"


def test_process_match_requires_pid_command_and_unpredictable_instance(monkeypatch):
    record = {"pid": 43210, "instance": str(uuid.uuid4())}
    result = SimpleNamespace(returncode=0, stdout=f"python {local.SCRIPT} run --instance wrong")
    runner = Mock(return_value=result)
    monkeypatch.setattr(subprocess, "run", runner)
    assert not local.process_matches(record)
    result.stdout = f"python unrelated.py --instance {record['instance']}"
    assert not local.process_matches(record)
    result.stdout = f"python {local.SCRIPT} run --instance {record['instance']}"
    assert local.process_matches(record)
    assert runner.call_args.args[0][:3] == ["ps", "-p", "43210"]


def test_stop_refuses_unknown_process_and_only_terminates_verified_pid(runtime, monkeypatch):
    data, _repo, _values = runtime
    record = {"pid": 43210, "instance": str(uuid.uuid4())}
    local.exclusive_write(data / "runtime.json", json.dumps(record))
    kill = Mock()
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(local, "process_matches", lambda _record: False)
    with pytest.raises(local.RuntimeErrorSafe, match="unknown"):
        local.stop(data)
    kill.assert_not_called()
    matches = iter([True, False])
    monkeypatch.setattr(local, "process_matches", lambda _record: next(matches))
    assert local.stop(data) == 0
    kill.assert_called_once_with(43210, signal.SIGTERM)
    assert (data / "secrets.json").exists()


def test_create_admin_sends_bootstrap_only_to_local_registration(runtime, monkeypatch, capsys):
    data, _repo, values = runtime
    monkeypatch.setattr(local, "read_record", lambda _dir: {"pid": 43210})
    monkeypatch.setattr(local, "process_matches", lambda _record: True)
    replies = iter(["person@example.test", "Local tester"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    password = "a-private-long-password"
    monkeypatch.setattr(local.getpass, "getpass", lambda _prompt: password)
    request = Mock(side_effect=[{"accounts_enabled": False}, {"authenticated": True}])
    monkeypatch.setattr(local, "request_json", request)
    assert local.create_admin(data, values) == 0
    path, payload = request.call_args.args
    assert path == "/api/auth/register"
    assert payload["invite_code"] == values["INTELLIDHAN_OWNER_TOKEN"]
    assert payload["password"] == password
    assert not any((data / name).exists() for name in ("password.txt", "account.json"))
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert all(value not in output.out + output.err for value in values.values())


def test_create_admin_never_replaces_existing_account(runtime, monkeypatch):
    data, _repo, values = runtime
    monkeypatch.setattr(local, "read_record", lambda _dir: {"pid": 43210})
    monkeypatch.setattr(local, "process_matches", lambda _record: True)
    request = Mock(return_value={"accounts_enabled": True})
    monkeypatch.setattr(local, "request_json", request)
    with pytest.raises(local.RuntimeErrorSafe, match="already exists"):
        local.create_admin(data, values)
    request.assert_called_once_with("/api/auth/session")


def test_server_binds_only_loopback_and_removes_only_own_record(runtime, monkeypatch):
    data, repo, values = runtime
    observed = {}

    def fake_server(_app, **kwargs):
        observed.update(kwargs)
        assert os.environ["INTELLIDHAN_LOCAL_ONLY"] == "1"
        assert os.environ["HOME"]
        assert "DATABASE_URL" not in os.environ
        record = json.loads((data / "runtime.json").read_text())
        assert record["pid"] == os.getpid()

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_server))
    monkeypatch.setattr(local, "REPO", repo)
    monkeypatch.setattr(local, "assert_port_available", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "must-not-use-cloud-db")
    # serve replaces the entire child environment; restore it after this in-process test.
    original_env = dict(os.environ)
    monkeypatch.chdir(repo)
    try:
        assert local.serve(data, values, str(uuid.uuid4())) == 0
    finally:
        os.environ.clear()
        os.environ.update(original_env)
    assert observed["host"] == "127.0.0.1" and observed["port"] == 8321
    assert observed["workers"] == 1 and observed["access_log"] is False
    assert not (data / "runtime.json").exists()


def test_status_does_not_create_runtime_state(tmp_path):
    data = tmp_path / "not-created"
    assert local.main(["--data-dir", str(data), "status"]) == 1
    assert not data.exists()


def test_port_probe_allows_restart_but_refuses_an_active_listener(monkeypatch):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((local.HOST, 0))
        listener.listen(1)
        monkeypatch.setattr(local, "PORT", listener.getsockname()[1])
        with pytest.raises(local.RuntimeErrorSafe, match="occupied"):
            local.assert_port_available()
    local.assert_port_available()

    probe = Mock()
    probe.__enter__ = Mock(return_value=probe)
    probe.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(socket, "socket", lambda: probe)
    local.assert_port_available()
    probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def test_backup_missing_source_never_creates_a_database(tmp_path):
    data = tmp_path / "missing"
    assert local.main(["--data-dir", str(data), "backup"]) == 1
    assert not data.exists()


def test_online_backup_retains_contents_and_never_overwrites_existing_copy(runtime):
    data, _repo, _values = runtime
    source = data / "state.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE preferences (value TEXT)")
        connection.execute("INSERT INTO preferences VALUES ('preserve-me')")
    source.chmod(0o600)
    assert local.backup(data) == 0
    first = next((data / "backups").glob("*.sqlite3"))
    with sqlite3.connect(first) as restored:
        assert restored.execute("SELECT value FROM preferences").fetchone() == ("preserve-me",)
        assert restored.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert first.stat().st_mode & 0o777 == 0o600
    assert (data / "backups").stat().st_mode & 0o777 == 0o700
    assert local.backup(data) == 0
    assert len(list((data / "backups").glob("*.sqlite3"))) == 2
    assert first.exists()
    assert not (data / "backups/secrets.json").exists()
