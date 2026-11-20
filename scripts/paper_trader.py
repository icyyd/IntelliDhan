#!/usr/bin/env python3
"""Run IntelliDhan's local-only, date-fenced forward-paper journal."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO / "data" / "paper-session"
PORT = 8330


def prepare(data_dir: Path, session_date: str) -> dict:
    from paper_trading.runner import atomic_json, session_bounds

    if data_dir.is_symlink():
        raise ValueError("The private runtime directory must not be a symlink")
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(data_dir, 0o700)
    os.umask(0o077)
    for name in ("session.json", "process.json", "heartbeat.json", "runner.lock", "runner.log", "preflight.json", "sdk-home"):
        if (data_dir / name).is_symlink():
            raise ValueError("Runtime files and SDK home must not be symlinks")
    session_bounds(session_date)
    config = {"session_date": date.fromisoformat(session_date).isoformat(),
              "initial_capital": 1000, "mode": "SIMULATION", "port": PORT,
              "max_premium": 500, "max_daily_loss": 500, "max_entries": 2,
              "strategy": "ORB_RETEST_PAPER_V1", "source": str(REPO)}
    path = data_dir / "session.json"
    if path.exists():
        if path.is_symlink() or json.loads(path.read_text()) != config:
            raise ValueError("Existing session configuration differs. The account will not be reset.")
    else:
        atomic_json(path, config)
    sdk_home = data_dir / "sdk-home"
    sdk_home.mkdir(mode=0o700, exist_ok=True)
    return config


def environment(data_dir: Path) -> dict:
    # Never inherit cloud credentials, API keys, broker configuration or PYTHONPATH.
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(data_dir / "sdk-home"),
        "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1",
    }


def health() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/paper/health", timeout=2) as reply:
            return json.load(reply) == {"mode": "SIMULATION", "portal": "UP"}
    except (OSError, ValueError, urllib.error.URLError):
        return False


def process_matches(record: dict) -> bool:
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return False
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False)
    command = result.stdout.strip()
    return (
        record.get("source") == str(REPO)
        and isinstance(record.get("instance"), str) and len(record["instance"]) == 32
        and isinstance(record.get("data_dir"), str)
        and str(REPO / "scripts" / "paper_trader.py") in command and " serve " in command
        and f"--instance {record['instance']}" in command
        and f"--data-dir {record['data_dir']}" in command
        and f"--session-date {record.get('session_date')}" in command
    )


def record(data_dir: Path) -> dict:
    path = data_dir / "process.json"
    if path.is_symlink():
        raise ValueError("Runtime process record must not be a symlink")
    result = json.loads(path.read_text()) if path.exists() else {}
    return result if result.get("data_dir") == str(data_dir) else {}


def serve(data_dir: Path, session_date: str, instance: str | None = None) -> int:
    from paper_trading.feed import OpenDFeed
    from paper_trading.portal import PaperPortal
    from paper_trading.runner import PaperRunner, atomic_json

    prepare(data_dir, session_date)
    # The process owns this lock for its entire lifetime, including startup.
    instance = instance or secrets.token_hex(16)
    if len(instance) != 32 or any(c not in "0123456789abcdef" for c in instance):
        raise ValueError("Invalid paper process instance")
    lock_fd = os.open(data_dir / "runner.lock", os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A paper runner already owns this ledger") from None
        os.environ["HOME"] = str(data_dir / "sdk-home")
        runner = PaperRunner(data_dir, session_date, OpenDFeed())
        portal = PaperPortal(("127.0.0.1", PORT), runner, REPO / "web")
        atomic_json(data_dir / "process.json", {"pid": os.getpid(), "source": str(REPO), "session_date": session_date,
                                               "data_dir": str(data_dir), "instance": instance})
        worker = threading.Thread(target=runner.run, name="paper-observer", daemon=True)
        worker.start()

        def shutdown(_number, _frame):
            runner.stop_event.set()
            threading.Thread(target=portal.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        try:
            portal.serve_forever(poll_interval=0.5)
        finally:
            runner.stop_event.set()
            worker.join(timeout=15)
            portal.server_close()
            # Unresolved positions and durable exit requests are never cleared.
        return 0


def start(data_dir: Path, session_date: str) -> int:
    prepare(data_dir, session_date)
    prior = record(data_dir)
    if process_matches(prior):
        print(json.dumps({"running": True, "url": f"http://127.0.0.1:{PORT}/", "session_date": session_date}))
        return 0
    if health():
        raise ValueError("Port 8330 is occupied by another journal; no process was replaced")
    command = [sys.executable, str(Path(__file__).resolve()), "serve", "--session-date", session_date,
               "--data-dir", str(data_dir), "--instance", secrets.token_hex(16)]
    log_fd = os.open(data_dir / "runner.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(log_fd, "ab") as log:
        child = subprocess.Popen(command, cwd=REPO, env=environment(data_dir), stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
    for _ in range(40):
        if child.poll() is not None:
            raise ValueError("The paper observer exited during startup; inspect its private runner.log")
        if health():
            print(json.dumps({"running": True, "mode": "SIMULATION", "session_date": session_date,
                              "url": f"http://127.0.0.1:{PORT}/", "data_dir": str(data_dir)}))
            return 0
        time.sleep(0.25)
    raise ValueError("Startup could not be verified; inspect status before starting another process")


def preflight(data_dir: Path, session_date: str) -> int:
    from paper_trading.feed import OpenDFeed
    from paper_trading.runner import atomic_json, utcnow

    prepare(data_dir, session_date)
    os.environ["HOME"] = str(data_dir / "sdk-home")
    feed = OpenDFeed()
    result = {"mode": "SIMULATION", "session_date": session_date, "observed_at": utcnow().isoformat(),
              "ready_for_entries": False, "checks": {}}
    try:
        feed.connect()
        result["checks"]["connection"] = feed.health()
        for symbol in ("SPY", "QQQ"):
            try:
                underlying = feed.underlying(symbol)
                bars = feed.bars(symbol, utcnow())
                options = feed.candidates(symbol, "CALL", session_date, underlying["price"])
                result["checks"][symbol] = {"underlying": underlying, "completed_bars": len(bars),
                                           "verified_option_quotes": len(options), "scan": feed.last_scan}
            except Exception as exc:
                result["checks"][symbol] = {"blocked": type(exc).__name__, "reason": str(exc)[:400]}
        result["reason"] = "Preflight is diagnostic only. Every entry must independently pass session, price, quote-age, and risk checks."
    except Exception as exc:
        result["reason"] = f"OpenD preflight blocked ({type(exc).__name__})."
    finally:
        feed.close()
    atomic_json(data_dir / "preflight.json", result)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "serve", "status", "stop", "preflight"))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--session-date", help="Explicit YYYY-MM-DD; a restart never changes it")
    parser.add_argument("--instance", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    data_dir = args.data_dir.expanduser().absolute()
    if args.command in {"start", "serve", "preflight"}:
        if not args.session_date:
            parser.error("--session-date is required; no implicit next-day rollover")
        if args.command == "serve":
            return serve(data_dir, args.session_date, args.instance)
        return {"start": start, "preflight": preflight}[args.command](data_dir, args.session_date)
    prior = record(data_dir)
    if args.command == "stop":
        if process_matches(prior):
            os.kill(prior["pid"], signal.SIGTERM)
            print("Stop requested. Ledger and unresolved paper positions are retained.")
        else:
            print("No matching owned paper process; nothing was stopped.")
        return 0
    heartbeat = data_dir / "heartbeat.json"
    print(json.dumps({"running": process_matches(prior), "portal_up": health(),
                      "session_date": prior.get("session_date"),
                      "agent": json.loads(heartbeat.read_text()) if heartbeat.exists() else None}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"Paper session: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
