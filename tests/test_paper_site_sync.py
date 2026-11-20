"""Offline checks for the credential boundary and one-way paper Site publisher."""
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trading import site_sync  # noqa: E402


TOKEN = "test-service-credential-never-a-real-token"


def snapshot():
    return {"mode": "SIMULATION", "session_date": "2026-11-20",
            "now": "2026-11-20T10:00:00-05:00", "account": {"cash": 1000},
            "trades": [], "events": [{"reason": "Waiting for a qualified paper signal"}]}


def worker(callback=snapshot):
    return site_sync.SiteSync(callback, site_sync.SyncConfig(site_sync.SITE_URL, TOKEN))


@pytest.fixture(autouse=True)
def prevent_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        pytest.fail("Tests must not make an actual network connection")
    monkeypatch.setattr(site_sync.http.client, "HTTPSConnection", blocked)


def test_private_config_round_trip_and_secret_repr(tmp_path):
    private = tmp_path / "runtime"
    assert site_sync.load_config(private) is None
    config = site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)
    assert TOKEN not in repr(config)
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    path = private / "site-sync.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = site_sync.load_config(private)
    assert loaded == config
    assert loaded.token == TOKEN
    assert list(private.iterdir()) == [path]


@pytest.mark.parametrize("url", [
    site_sync.SITE_URL + "/", site_sync.SITE_URL + "/api/paper/sync",
    site_sync.SITE_URL + ".evil.example", site_sync.SITE_URL + "?token=anything",
    site_sync.SITE_URL.replace("https:", "http:"),
    "https://evil.example@" + site_sync.SITE_HOST,
    "https://" + site_sync.SITE_HOST + ":443", "https://127.0.0.1",
])
def test_only_exact_private_site_origin_allowed(tmp_path, url):
    with pytest.raises(ValueError) as caught:
        site_sync.configure_sync(tmp_path / "runtime", url, TOKEN)
    assert TOKEN not in str(caught.value)
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("token", ["", "short", TOKEN + "\r\nX-Evil:yes", TOKEN + " ", "ö" * 20, "a" * 4097, None])
def test_bad_credentials_rejected_without_echo(tmp_path, token):
    with pytest.raises(ValueError, match="credential is invalid") as caught:
        site_sync.configure_sync(tmp_path / "runtime", site_sync.SITE_URL, token)
    assert str(caught.value) == "The Site service credential is invalid"


def test_existing_config_replaced_atomically(tmp_path):
    private = tmp_path / "runtime"
    site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)
    before_inode = (private / "site-sync.json").stat().st_ino
    replacement = TOKEN + "-rotated-test"
    site_sync.configure_sync(private, site_sync.SITE_URL, replacement)
    assert site_sync.load_config(private).token == replacement
    assert (private / "site-sync.json").stat().st_ino != before_inode


def test_failed_replacement_keeps_previous_credential_and_cleans_temp(tmp_path, monkeypatch):
    private = tmp_path / "runtime"
    site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)

    def fail_replace(*_args, **_kwargs):
        raise OSError(TOKEN)

    monkeypatch.setattr(site_sync.os, "replace", fail_replace)
    with pytest.raises(ValueError, match="could not be saved") as caught:
        site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN + "-new")
    assert TOKEN not in str(caught.value)
    assert site_sync.load_config(private).token == TOKEN
    assert [path.name for path in private.iterdir()] == ["site-sync.json"]


def test_symlink_directory_and_credential_rejected(tmp_path):
    private = tmp_path / "runtime"
    private.mkdir(mode=0o700)
    link = tmp_path / "linked-runtime"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        site_sync.configure_sync(link, site_sync.SITE_URL, TOKEN)
    with pytest.raises(ValueError, match="symlink"):
        site_sync.load_config(link)
    with pytest.raises(ValueError, match="symlink"):
        site_sync.configure_sync(link / "child", site_sync.SITE_URL, TOKEN)
    target = tmp_path / "unrelated"
    target.write_text("unchanged")
    (private / "site-sync.json").symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)
    with pytest.raises(ValueError, match="regular file"):
        site_sync.load_config(private)
    assert target.read_text() == "unchanged"


def test_insecure_modes_and_hardlinks_rejected(tmp_path):
    private = tmp_path / "runtime"
    private.mkdir(mode=0o755)
    private.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)
    private.chmod(0o700)
    site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)
    config = private / "site-sync.json"
    config.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        site_sync.load_config(private)
    with pytest.raises(ValueError, match="0600"):
        site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)
    config.chmod(0o600)
    os.link(config, private / "hardlink")
    with pytest.raises(ValueError, match="regular file"):
        site_sync.load_config(private)


@pytest.mark.parametrize("content", [
    '{"token":"' + TOKEN, "[]", json.dumps({"schema_version": True, "site_url": site_sync.SITE_URL, "token": TOKEN}),
    json.dumps({"schema_version": 1, "site_url": site_sync.SITE_URL, "token": TOKEN, "extra": True}),
    " " * (site_sync.MAX_CONFIG_BYTES + 1),
])
def test_bad_config_fails_closed_without_content_echo(tmp_path, content):
    private = tmp_path / "runtime"
    site_sync.configure_sync(private, site_sync.SITE_URL, TOKEN)
    (private / "site-sync.json").write_text(content)
    with pytest.raises(ValueError) as caught:
        site_sync.load_config(private)
    assert TOKEN not in str(caught.value)


def test_envelope_preserves_observation_timestamp_and_full_state():
    state = snapshot()
    encoded = site_sync._envelope(state)
    assert json.loads(encoded) == {"schema_version": 1, "session_date": state["session_date"],
                                   "captured_at": state["now"], "state": state}
    assert TOKEN.encode() not in encoded


@pytest.mark.parametrize("change", [
    {"mode": "LIVE"}, {"session_date": "20261120"}, {"session_date": "bad"},
    {"now": "2026-11-20T10:00:00"}, {"now": None}, {"now": "bad"},
    {"bad_price": float("nan")}, {"bad_price": float("inf")},
    {"too_large": "x" * site_sync.MAX_PAYLOAD_BYTES},
])
def test_invalid_or_excessive_snapshots_never_post(change):
    state = {**snapshot(), **change}
    sync = worker(lambda: state)
    assert sync._sync_once() == 10
    status = sync.status()
    assert status["state"] == "SNAPSHOT_REJECTED"
    assert status["http_status"] is None
    assert status["last_synced_capture_at"] is None


def test_https_post_is_fixed_origin_no_redirect_and_has_verified_tls(monkeypatch):
    observed = []

    class Response:
        status = 302

        def close(self):
            observed.append("response_closed")

    class Connection:
        def __init__(self, host, *, port, timeout, context):
            assert host == site_sync.SITE_HOST
            assert port == 443 and timeout == 2
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

        def request(self, method, path, *, body, headers):
            assert method == "POST" and path == "/api/paper/sync"
            assert headers["OAI-Sites-Authorization"] == "Bearer " + TOKEN
            assert headers["Content-Type"] == "application/json"
            assert TOKEN.encode() not in body
            assert json.loads(body)["state"] == snapshot()
            observed.append("request")

        def getresponse(self):
            return Response()

        def close(self):
            observed.append("connection_closed")

    monkeypatch.setattr(site_sync.http.client, "HTTPSConnection", Connection)
    sync = worker()
    sync._sync_once()
    assert observed == ["request", "response_closed", "connection_closed"]
    assert sync.status()["http_status"] == 302
    assert sync.status()["state"] == "HTTP_ERROR"


def test_network_and_callback_errors_never_leak_credentials_or_details(monkeypatch, capsys):
    def fail(*_args):
        raise RuntimeError("provider returned " + TOKEN)

    sync = worker()
    monkeypatch.setattr(sync, "_post", fail)
    sync._sync_once()
    assert sync.status()["state"] == "NETWORK_ERROR"
    assert TOKEN not in json.dumps(sync.status())
    invalid = worker(fail)
    invalid._sync_once()
    assert invalid.status()["state"] == "SNAPSHOT_REJECTED"
    assert TOKEN not in json.dumps(invalid.status())
    output = capsys.readouterr()
    assert output.out == "" and output.err == ""


def test_failure_backoff_is_bounded_and_resets_on_success(monkeypatch):
    sync = worker()
    monkeypatch.setattr(sync, "_post", lambda _payload: 429)
    assert [sync._sync_once() for _ in range(8)] == [10, 20, 40, 80, 160, 300, 300, 300]
    assert sync.status()["consecutive_failures"] == 8
    monkeypatch.setattr(sync, "_post", lambda _payload: 204)
    assert sync._sync_once() == 5
    assert sync.status()["consecutive_failures"] == 0
    assert sync.status()["last_synced_capture_at"] == snapshot()["now"]


def test_thread_start_is_nonblocking_and_stop_interrupts_backoff(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    callback_threads = []

    def blocked_snapshot():
        callback_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(timeout=2)
        return snapshot()

    sync = worker(blocked_snapshot)
    monkeypatch.setattr(sync, "_post", lambda _payload: 204)
    sync.start()
    assert entered.wait(timeout=1)
    first_thread = sync._thread
    sync.start()
    assert sync._thread is first_thread and first_thread.daemon
    assert sync.status()["running"]
    release.set()
    sync.stop()
    assert not sync.status()["running"]
    assert sync.status()["state"] == "STOPPED"
    assert callback_threads == [first_thread.ident]
    assert first_thread.ident != threading.get_ident()


def test_stop_before_attempt_prevents_network(monkeypatch):
    sync = worker()
    calls = []
    monkeypatch.setattr(sync, "_post", lambda _payload: calls.append(True))
    sync.stop()
    sync._sync_once()
    assert calls == []


def test_status_is_a_detached_safe_copy(monkeypatch):
    sync = worker()
    monkeypatch.setattr(sync, "_post", lambda _payload: 200)
    sync._sync_once()
    state = sync.status()
    state["state"] = "modified"
    assert sync.status()["state"] == "SYNCED"
    assert TOKEN not in repr(sync) and TOKEN not in json.dumps(sync.status())
