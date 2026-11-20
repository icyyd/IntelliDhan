#!/usr/bin/env python3
"""Publish an existing local paper journal without restarting its observer."""
from __future__ import annotations

import argparse
from datetime import date
import fcntl
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import paper_trader
from paper_trading.runner import atomic_json
from paper_trading.site_sync import MAX_PAYLOAD_BYTES, SiteSync, load_config


SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[1]
LOCAL_URL = "http://127.0.0.1:8330"
PROCESS_FILE = "site-sync-sidecar.json"
LOCK_FILE = "site-sync-sidecar.lock"
LOG_FILE = "site-sync-sidecar.log"


def _private_file(path: Path, *, missing_ok=False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return
        raise ValueError("A required private runtime file is missing") from None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError("Runtime files must be owned regular files with mode 0600 and no links")


def _read_private(path: Path, *, missing_ok=False) -> dict:
    _private_file(path, missing_ok=missing_ok)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise ValueError("A required private runtime file is missing") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("The private runtime file changed or is unsafe")
        raw = handle.read(16385)
    try:
        value = json.loads(raw) if len(raw) <= 16384 else None
    except (ValueError, UnicodeError):
        raise ValueError("The private runtime record is invalid") from None
    if not isinstance(value, dict):
        raise ValueError("The private runtime record is invalid")
    return value


def _open_owned(path: Path) -> int:
    _private_file(path, missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        os.close(fd)
        raise ValueError("The private sidecar file changed or is unsafe")
    return fd


def _identity(record: dict) -> tuple:
    return tuple(record.get(key) for key in ("pid", "instance", "session_date", "data_dir", "source"))


def same_observer(data_dir: Path, initial: dict) -> bool:
    try:
        current = _read_private(data_dir / "process.json")
        return _identity(current) == _identity(initial) and paper_trader.process_matches(current)
    except (OSError, ValueError):
        return False


def runtime(data_dir: Path) -> tuple[dict, dict, object]:
    # The existing configuration loader verifies 0700 ownership and rejects
    # symlink components. It reads the credential without printing it.
    config = load_config(data_dir)
    if config is None:
        raise ValueError("Configure private Site sync before starting its sidecar")
    session = _read_private(data_dir / "session.json")
    observer = _read_private(data_dir / "process.json")
    session_day = session.get("session_date")
    if (session.get("mode") != "SIMULATION" or session.get("source") != str(REPO)
            or session.get("port") != 8330 or not isinstance(session_day, str)
            or date.fromisoformat(session_day).isoformat() != session_day
            or observer.get("session_date") != session_day or observer.get("data_dir") != str(data_dir)
            or not paper_trader.process_matches(observer)):
        raise ValueError("The sidecar requires the matching owned, running Simulation observer")
    return session, observer, config


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalSnapshot:
    """GET-only loopback reader with its portal cookie kept solely in memory."""

    def __init__(self, data_dir: Path, session_date: str, observer: dict):
        self.data_dir = data_dir
        self.session_date = session_date
        self.observer = dict(observer)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirects(), urllib.request.HTTPCookieProcessor(self.cookies),
        )
        self.authenticated = False

    def _get(self, path: str) -> bytes:
        if path not in {"/", "/api/paper/export.json"}:
            raise ValueError("The sidecar permits only fixed local read endpoints")
        request = urllib.request.Request(LOCAL_URL + path, method="GET", headers={"Accept": "application/json"})
        with self.opener.open(request, timeout=2) as response:
            if response.status != 200 or response.geturl() != LOCAL_URL + path:
                raise ValueError("The local paper portal did not return the requested resource")
            raw = response.read(MAX_PAYLOAD_BYTES + 1)
            if len(raw) > MAX_PAYLOAD_BYTES:
                raise ValueError("The local paper journal exceeds the sync payload limit")
            return raw

    def __call__(self) -> dict:
        if not same_observer(self.data_dir, self.observer):
            raise ValueError("The original paper observer is no longer running")
        try:
            if not self.authenticated:
                self._get("/")
                self.authenticated = True
            try:
                raw = self._get("/api/paper/export.json")
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code != 401:
                    raise
                self._get("/")
                raw = self._get("/api/paper/export.json")
            state = json.loads(raw)
        except (OSError, ValueError, urllib.error.URLError):
            raise ValueError("The local paper journal could not be read safely") from None
        if (not isinstance(state, dict) or state.get("mode") != "SIMULATION"
                or state.get("session_date") != self.session_date
                or not same_observer(self.data_dir, self.observer)):
            raise ValueError("The local paper journal does not match the original Simulation session")
        return state


def process_matches(record: dict) -> bool:
    if (not isinstance(record.get("pid"), int) or isinstance(record.get("pid"), bool)
            or record["pid"] <= 1 or record.get("source") != str(REPO)
            or not isinstance(record.get("data_dir"), str)
            or any(not isinstance(record.get(key), str) or len(record[key]) != 32
                   or any(char not in "0123456789abcdef" for char in record[key])
                   for key in ("instance", "observer_instance"))
            or not isinstance(record.get("observer_pid"), int) or isinstance(record.get("observer_pid"), bool)
            or record["observer_pid"] <= 1):
        return False
    result = subprocess.run(["ps", "-p", str(record["pid"]), "-o", "command="],
                            capture_output=True, text=True, check=False)
    try:
        command = shlex.split(result.stdout.strip())
    except ValueError:
        return False
    expected = [str(SCRIPT), "serve", "--data-dir", record["data_dir"], "--instance", record["instance"],
                "--observer-instance", record["observer_instance"], "--observer-pid", str(record["observer_pid"])]
    return len(command) == len(expected) + 1 and command[1:] == expected


def serve(data_dir: Path, instance: str, observer_instance: str, observer_pid: int) -> int:
    os.umask(0o077)
    if (not isinstance(instance, str) or len(instance) != 32
            or any(char not in "0123456789abcdef" for char in instance)):
        raise ValueError("A valid private sidecar instance is required")
    session, observer, config = runtime(data_dir)
    if observer.get("instance") != observer_instance or observer.get("pid") != observer_pid:
        raise ValueError("The paper observer changed before sidecar startup")
    with os.fdopen(_open_owned(data_dir / LOCK_FILE), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A sidecar already owns this paper journal sync") from None
        local = LocalSnapshot(data_dir, session["session_date"], observer)
        local()  # Establish private portal access before reporting startup success.
        _private_file(data_dir / PROCESS_FILE, missing_ok=True)
        atomic_json(data_dir / PROCESS_FILE, {
            "pid": os.getpid(), "source": str(REPO), "data_dir": str(data_dir), "instance": instance,
            "observer_instance": observer_instance, "observer_pid": observer_pid,
            "session_date": session["session_date"],
        })
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_args: stop.set())
        signal.signal(signal.SIGINT, lambda *_args: stop.set())
        sync = SiteSync(local, config)
        sync.start()
        try:
            while not stop.wait(1):
                if not same_observer(data_dir, observer):
                    break
        finally:
            sync.stop()
    return 0


def start(data_dir: Path) -> int:
    os.umask(0o077)
    session, observer, _config = runtime(data_dir)
    prior = _read_private(data_dir / PROCESS_FILE, missing_ok=True)
    if process_matches(prior):
        if prior.get("observer_instance") != observer["instance"] or prior.get("observer_pid") != observer["pid"]:
            raise ValueError("A previous sidecar is exiting; no process was stopped or replaced")
        print(json.dumps({"running": True, "mode": "SIMULATION", "sidecar_pid": prior["pid"]}))
        return 0
    instance = secrets.token_hex(16)
    command = [sys.executable, str(SCRIPT), "serve", "--data-dir", str(data_dir), "--instance", instance,
               "--observer-instance", observer["instance"], "--observer-pid", str(observer["pid"])]
    with os.fdopen(_open_owned(data_dir / LOG_FILE), "ab") as log:
        child = subprocess.Popen(command, cwd=REPO, env=paper_trader.environment(data_dir),
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(40):
        if child.poll() is not None:
            raise ValueError("The sidecar exited during startup; inspect its private log")
        created = _read_private(data_dir / PROCESS_FILE, missing_ok=True)
        if created.get("instance") == instance and created.get("pid") == child.pid and process_matches(created):
            print(json.dumps({"running": True, "mode": "SIMULATION", "sidecar_pid": child.pid,
                              "session_date": session["session_date"], "observer_unchanged": True}))
            return 0
        time.sleep(0.25)
    raise ValueError("Sidecar startup could not be verified; no observer process was changed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "serve"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--instance", help=argparse.SUPPRESS)
    parser.add_argument("--observer-instance", help=argparse.SUPPRESS)
    parser.add_argument("--observer-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    data_dir = args.data_dir.expanduser().absolute()
    if args.command == "start":
        return start(data_dir)
    return serve(data_dir, args.instance, args.observer_instance, args.observer_pid)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError):
        # Do not echo provider exceptions, cookies, configuration, or tokens.
        print("Paper Site sidecar could not complete its request; verify its private configuration and observer.", file=sys.stderr)
        raise SystemExit(1) from None
