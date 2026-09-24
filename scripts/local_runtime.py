#!/usr/bin/env python3
"""Run the private IntelliDhan Simulation desk without cloud configuration.

The launcher itself uses only the standard library. Run with a project Python
environment, or pass --python /absolute/path/to/venv/bin/python. It never reads
the repository .env or displays generated credentials.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import uuid


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/local_runtime.py"
HOST, PORT = "127.0.0.1", 8321
URL = f"http://{HOST}:{PORT}"
SECRET_NAMES = (
    "INTELLIDHAN_OWNER_TOKEN", "AUTOTRADE_CODEX_AGENT_TOKEN", "AUTOTRADE_CONTROL_TOKEN",
)


class RuntimeErrorSafe(RuntimeError):
    """An operator-facing error that contains no request payload or credentials."""


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/IntelliDhan/local"
    return Path.home() / ".local/share/IntelliDhan/local"


def validate_private_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeErrorSafe(f"Refusing unsafe local file permissions: {path}")


def exclusive_write(path: Path, content: str | bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content.encode() if isinstance(content, str) else content)


def prepare_runtime(data_dir: Path, repo: Path = REPO) -> dict[str, str]:
    if not data_dir.is_absolute():
        raise RuntimeErrorSafe("The local data directory must be an absolute path.")
    if data_dir.is_symlink():
        raise RuntimeErrorSafe("The local data directory must not be a symlink.")
    if data_dir.resolve().is_relative_to(repo.resolve()):
        raise RuntimeErrorSafe("Keep local data outside the source checkout.")
    ancestor = data_dir
    while not ancestor.exists():
        ancestor = ancestor.parent
    probe = subprocess.run(
        ["git", "-C", str(ancestor), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode == 0 and probe.stdout.strip() == "true":
        raise RuntimeErrorSafe("Keep local data outside every Git worktree.")
    data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = data_dir.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeErrorSafe(f"Local data directory must be owned by you with mode 0700: {data_dir}")
    secret_path = data_dir / "secrets.json"
    if not secret_path.exists():
        values = {name: secrets.token_urlsafe(48) for name in SECRET_NAMES}
        try:
            exclusive_write(secret_path, json.dumps(values, indent=2) + "\n")
        except FileExistsError:
            pass
    validate_private_file(secret_path)
    try:
        values = json.loads(secret_path.read_text())
    except (ValueError, OSError) as exc:
        raise RuntimeErrorSafe("Local secret file is unreadable; it was not replaced.") from exc
    if not isinstance(values, dict) or set(values) != set(SECRET_NAMES) or any(
        not isinstance(value, str) or len(value) < 40 for value in values.values()
    ):
        raise RuntimeErrorSafe("Local secret file has an invalid schema; it was not replaced.")
    policy = data_dir / "autotrade.yaml"
    if not policy.exists():
        try:
            exclusive_write(policy, (repo / "config/autotrade.yaml").read_bytes())
        except FileExistsError:
            pass
    for name in ("autotrade.yaml", "autotrade-state.json", "state.sqlite3", "runtime.json", "runtime.log", "runtime.lock"):
        path = data_dir / name
        if path.exists() or path.is_symlink():
            validate_private_file(path)
    return values


def runtime_environment(data_dir: Path, values: dict[str, str], repo: Path = REPO) -> dict[str, str]:
    # An allowlist prevents inherited database URLs, broker/research/delivery
    # credentials, proxy variables, and PYTHONPATH from crossing this boundary.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    roots = [repo / "shared-schemas", *[repo / "services" / name for name in (
        "gateway", "engine", "analytics", "ingestor", "learning", "delivery",
    )]]
    env.update(values)
    env.update({
        "PYTHONPATH": os.pathsep.join(map(str, roots)),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "INTELLIDHAN_LOCAL_ONLY": "1", "INTELLIDHAN_PERSISTENT_STATE": "1",
        "INTELLIDHAN_REQUIRE_DURABLE_STATE": "1", "INTELLIDHAN_SECURE_COOKIE": "0",
        "INTELLIDHAN_STATE_DB": str(data_dir / "state.sqlite3"),
        "AUTOTRADE_POLICY_PATH": str(data_dir / "autotrade.yaml"),
        "AUTOTRADE_STATE_PATH": str(data_dir / "autotrade-state.json"),
        "XDG_CACHE_HOME": str(data_dir / "cache"),
    })
    return env


def read_record(data_dir: Path) -> dict | None:
    path = data_dir / "runtime.json"
    if not path.exists():
        return None
    validate_private_file(path)
    try:
        record = json.loads(path.read_text())
        if not isinstance(record.get("pid"), int) or record["pid"] <= 1:
            raise ValueError
        uuid.UUID(record["instance"])
        return record
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeErrorSafe("Invalid local process record; refusing to overwrite it or stop a process.") from exc


def process_matches(record: dict) -> bool:
    result = subprocess.run(
        ["ps", "-p", str(record["pid"]), "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and str(SCRIPT) in result.stdout and bool(
        re.search(r"(?:^|\s)--instance\s+" + re.escape(record["instance"]) + r"(?:\s|$)", result.stdout)
    )


def request_json(path: str, payload: dict | None = None) -> dict:
    request = Request(
        URL + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    # Local credentials must never pass through an inherited proxy.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeErrorSafe(f"Local request failed (HTTP {exc.code}); no credentials were displayed.") from exc
    except (URLError, ValueError, TimeoutError, OSError) as exc:
        raise RuntimeErrorSafe("The local server is unavailable or returned an invalid response.") from exc


def command(python: Path, data_dir: Path, instance: str) -> list[str]:
    return [str(python), str(SCRIPT), "--data-dir", str(data_dir), "--python", str(python), "run", "--instance", instance]


def assert_port_available() -> None:
    with socket.socket() as probe:
        # Match uvicorn's restart behavior: a closed server's TIME_WAIT sockets
        # must not look like an active listener. Do not enable SO_REUSEPORT.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, PORT))
        except OSError as exc:
            raise RuntimeErrorSafe(f"Port {PORT} is occupied. No existing process was stopped.") from exc


def serve(data_dir: Path, values: dict[str, str], instance: str) -> int:
    import uvicorn

    prior_umask = os.umask(0o077)
    lock = os.open(data_dir / "runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeErrorSafe("A local IntelliDhan launcher already owns this data directory.") from exc
        assert_port_available()
        previous = read_record(data_dir)
        if previous and process_matches(previous):
            raise RuntimeErrorSafe("A recorded IntelliDhan process still exists; it was not replaced.")
        record = {"pid": os.getpid(), "instance": instance, "repo": str(REPO), "url": URL, "started_at": time.time()}
        pending = data_dir / f"runtime-{instance}.tmp"
        exclusive_write(pending, json.dumps(record, indent=2) + "\n")
        pending.replace(data_dir / "runtime.json")
        env = runtime_environment(data_dir, values)
        os.environ.clear()
        os.environ.update(env)
        os.chdir(REPO)
        try:
            uvicorn.run("intellidhan_gateway.app:app", host=HOST, port=PORT, access_log=False, workers=1)
        finally:
            current = read_record(data_dir)
            if current and current.get("instance") == instance:
                (data_dir / "runtime.json").unlink()
    finally:
        os.close(lock)
        os.umask(prior_umask)
    return 0


def start(data_dir: Path, values: dict[str, str], python: Path, *, foreground: bool = False) -> int:
    previous = read_record(data_dir)
    if previous and process_matches(previous):
        print(f"IntelliDhan is already running at {URL} (PID {previous['pid']}).")
        return 0
    assert_port_available()
    instance = str(uuid.uuid4())
    args = command(python, data_dir, instance)
    env = runtime_environment(data_dir, values)
    if foreground:
        return subprocess.call(args, cwd=REPO, env=env)
    descriptor = os.open(data_dir / "runtime.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "ab") as log:
        child = subprocess.Popen(args, cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeErrorSafe(f"Local server exited. Inspect the private log at {data_dir / 'runtime.log'}.")
        try:
            record = read_record(data_dir)
            owns_process = record and record.get("instance") == instance and record.get("pid") == child.pid
            if owns_process and request_json("/api/liveness").get("ok") is True:
                print(f"IntelliDhan is running at {URL} in local Simulation. Data: {data_dir}")
                print("Use create-admin once, then sign in through the browser. Market readiness may still be warming up.")
                return 0
        except RuntimeErrorSafe:
            pass
        time.sleep(0.2)
    print(f"Local process started (PID {child.pid}); readiness is still pending. Use status. Log: {data_dir / 'runtime.log'}")
    return 0


def status(data_dir: Path) -> int:
    record = read_record(data_dir)
    if not record or not process_matches(record):
        print("No matching managed IntelliDhan process is running. No process was changed.")
        return 1
    print(f"Local process is running at {URL} (PID {record['pid']}). Data: {data_dir}")
    try:
        request_json("/api/liveness")
        print("Web process responds. Full market readiness requires /api/health; liveness does not authorize trades.")
    except RuntimeErrorSafe:
        print("Web process is not responding yet.")
    return 0


def stop(data_dir: Path) -> int:
    record = read_record(data_dir)
    if not record or not process_matches(record):
        raise RuntimeErrorSafe("No matching managed process; refusing to stop an unknown process.")
    try:
        os.kill(record["pid"], signal.SIGTERM)
    except ProcessLookupError:
        print("Local process has already stopped.")
        return 0
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not process_matches(record):
            print("Local IntelliDhan stopped. Accounts and journals remain in the data directory.")
            return 0
        time.sleep(0.2)
    raise RuntimeErrorSafe("Graceful shutdown is still pending; no force-kill was attempted.")


def create_admin(data_dir: Path, values: dict[str, str]) -> int:
    record = read_record(data_dir)
    if not record or not process_matches(record):
        raise RuntimeErrorSafe("Start the managed local server before creating its first account.")
    if request_json("/api/auth/session").get("accounts_enabled"):
        raise RuntimeErrorSafe("An account already exists. Sign in to manage accounts; nothing was overwritten.")
    email = input("Email for your local sign-in: ").strip()
    name = input("Display name: ").strip()
    password = getpass.getpass("Password (12–128 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise RuntimeErrorSafe("Passwords do not match. No account was created.")
    if not 12 <= len(password) <= 128:
        raise RuntimeErrorSafe("Password must be 12–128 characters. No account was created.")
    request_json("/api/auth/register", {
        "email": email, "display_name": name, "password": password,
        "invite_code": values["INTELLIDHAN_OWNER_TOKEN"],
    })
    print(f"Local administrator created. Sign in at {URL} with the email and password you entered.")
    return 0


def backup(data_dir: Path) -> int:
    """Take a consistent online SQLite copy; never create a missing source."""
    source = data_dir / "state.sqlite3"
    if not source.is_file():
        raise RuntimeErrorSafe("No local database exists to back up; no database was created.")
    validate_private_file(source)
    copies = data_dir / "backups"
    if copies.is_symlink():
        raise RuntimeErrorSafe("The backup directory must not be a symlink.")
    copies.mkdir(mode=0o700, exist_ok=True)
    info = copies.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeErrorSafe("The backup directory must be private (mode 0700).")
    destination = copies / f"state-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:10]}.sqlite3"
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as reader:
            if reader.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeErrorSafe("Source database integrity check failed; backup was not created.")
            exclusive_write(destination, b"")
            try:
                deadline = time.monotonic() + 30

                def bounded_progress(_status: int, _remaining: int, _total: int) -> None:
                    if time.monotonic() > deadline:
                        raise RuntimeErrorSafe("Backup exceeded its 30-second budget; retry when the database is less busy.")

                with closing(sqlite3.connect(destination, timeout=5)) as writer:
                    reader.backup(writer, pages=256, progress=bounded_progress, sleep=0.05)
                    if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise RuntimeErrorSafe("Backup integrity check failed.")
            except Exception:
                destination.unlink(missing_ok=True)
                raise
    except sqlite3.Error as exc:
        raise RuntimeErrorSafe("Local database backup failed; existing backups were preserved.") from exc
    print(f"Verified local database backup: {destination}")
    print("This same-device copy does not protect against device loss or disk failure.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("start", "status", "stop", "create-admin", "backup"):
        commands.add_parser(name)
    run = commands.add_parser("run", help="run in the foreground")
    run.add_argument("--instance", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.action == "backup":
            return backup(args.data_dir)
        if args.action in {"status", "stop"}:
            return status(args.data_dir) if args.action == "status" else stop(args.data_dir)
        values = prepare_runtime(args.data_dir)
        if not args.python.is_absolute() or not args.python.is_file():
            raise RuntimeErrorSafe("--python must name an existing absolute Python executable.")
        if args.action == "run" and args.instance:
            uuid.UUID(args.instance)
            return serve(args.data_dir, values, args.instance)
        if args.action in {"start", "run"}:
            return start(args.data_dir, values, args.python, foreground=args.action == "run")
        if args.action == "create-admin":
            return create_admin(args.data_dir, values)
        return 1
    except (RuntimeErrorSafe, ValueError) as exc:
        print(f"Local runtime: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
