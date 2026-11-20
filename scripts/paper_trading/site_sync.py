"""One-way, best-effort publication of paper observations to one private Site.

This module cannot place trades or mutate the ledger. Its service credential is
local-only, never part of the snapshot, request URL, status, or exception text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import http.client
import json
import os
from pathlib import Path
import secrets
import ssl
import stat
import threading
from typing import Callable


SITE_URL = "https://intellidhan-paper-journal.dhanvinnraval.chatgpt.site"
SITE_HOST = "intellidhan-paper-journal.dhanvinnraval.chatgpt.site"
CONFIG_NAME = "site-sync.json"
MAX_CONFIG_BYTES = 16 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024
NETWORK_TIMEOUT_SECONDS = 2
POLL_SECONDS = 5
MAX_BACKOFF_SECONDS = 300


@dataclass(frozen=True)
class SyncConfig:
    site_url: str
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.site_url != SITE_URL:
            raise ValueError("Paper sync permits only the configured private Site origin")
        if (not isinstance(self.token, str) or not 16 <= len(self.token) <= 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in self.token)):
            raise ValueError("The Site service credential is invalid")


def _directory(data_dir: Path, *, create: bool) -> int | None:
    """Open an owned private directory without following a symlink component."""
    path = Path(data_dir).expanduser().absolute()
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("Paper sync runtime paths must not contain symlinks")
    if create:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if not create:
            return None
        raise ValueError("The private paper sync directory is unavailable") from None
    except OSError:
        raise ValueError("The private paper sync directory is unavailable") from None
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise ValueError("The paper sync directory must be owned by this user with mode 0700")
    return fd


def _check_file(directory: int) -> os.stat_result | None:
    try:
        info = os.stat(CONFIG_NAME, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError("The Site credential must be an owned, unlinked regular file with mode 0600")
    return info


def configure_sync(data_dir: Path, site_url: str, token: str) -> SyncConfig:
    """Store an explicitly supplied service credential atomically and privately.

    The caller must obtain the token through hidden stdin or another protected
    channel, not an argument, environment dump, browser state, or log message.
    """
    config = SyncConfig(site_url, token)
    payload = json.dumps({"schema_version": 1, "site_url": config.site_url,
                          "token": config.token}, separators=(",", ":")).encode("utf-8")
    directory = _directory(data_dir, create=True)
    temp_name = ".site-sync." + secrets.token_hex(16) + ".tmp"
    try:
        _check_file(directory)
        fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Check again before replacement; replacing never follows the old file.
        _check_file(directory)
        os.replace(temp_name, CONFIG_NAME, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError:
        raise ValueError("The private Site credential could not be saved") from None
    finally:
        try:
            os.unlink(temp_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return config


def load_config(data_dir: Path) -> SyncConfig | None:
    """Return no configuration when absent; reject insecure or malformed files."""
    directory = _directory(data_dir, create=False)
    if directory is None:
        return None
    try:
        if _check_file(directory) is None:
            return None
        fd = os.open(CONFIG_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or info.st_size > MAX_CONFIG_BYTES):
                raise ValueError("The private Site credential file is invalid")
            raw = handle.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("The private Site credential file is invalid")
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "site_url", "token"}
                or not isinstance(value["schema_version"], int) or isinstance(value["schema_version"], bool)
                or value["schema_version"] != 1):
            raise ValueError("The private Site credential file is invalid")
        return SyncConfig(value["site_url"], value["token"])
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        raise ValueError("The private Site credential could not be loaded") from None
    finally:
        os.close(directory)


def _envelope(snapshot: dict) -> bytes:
    if not isinstance(snapshot, dict) or snapshot.get("mode") != "SIMULATION":
        raise ValueError("Only a paper snapshot may be synchronized")
    session_date = snapshot.get("session_date")
    captured_at = snapshot.get("now")
    if not isinstance(session_date, str) or date.fromisoformat(session_date).isoformat() != session_date:
        raise ValueError("The paper snapshot session date is invalid")
    if not isinstance(captured_at, str):
        raise ValueError("The paper snapshot timestamp is invalid")
    observed = datetime.fromisoformat(captured_at)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("The paper snapshot timestamp must include its timezone")
    envelope = {"schema_version": 1, "session_date": session_date,
                "captured_at": captured_at, "state": snapshot}
    # Bound allocation while encoding; do not first build an unbounded string.
    payload = bytearray()
    for part in json.JSONEncoder(allow_nan=False, separators=(",", ":")).iterencode(envelope):
        encoded = part.encode("utf-8")
        if len(payload) + len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("The paper snapshot exceeds the sync payload limit")
        payload.extend(encoded)
    return bytes(payload)


class SiteSync:
    """A stoppable daemon worker: never called from the trading decision path."""

    def __init__(self, snapshot_callable: Callable[[], dict], config: SyncConfig):
        if not callable(snapshot_callable) or not isinstance(config, SyncConfig):
            raise ValueError("A paper snapshot callback and private Site configuration are required")
        self._snapshot = snapshot_callable
        self._config = config
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = {"state": "STOPPED", "http_status": None, "consecutive_failures": 0,
                       "last_attempt_at": None, "last_synced_capture_at": None,
                       "next_attempt_seconds": None}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._state["state"] = "WAITING"
            self._thread = threading.Thread(target=self._run, name="paper-site-sync", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            worker = self._thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=NETWORK_TIMEOUT_SECONDS + 0.5)

    def status(self) -> dict:
        with self._lock:
            return {**self._state, "running": self._thread is not None and self._thread.is_alive()}

    def _post(self, payload: bytes) -> int:
        # HTTPSConnection does not follow redirects or inherit proxy settings.
        # The host, path, TLS verifier, and credential destination are fixed.
        connection = http.client.HTTPSConnection(SITE_HOST, port=443,
                                                timeout=NETWORK_TIMEOUT_SECONDS,
                                                context=ssl.create_default_context())
        try:
            connection.request("POST", "/api/paper/sync", body=payload, headers={
                "Content-Type": "application/json", "Accept": "application/json",
                "OAI-Sites-Authorization": "Bearer " + self._config.token,
                "User-Agent": "IntelliDhan-Paper-Sync/1", "Connection": "close",
            })
            response = connection.getresponse()
            try:
                return int(response.status)
            finally:
                response.close()
        finally:
            connection.close()

    def _sync_once(self) -> int:
        attempted_at = datetime.now(timezone.utc).isoformat()
        http_status = None
        capture = None
        outcome = "SNAPSHOT_REJECTED"
        try:
            snapshot = self._snapshot()
            payload = _envelope(snapshot)
            capture = snapshot["now"]
            outcome = "NETWORK_ERROR"
            if self._stop.is_set():
                return POLL_SECONDS
            http_status = self._post(payload)
            if not isinstance(http_status, int) or isinstance(http_status, bool) or not 100 <= http_status <= 599:
                http_status = None
            outcome = "SYNCED" if http_status is not None and 200 <= http_status < 300 else "HTTP_ERROR"
        except Exception:
            # Provider content, headers, response bodies, config, and exception
            # messages are deliberately excluded from status and application logs.
            pass
        with self._lock:
            success = outcome == "SYNCED"
            failures = 0 if success else self._state["consecutive_failures"] + 1
            delay = POLL_SECONDS if success else min(MAX_BACKOFF_SECONDS, POLL_SECONDS * 2 ** min(failures, 6))
            self._state.update(state=outcome, http_status=http_status, consecutive_failures=failures,
                               last_attempt_at=attempted_at, next_attempt_seconds=delay)
            if success:
                self._state["last_synced_capture_at"] = capture
        return delay

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self._stop.wait(self._sync_once()):
                    break
        finally:
            with self._lock:
                self._state.update(state="STOPPED", next_attempt_seconds=None)
