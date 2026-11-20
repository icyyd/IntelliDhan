"""Offline checks for the paper observer and loopback privacy boundary."""
import http.client
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from paper_trading.portal import PaperPortal  # noqa: E402
from paper_trading.runner import PaperRunner, session_bounds  # noqa: E402


class Feed:
    def __init__(self):
        self.calls = []

    def connect(self):
        self.calls.append("connect")

    def health(self):
        return {"status": "CONNECTED", "quote_logged_in": True}

    def close(self):
        pass

    def underlying(self, _symbol):
        raise RuntimeError("do not leak provider tokens")


def test_exchange_calendar_holiday_and_early_close():
    with pytest.raises(ValueError):
        session_bounds("2026-11-26")
    opened, closed = session_bounds("2026-11-20")
    assert opened.isoformat() == "2026-11-20T14:30:00+00:00"
    assert closed.isoformat() == "2026-11-20T21:00:00+00:00"
    assert session_bounds("2026-11-27")[1].hour == 18


def test_agent_waits_for_authorized_date_and_does_not_roll_over(tmp_path):
    moment = [datetime.fromisoformat("2026-11-19T21:00:00-05:00")]
    feed = Feed()
    runner = PaperRunner(tmp_path, "2026-11-20", feed, clock=lambda: moment[0])
    runner.step()
    assert runner.snapshot()["agent"]["status"] == "SCHEDULED"
    assert runner.snapshot()["account"]["initial_capital"] == 1000
    moment[0] = datetime.fromisoformat("2026-11-23T10:00:00-05:00")
    runner.step()
    assert runner.snapshot()["agent"]["status"] == "SESSION_COMPLETE"
    assert runner.snapshot()["trades"] == []


def test_runtime_data_errors_are_sanitized_and_never_create_trades(tmp_path):
    now = datetime.fromisoformat("2026-11-20T10:00:00-05:00")
    runner = PaperRunner(tmp_path, "2026-11-20", Feed(), clock=lambda: now)
    runner.step()
    state = runner.snapshot()
    assert state["agent"]["status"] == "DATA_BLOCKED"
    assert state["trades"] == []
    assert "tokens" not in json.dumps(state)
    assert state["account"]["cash"] == 1000


def test_logged_out_feed_is_not_reported_connected(tmp_path):
    feed = Feed()
    feed.health = lambda: {"status": "BLOCKED", "quote_logged_in": False}
    runner = PaperRunner(tmp_path, "2026-11-20", feed)
    runner.step()
    assert runner.snapshot()["agent"]["status"] == "DATA_BLOCKED"
    assert runner.connected is False


def test_recovered_exit_intent_survives_missing_underlying(tmp_path):
    now = datetime.fromisoformat("2026-11-20T10:00:00-05:00")
    runner = PaperRunner(tmp_path, "2026-11-20", Feed(), clock=lambda: now)
    calls = []

    class FakeLedger:
        def request_exit(self, _id, reason, _now):
            calls.append(reason)

        def mark(self, *_args):
            pass

        def exit(self, _id, _quote, underlying, _now):
            assert underlying is None

        def record_event(self, *_args, **_kwargs):
            pass

    runner.ledger = FakeLedger()
    runner.feed.option_quote = lambda _p: {"bid": 1.0}
    runner._manage_position({"symbol": "SPY", "trade_id": "paper_1", "status": "EXIT_PENDING",
                             "exit_reason": "Original stop crossed"}, now)
    assert calls == ["Original stop crossed"]


def test_fresh_exit_uses_post_fetch_clock_and_omits_stale_underlying(tmp_path):
    moment = [datetime.fromisoformat("2026-11-20T10:00:00-05:00")]
    runner = PaperRunner(tmp_path, "2026-11-20", Feed(), clock=lambda: moment[0])
    runner.feed.underlying = lambda _s: {"symbol": "SPY", "price": 100,
                                       "source": "MOOMOO_OPEND", "observed_at": "2026-11-20T09:59:00-05:00"}
    runner.feed.bars = lambda *_a: []

    def quote(_p):
        moment[0] += timedelta(seconds=2)
        return {"bid": 1, "observed_at": moment[0].isoformat()}

    runner.feed.option_quote = quote
    observed = []

    class FakeLedger:
        def request_exit(self, *_a):
            pass

        def mark(self, quote, now):
            assert now == datetime.fromisoformat(quote["observed_at"])

        def exit(self, _id, quote, underlying, now):
            assert underlying is None
            assert now == datetime.fromisoformat(quote["observed_at"])
            observed.append(True)

        def record_event(self, *_a, **_k):
            pass

    runner.ledger = FakeLedger()
    runner._manage_position({"symbol": "SPY", "trade_id": "paper_1", "status": "EXIT_PENDING",
                             "exit_reason": "Original stop crossed"}, moment[0])
    assert observed == [True]


@pytest.fixture
def portal():
    class Runner:
        def snapshot(self, **kwargs):
            return {"mode": "SIMULATION", "full_history": kwargs.get("full_history")}

    server = PaperPortal(("127.0.0.1", 0), Runner(), ROOT / "web")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.server_close()
    worker.join()


def request(server, path, headers=None, method="GET"):
    client = http.client.HTTPConnection("127.0.0.1", server.server_port)
    client.request(method, path, headers=headers or {})
    response = client.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    client.close()
    return result


def test_personal_state_requires_httponly_same_origin_session(portal):
    assert request(portal, "/api/paper/state")[0] == 401
    status, headers, _ = request(portal, "/")
    assert status == 200
    assert "HttpOnly" in headers["Set-Cookie"]
    assert "SameSite=Strict" in headers["Set-Cookie"]
    cookie = headers["Set-Cookie"].split(";")[0]
    assert request(portal, "/api/paper/state", {"Cookie": cookie})[0] == 200
    status, _, body = request(portal, "/api/paper/export.json", {"Cookie": cookie})
    assert status == 200 and json.loads(body)["full_history"] is True
    assert request(portal, "/api/paper/state", {"Cookie": cookie, "Origin": "https://evil.example"})[0] == 403
    assert request(portal, "/", {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert request(portal, "/", {"Host": "evil.example"})[0] == 403
    assert request(portal, "/", {"Sec-Fetch-Dest": "iframe"})[0] == 403
    assert request(portal, "/api/paper/state", method="POST")[0] == 405
    assert request(portal, "/../../.env", {"Cookie": cookie})[0] == 404


def test_server_refuses_external_binding():
    with pytest.raises(ValueError):
        PaperPortal(("0.0.0.0", 8330), None, ROOT / "web")


def test_private_launcher_configuration_and_env(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("paper_launcher", ROOT / "scripts/paper_trader.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.prepare(tmp_path / "runtime", "2026-11-20")
    assert config["max_premium"] == 500
    assert module.prepare(tmp_path / "runtime", "2026-11-20") == config
    with pytest.raises(ValueError, match="will not be reset"):
        module.prepare(tmp_path / "runtime", "2026-11-23")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    assert "ANTHROPIC_API_KEY" not in module.environment(tmp_path)
    assert "PYTHONPATH" not in module.environment(tmp_path)
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "runner.log").symlink_to(tmp_path / "unrelated")
    with pytest.raises(ValueError, match="symlink"):
        module.prepare(unsafe, "2026-11-20")


def test_process_ownership_requires_exact_instance(tmp_path, monkeypatch):
    import subprocess
    spec = importlib.util.spec_from_file_location("paper_launcher_check", ROOT / "scripts/paper_trader.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    record = {"pid": 12345, "source": str(ROOT), "data_dir": str(tmp_path),
              "session_date": "2026-11-20", "instance": "a" * 32}
    command = f"python {ROOT}/scripts/paper_trader.py serve --session-date 2026-11-20 --data-dir {tmp_path} --instance " + "a" * 32
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, command, ""))
    assert module.process_matches(record)
    assert not module.process_matches({**record, "instance": "b" * 32})
    assert not module.process_matches({**record, "session_date": "2026-11-23"})
    assert not module.process_matches({**record, "data_dir": str(tmp_path / "other")} )
