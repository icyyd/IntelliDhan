"""Offline tests: the publisher sidecar cannot operate the paper observer."""
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("paper_site_sync_sidecar", ROOT / "scripts/paper_site_sync.py")
sidecar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecar)


@pytest.fixture(autouse=True)
def isolate_io(monkeypatch):
    def blocked(*_args, **_kwargs):
        pytest.fail("An isolated sidecar test attempted network access or process launch")
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from paper_trading.site_sync import SITE_URL, configure_sync
    private = tmp_path / "runtime"
    configure_sync(private, SITE_URL, "test-private-sites-token-not-real")
    observer = {"pid": 1234, "instance": "a" * 32, "session_date": "2026-11-20",
                "data_dir": str(private), "source": str(ROOT)}
    session = {"mode": "SIMULATION", "session_date": "2026-11-20", "source": str(ROOT), "port": 8330}
    sidecar.atomic_json(private / "process.json", observer)
    sidecar.atomic_json(private / "session.json", session)
    monkeypatch.setattr(sidecar.paper_trader, "process_matches", lambda record: record == observer)
    return private, session, observer


def state():
    return {"mode": "SIMULATION", "session_date": "2026-11-20", "now": "2026-11-20T10:00:00-05:00", "trades": []}


def test_runtime_requires_matching_owned_simulation_observer(runtime):
    private, session, observer = runtime
    found_session, found_observer, config = sidecar.runtime(private)
    assert found_session == session and found_observer == observer
    assert config.token not in repr(config)
    sidecar.atomic_json(private / "session.json", {**session, "mode": "LIVE"})
    with pytest.raises(ValueError, match="Simulation observer"):
        sidecar.runtime(private)


def test_original_identity_change_or_exit_stops_reading(runtime, monkeypatch):
    private, _session, observer = runtime
    assert sidecar.same_observer(private, observer)
    sidecar.atomic_json(private / "process.json", {**observer, "instance": "b" * 32})
    assert not sidecar.same_observer(private, observer)
    sidecar.atomic_json(private / "process.json", observer)
    monkeypatch.setattr(sidecar.paper_trader, "process_matches", lambda _record: False)
    assert not sidecar.same_observer(private, observer)


def test_local_reader_gets_cookie_once_then_reads_only_export(runtime):
    private, _session, observer = runtime
    reader = sidecar.LocalSnapshot(private, observer["session_date"], observer)
    calls = []

    class Response:
        status = 200

        def __init__(self, url):
            self.url = url

        def geturl(self):
            return self.url

        def read(self, limit):
            assert limit == sidecar.MAX_PAYLOAD_BYTES + 1
            return b"home" if self.url.endswith("/") else json.dumps(state()).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET" and request.data is None and timeout == 2
            assert request.full_url.startswith("http://127.0.0.1:8330/")
            assert "Oai-sites-authorization" not in request.headers
            calls.append(request.full_url)
            return Response(request.full_url)

    reader.opener = Opener()
    assert reader() == state() and reader() == state()
    assert calls == [sidecar.LOCAL_URL + "/", sidecar.LOCAL_URL + "/api/paper/export.json",
                     sidecar.LOCAL_URL + "/api/paper/export.json"]


def test_no_redirects_or_proxy_inheritance(runtime, monkeypatch):
    private, _session, observer = runtime
    monkeypatch.setenv("http_proxy", "http://unexpected.example")
    reader = sidecar.LocalSnapshot(private, observer["session_date"], observer)
    redirects = [item for item in reader.opener.handlers if isinstance(item, urllib.request.HTTPRedirectHandler)]
    assert len(redirects) == 1 and isinstance(redirects[0], sidecar.NoRedirects)
    assert redirects[0].redirect_request(None, None, 302, "", {}, "https://evil.example") is None
    proxy = [item for item in reader.opener.handlers if isinstance(item, urllib.request.ProxyHandler)]
    assert not proxy or all(not item.proxies for item in proxy)
    with pytest.raises(ValueError, match="fixed local"):
        reader._get("https://evil.example")


@pytest.mark.parametrize("mutation", [{"mode": "LIVE"}, {"session_date": "2026-11-23"}])
def test_local_snapshot_rejects_other_session_and_mode(runtime, monkeypatch, mutation):
    private, _session, observer = runtime
    reader = sidecar.LocalSnapshot(private, observer["session_date"], observer)
    monkeypatch.setattr(reader, "_get", lambda _path: json.dumps({**state(), **mutation}).encode())
    with pytest.raises(ValueError, match="original Simulation session"):
        reader()


def test_changed_observer_during_request_rejects_export(runtime, monkeypatch):
    private, _session, observer = runtime
    reader = sidecar.LocalSnapshot(private, observer["session_date"], observer)
    checks = iter([True, False])
    monkeypatch.setattr(sidecar, "same_observer", lambda *_args: next(checks))
    monkeypatch.setattr(reader, "_get", lambda _path: json.dumps(state()).encode())
    with pytest.raises(ValueError, match="original Simulation session"):
        reader()


def test_expired_cookie_is_renewed_once_without_persisting_cookie(runtime, monkeypatch):
    private, _session, observer = runtime
    reader = sidecar.LocalSnapshot(private, observer["session_date"], observer)
    reader.authenticated = True
    calls = []

    def get(path):
        calls.append(path)
        if len(calls) == 1:
            raise urllib.error.HTTPError(sidecar.LOCAL_URL, 401, "unauthorized", {}, io.BytesIO())
        return json.dumps(state()).encode()

    monkeypatch.setattr(reader, "_get", get)
    assert reader() == state()
    assert calls == ["/api/paper/export.json", "/", "/api/paper/export.json"]
    assert all("cookie" not in file.name for file in private.iterdir())


def test_unsafe_runtime_and_sidecar_paths_are_rejected(runtime):
    private, _session, observer = runtime
    outside = private.parent / "untouched"
    outside.write_text("keep")
    for filename in (sidecar.LOG_FILE, sidecar.LOCK_FILE):
        path = private / filename
        path.symlink_to(outside)
        with pytest.raises(ValueError, match="no links"):
            sidecar._open_owned(path)
        path.unlink()
    process = private / sidecar.PROCESS_FILE
    process.symlink_to(outside)
    with pytest.raises(ValueError, match="no links"):
        sidecar._read_private(process)
    assert outside.read_text() == "keep"
    assert sidecar.same_observer(private, observer)


def test_sidecar_process_identity_is_exact(runtime, monkeypatch):
    private, _session, observer = runtime
    record = {"pid": 5678, "source": str(ROOT), "data_dir": str(private), "instance": "b" * 32,
              "observer_instance": observer["instance"], "observer_pid": observer["pid"]}
    command = (f"python {sidecar.SCRIPT} serve --data-dir {private} --instance {'b' * 32} "
               f"--observer-instance {observer['instance']} --observer-pid {observer['pid']}")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, command, ""))
    assert sidecar.process_matches(record)
    for change in ({"pid": True}, {"instance": "c" * 32}, {"observer_pid": 9999},
                   {"observer_instance": "d" * 32}, {"data_dir": str(private) + "-extra"}):
        assert not sidecar.process_matches({**record, **change})
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, command + " --extra", ""))
    assert not sidecar.process_matches(record)


def test_start_launches_only_sidecar_with_clean_env_and_preserves_observer(runtime, monkeypatch, capsys):
    private, session, observer = runtime
    originals = {name: (private / name).read_bytes() for name in ("session.json", "process.json", "site-sync.json")}
    launched = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")

    def launch(command, **kwargs):
        launched.append((command, kwargs))
        assert command[1:3] == [str(sidecar.SCRIPT), "serve"]
        assert "stop" not in command and "paper_trader.py" not in " ".join(command)
        assert "test-private-sites-token-not-real" not in " ".join(command)
        assert "ANTHROPIC_API_KEY" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
        assert kwargs["start_new_session"] and kwargs["stdin"] == subprocess.DEVNULL
        sidecar.atomic_json(private / sidecar.PROCESS_FILE, {
            "pid": 5678, "instance": command[command.index("--instance") + 1], "source": str(ROOT),
            "data_dir": str(private), "observer_instance": observer["instance"], "observer_pid": observer["pid"],
        })
        return SimpleNamespace(pid=5678, poll=lambda: None)

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(sidecar, "process_matches", lambda record: record.get("pid") == 5678)
    assert sidecar.start(private) == 0
    assert len(launched) == 1
    assert stat.S_IMODE((private / sidecar.LOG_FILE).stat().st_mode) == 0o600
    assert stat.S_IMODE((private / sidecar.PROCESS_FILE).stat().st_mode) == 0o600
    assert all((private / name).read_bytes() == value for name, value in originals.items())
    assert json.loads(capsys.readouterr().out)["observer_unchanged"] is True


def test_start_does_not_duplicate_or_stop_an_existing_sidecar(runtime, monkeypatch, capsys):
    private, _session, observer = runtime
    record = {"pid": 5678, "observer_pid": observer["pid"], "observer_instance": observer["instance"]}
    sidecar.atomic_json(private / sidecar.PROCESS_FILE, record)
    monkeypatch.setattr(sidecar, "process_matches", lambda _record: True)
    assert sidecar.start(private) == 0
    assert json.loads(capsys.readouterr().out)["sidecar_pid"] == 5678
    sidecar.atomic_json(private / sidecar.PROCESS_FILE, {**record, "observer_instance": "c" * 32})
    with pytest.raises(ValueError, match="no process was stopped"):
        sidecar.start(private)


def test_serve_exits_when_observer_changes_and_only_stops_sync(runtime, monkeypatch):
    private, _session, observer = runtime
    calls = []

    class Sync:
        def __init__(self, snapshot, _config):
            assert snapshot() == state()

        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

    class Event:
        def wait(self, seconds):
            assert seconds == 1
            return False

        def set(self):
            calls.append("signal_received")

    monkeypatch.setattr(sidecar, "LocalSnapshot", lambda *_args: state)
    monkeypatch.setattr(sidecar, "SiteSync", Sync)
    monkeypatch.setattr(sidecar.threading, "Event", Event)
    monkeypatch.setattr(sidecar.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(sidecar, "same_observer", lambda *_args: False)
    before = (private / "process.json").read_bytes()
    assert sidecar.serve(private, "b" * 32, observer["instance"], observer["pid"]) == 0
    assert calls == ["start", "stop"]
    assert (private / "process.json").read_bytes() == before
    assert stat.S_IMODE((private / sidecar.LOCK_FILE).stat().st_mode) == 0o600


def test_serve_rejects_startup_observer_replacement(runtime):
    private, _session, observer = runtime
    with pytest.raises(ValueError, match="observer changed"):
        sidecar.serve(private, "b" * 32, "c" * 32, observer["pid"])
    with pytest.raises(ValueError, match="observer changed"):
        sidecar.serve(private, "b" * 32, observer["instance"], observer["pid"] + 1)
    assert not (private / sidecar.PROCESS_FILE).exists()


def test_cli_has_no_observer_stop_or_broker_commands():
    with pytest.raises(SystemExit):
        sidecar.main(["stop", "--data-dir", "/unused"])
