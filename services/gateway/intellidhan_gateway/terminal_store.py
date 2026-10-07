"""Durable operational state for the personal terminal.

PostgreSQL is selected when ``DATABASE_URL``/``INTELLIDHAN_DATABASE_URL`` is
configured.  A SQLite database is the zero-infrastructure local fallback.  The
schema deliberately stores audit payloads verbatim while normalizing securities,
universe membership, watchlists, and saved screens for the discovery workflow.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


SCHEMA = """
CREATE TABLE IF NOT EXISTS securities (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    exchange_name TEXT,
    sector TEXT,
    industry TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS universe_memberships (
    universe_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (universe_id, symbol, effective_from)
);
CREATE TABLE IF NOT EXISTS runtime_settings (
    setting_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trades (
    alert_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    outcome TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefings (
    briefing_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_briefs (
    briefing_id TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlists (
    watchlist_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist_members (
    watchlist_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (watchlist_id, symbol)
);
CREATE TABLE IF NOT EXISTS saved_screens (
    screen_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    filters TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS user_sessions (
    session_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id
    ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
    ON user_sessions(expires_at);
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS user_capital_limits (
    user_id TEXT NOT NULL,
    module TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, module),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS user_watchlists (
    user_id TEXT NOT NULL,
    watchlist_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, watchlist_id),
    UNIQUE (user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS user_watchlist_members (
    user_id TEXT NOT NULL,
    watchlist_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, watchlist_id, symbol),
    FOREIGN KEY (user_id, watchlist_id)
        REFERENCES user_watchlists(user_id, watchlist_id)
);
CREATE TABLE IF NOT EXISTS user_saved_screens (
    user_id TEXT NOT NULL,
    screen_id TEXT NOT NULL,
    name TEXT NOT NULL,
    filters TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, screen_id),
    UNIQUE (user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS delivery_log (
    delivery_key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autotrade_intent_events (
    event_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    event_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autotrade_events_intent_at
    ON autotrade_intent_events(intent_id, event_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = "-".join(value.strip().lower().split())
    return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-_")[:64]


class StoreUnavailable(RuntimeError):
    """Sanitized, retryable storage failure; never a missing account or empty ledger."""

    def __init__(self, reason: str, retry_after_seconds: int) -> None:
        self.reason = reason
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__(
            "Database active-time quota exhausted. Saved data is temporarily unavailable."
            if reason == "quota"
            else "Saved data is temporarily unavailable. Please try again shortly."
        )


class TerminalStore:
    """Small synchronous store; writes are short and remain off market math paths."""

    def __init__(self, location: str | Path | None = None) -> None:
        configured = str(
            location
            or os.getenv("INTELLIDHAN_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or os.getenv("INTELLIDHAN_STATE_DB", "data/intellidhan-state.sqlite3")
        )
        self.location = configured
        self.postgres = configured.startswith(("postgres://", "postgresql://"))
        self.initialized = False
        self.last_error: str | None = None
        self._failure_reason: str | None = None
        self._retry_at = 0.0
        self._recovery_probe = False
        self._failure_lock = threading.Lock()
        self._failure_generation = 0

    @property
    def retry_after_seconds(self) -> int:
        return max(0, math.ceil(self._retry_at - time.monotonic()))

    @property
    def failure_generation(self) -> int:
        """Monotonic failure history; a successful probe must not erase it."""
        with self._failure_lock:
            return self._failure_generation

    def _admit_connection(self) -> tuple[int, bool]:
        # One recovery probe at a time, shared by browser and background work.
        # Successful connections are short-lived so an idle DB can sleep.
        with self._failure_lock:
            if self._failure_reason:
                if self.retry_after_seconds or self._recovery_probe:
                    raise StoreUnavailable(self._failure_reason, self.retry_after_seconds)
                self._recovery_probe = True
                return self._failure_generation, True
            return self._failure_generation, False

    def _record_connection_failure(self, exc: Exception) -> StoreUnavailable:
        # Never send libpq's host/DSN/error details to a public health endpoint.
        reason = "quota" if "active time quota" in str(exc).lower() else "connection"
        delay = 1800 if reason == "quota" else 60
        failure = StoreUnavailable(reason, delay)
        with self._failure_lock:
            self._failure_generation += 1
            self._failure_reason = reason
            self._retry_at = time.monotonic() + delay
            self._recovery_probe = False
            self.last_error = str(failure)
        return failure

    @property
    def backend(self) -> str:
        return "postgresql" if self.postgres else "sqlite"

    @property
    def deploy_durable(self) -> bool:
        return self.postgres or os.getenv("INTELLIDHAN_PERSISTENT_STATE", "").lower() in {
            "1",
            "true",
            "yes",
        }

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        generation, recovery_probe = self._admit_connection()
        try:
            if self.postgres:
                with psycopg.connect(
                    self.location,
                    row_factory=dict_row,
                    connect_timeout=5,
                    options="-c statement_timeout=5000 -c lock_timeout=5000",
                ) as connection:
                    yield connection
                    connection.commit()
            else:
                path = Path(self.location)
                path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(path, timeout=5)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                try:
                    yield connection
                    connection.commit()
                finally:
                    connection.close()
        except (psycopg.OperationalError, psycopg.InterfaceError, sqlite3.OperationalError) as exc:
            raise self._record_connection_failure(exc) from exc
        else:
            with self._failure_lock:
                if generation == self._failure_generation:
                    self._failure_reason = None
                    self._retry_at = 0.0
                    self.last_error = None
        finally:
            with self._failure_lock:
                if recovery_probe and generation == self._failure_generation:
                    self._recovery_probe = False

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.postgres else statement

    def init_schema(self) -> None:
        try:
            with self._connection() as connection:
                if self.postgres:
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                else:
                    connection.executescript(SCHEMA)
            self.initialized = True
        except Exception as exc:
            self.initialized = False
            self.last_error = str(exc)
            raise

    def _ensure(self) -> None:
        if not self.initialized:
            self.init_schema()

    def _execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        self._ensure()
        with self._connection() as connection:
            connection.execute(self._sql(statement), params)

    def _fetchall(self, statement: str, params: tuple[Any, ...] = ()) -> list[dict]:
        self._ensure()
        with self._connection() as connection:
            rows = connection.execute(self._sql(statement), params).fetchall()
        return [dict(row) for row in rows]

    def put_setting(self, key: str, payload: Any) -> None:
        self._execute(
            """INSERT INTO runtime_settings (setting_key, payload, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(setting_key) DO UPDATE SET
                 payload=excluded.payload, updated_at=excluded.updated_at""",
            (key, json.dumps(payload), _now()),
        )

    def get_setting(self, key: str) -> Any | None:
        rows = self._fetchall(
            "SELECT payload FROM runtime_settings WHERE setting_key=?", (key,)
        )
        return json.loads(rows[0]["payload"]) if rows else None

    def append_autotrade_event(self, intent_id: str, payload: dict[str, Any]) -> None:
        """Insert one immutable event; event_id makes replica retries idempotent."""
        self._execute(
            """INSERT INTO autotrade_intent_events
               (event_id, intent_id, event_at, payload)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(event_id) DO NOTHING""",
            (
                payload["event_id"],
                intent_id,
                payload["at"],
                json.dumps(payload),
            ),
        )

    def list_autotrade_events(self, intent_id: str | None = None) -> list[dict[str, Any]]:
        if intent_id is None:
            rows = self._fetchall(
                """SELECT intent_id, payload FROM autotrade_intent_events
                   ORDER BY event_at, event_id"""
            )
        else:
            rows = self._fetchall(
                """SELECT intent_id, payload FROM autotrade_intent_events
                   WHERE intent_id=? ORDER BY event_at, event_id""",
                (intent_id,),
            )
        return [
            {"intent_id": row["intent_id"], **json.loads(row["payload"])}
            for row in rows
        ]

    # Account records are deliberately separate from shared engine state.  This
    # keeps personal limits and research lists isolated without a destructive
    # migration of legacy single-owner installations.

    def count_users(self) -> int:
        rows = self._fetchall("SELECT COUNT(*) AS count FROM users")
        return int(rows[0]["count"])

    def create_user(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        role: str = "TRADER",
        preferences: dict[str, Any] | None = None,
        capital_limits: dict[str, dict[str, Any]] | None = None,
        default_watchlist: str | None = None,
        require_first: bool = False,
    ) -> dict[str, Any]:
        """Create an account and its personal defaults in one transaction.

        ``require_first`` serializes the zero-user bootstrap claim so two
        concurrent requests cannot both create the initial administrator.
        """
        email = email.strip().lower()
        display_name = display_name.strip()
        role = role.strip().upper()
        if not email or len(email) > 254:
            raise ValueError("email must be 1–254 characters")
        if not display_name or len(display_name) > 80:
            raise ValueError("display name must be 1–80 characters")
        if role not in {"ADMIN", "TRADER", "VIEWER"}:
            raise ValueError("role must be ADMIN, TRADER, or VIEWER")
        if require_first and role != "ADMIN":
            raise ValueError("the initial account must be an administrator")
        watchlist_name = default_watchlist.strip() if default_watchlist else None
        if watchlist_name and (len(watchlist_name) > 60 or not _slug(watchlist_name)):
            raise ValueError("watchlist name must be 1–60 letters or numbers")
        user_id = uuid4().hex
        now = _now()
        public_user = {
            "user_id": user_id,
            "email": email,
            "display_name": display_name,
            "role": role,
            "active": True,
            "created_at": now,
            "updated_at": now,
            "last_login_at": None,
        }
        self._ensure()
        try:
            with self._connection() as connection:
                if require_first:
                    if self.postgres:
                        connection.execute(
                            "SELECT pg_advisory_xact_lock(%s)", (4_923_187_441,)
                        )
                    else:
                        connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        "SELECT COUNT(*) AS count FROM users"
                    ).fetchone()
                    if int(row["count"]) != 0:
                        raise ValueError("the initial administrator already exists")
                connection.execute(
                    self._sql(
                        """INSERT INTO users
                           (user_id, email, display_name, password_hash, role, active,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, 1, ?, ?)"""
                    ),
                    (user_id, email, display_name, password_hash, role, now, now),
                )
                connection.execute(
                    self._sql(
                        """INSERT INTO user_preferences (user_id, payload, updated_at)
                           VALUES (?, ?, ?)"""
                    ),
                    (user_id, json.dumps(preferences or {}), now),
                )
                for module, payload in (capital_limits or {}).items():
                    connection.execute(
                        self._sql(
                            """INSERT INTO user_capital_limits
                               (user_id, module, payload, updated_at)
                               VALUES (?, ?, ?, ?)"""
                        ),
                        (user_id, module, json.dumps(payload), now),
                    )
                if watchlist_name:
                    connection.execute(
                        self._sql(
                            """INSERT INTO user_watchlists
                               (user_id, watchlist_id, name, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?)"""
                        ),
                        (user_id, _slug(watchlist_name), watchlist_name, now, now),
                    )
        except (sqlite3.IntegrityError, psycopg.IntegrityError) as exc:
            if self.get_user_by_email(email):
                raise ValueError("an account with that email already exists") from exc
            raise RuntimeError("account defaults could not be initialized") from exc
        return public_user

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        rows = self._fetchall(
            """SELECT user_id, email, display_name, role, active, created_at,
                      updated_at, last_login_at
               FROM users WHERE user_id=?""",
            (user_id,),
        )
        if not rows:
            return None
        rows[0]["active"] = bool(rows[0]["active"])
        return rows[0]

    def get_user_by_email(self, email: str, *, include_password: bool = False) -> dict[str, Any] | None:
        columns = (
            "user_id, email, display_name, role, active, created_at, updated_at, "
            "last_login_at"
        )
        if include_password:
            columns += ", password_hash"
        rows = self._fetchall(
            f"SELECT {columns} FROM users WHERE email=?", (email.strip().lower(),)
        )
        if not rows:
            return None
        rows[0]["active"] = bool(rows[0]["active"])
        return rows[0]

    def list_users(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            """SELECT user_id, email, display_name, role, active, created_at,
                      updated_at, last_login_at
               FROM users ORDER BY display_name, email"""
        )
        for row in rows:
            row["active"] = bool(row["active"])
        return rows

    def mark_user_login(self, user_id: str) -> None:
        now = _now()
        self._execute(
            "UPDATE users SET last_login_at=?, updated_at=? WHERE user_id=?",
            (now, now, user_id),
        )

    def create_user_session(
        self, *, session_hash: str, user_id: str, expires_at: str
    ) -> None:
        now = _now()
        self._execute(
            """INSERT INTO user_sessions
               (session_hash, user_id, created_at, expires_at, last_seen_at, revoked_at)
               VALUES (?, ?, ?, ?, ?, NULL)""",
            (session_hash, user_id, now, expires_at, now),
        )

    def get_user_session(self, session_hash: str) -> dict[str, Any] | None:
        rows = self._fetchall(
            """SELECT s.session_hash, s.created_at AS session_created_at,
                      s.expires_at, s.last_seen_at, u.user_id, u.email,
                      u.display_name, u.role, u.active
               FROM user_sessions s
               JOIN users u ON u.user_id=s.user_id
               WHERE s.session_hash=? AND s.revoked_at IS NULL
                 AND s.expires_at>? AND u.active=1""",
            (session_hash, _now()),
        )
        if not rows:
            return None
        row = rows[0]
        row["active"] = bool(row["active"])
        return row

    def revoke_user_session(self, session_hash: str) -> None:
        self._execute(
            "UPDATE user_sessions SET revoked_at=? WHERE session_hash=?",
            (_now(), session_hash),
        )

    def revoke_user_sessions(self, user_id: str) -> None:
        self._execute(
            "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (_now(), user_id),
        )

    def get_user_preferences(self, user_id: str) -> dict[str, Any]:
        rows = self._fetchall(
            "SELECT payload FROM user_preferences WHERE user_id=?", (user_id,)
        )
        return json.loads(rows[0]["payload"]) if rows else {}

    def put_user_preferences(self, user_id: str, payload: dict[str, Any]) -> None:
        self._execute(
            """INSERT INTO user_preferences (user_id, payload, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 payload=excluded.payload, updated_at=excluded.updated_at""",
            (user_id, json.dumps(payload), _now()),
        )

    def get_user_capital_limits(self, user_id: str) -> dict[str, dict[str, Any]]:
        rows = self._fetchall(
            """SELECT module, payload FROM user_capital_limits
               WHERE user_id=? ORDER BY module""",
            (user_id,),
        )
        return {row["module"]: json.loads(row["payload"]) for row in rows}

    def put_user_capital_limits(
        self, user_id: str, limits: dict[str, dict[str, Any]]
    ) -> None:
        now = _now()
        self._ensure()
        with self._connection() as connection:
            for module, payload in limits.items():
                connection.execute(
                    self._sql(
                        """INSERT INTO user_capital_limits
                           (user_id, module, payload, updated_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(user_id, module) DO UPDATE SET
                             payload=excluded.payload, updated_at=excluded.updated_at"""
                    ),
                    (user_id, module, json.dumps(payload), now),
                )

    def create_user_watchlist(self, user_id: str, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("watchlist name must be 1–60 characters")
        watchlist_id = _slug(name)
        if not watchlist_id:
            raise ValueError("watchlist name must contain letters or numbers")
        now = _now()
        self._execute(
            """INSERT INTO user_watchlists
               (user_id, watchlist_id, name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, watchlist_id) DO UPDATE SET
                 name=excluded.name, updated_at=excluded.updated_at""",
            (user_id, watchlist_id, name, now, now),
        )
        return {"watchlist_id": watchlist_id, "name": name, "symbols": []}

    def list_user_watchlists(self, user_id: str) -> list[dict[str, Any]]:
        watchlists = self._fetchall(
            """SELECT watchlist_id, name, created_at, updated_at
               FROM user_watchlists WHERE user_id=? ORDER BY name""",
            (user_id,),
        )
        members = self._fetchall(
            """SELECT watchlist_id, symbol, note, created_at
               FROM user_watchlist_members WHERE user_id=? ORDER BY created_at""",
            (user_id,),
        )
        by_list: dict[str, list[dict[str, Any]]] = {}
        for member in members:
            by_list.setdefault(member["watchlist_id"], []).append(member)
        for item in watchlists:
            item["members"] = by_list.get(item["watchlist_id"], [])
            item["symbols"] = [member["symbol"] for member in item["members"]]
        return watchlists

    def add_user_watchlist_member(
        self, user_id: str, watchlist_id: str, symbol: str, note: str = ""
    ) -> None:
        if not self._fetchall(
            """SELECT watchlist_id FROM user_watchlists
               WHERE user_id=? AND watchlist_id=?""",
            (user_id, watchlist_id),
        ):
            raise KeyError("unknown watchlist")
        self._execute(
            """INSERT INTO user_watchlist_members
               (user_id, watchlist_id, symbol, note, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, watchlist_id, symbol) DO UPDATE SET
                 note=excluded.note""",
            (user_id, watchlist_id, symbol.upper(), note.strip()[:500], _now()),
        )

    def remove_user_watchlist_member(
        self, user_id: str, watchlist_id: str, symbol: str
    ) -> None:
        self._execute(
            """DELETE FROM user_watchlist_members
               WHERE user_id=? AND watchlist_id=? AND symbol=?""",
            (user_id, watchlist_id, symbol.upper()),
        )

    def user_watchlists_for_symbol(self, user_id: str, symbol: str) -> list[str]:
        rows = self._fetchall(
            """SELECT watchlist_id FROM user_watchlist_members
               WHERE user_id=? AND symbol=? ORDER BY watchlist_id""",
            (user_id, symbol.upper()),
        )
        return [row["watchlist_id"] for row in rows]

    def put_user_saved_screen(
        self, user_id: str, name: str, filters: dict[str, Any]
    ) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("screen name must be 1–60 characters")
        screen_id = _slug(name)
        if not screen_id:
            raise ValueError("screen name must contain letters or numbers")
        now = _now()
        self._execute(
            """INSERT INTO user_saved_screens
               (user_id, screen_id, name, filters, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, screen_id) DO UPDATE SET
                 name=excluded.name, filters=excluded.filters,
                 updated_at=excluded.updated_at""",
            (user_id, screen_id, name, json.dumps(filters), now, now),
        )
        return {"screen_id": screen_id, "name": name, "filters": filters}

    def list_user_saved_screens(self, user_id: str) -> list[dict[str, Any]]:
        rows = self._fetchall(
            """SELECT screen_id, name, filters, created_at, updated_at
               FROM user_saved_screens WHERE user_id=? ORDER BY name""",
            (user_id,),
        )
        for row in rows:
            row["filters"] = json.loads(row["filters"])
        return rows

    def seed_universe(self, records: list[dict[str, Any]], universe_id: str = "live") -> None:
        self._ensure()
        now = _now()
        with self._connection() as connection:
            for item in records:
                symbol = str(item["symbol"]).upper()
                connection.execute(
                    self._sql(
                        """INSERT INTO securities
                           (symbol, name, asset_type, exchange_name, sector, industry,
                            active, source, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(symbol) DO UPDATE SET
                             name=excluded.name, asset_type=excluded.asset_type,
                             exchange_name=excluded.exchange_name, sector=excluded.sector,
                             industry=excluded.industry, active=excluded.active,
                             source=excluded.source, updated_at=excluded.updated_at"""
                    ),
                    (
                        symbol,
                        item.get("name") or symbol,
                        item.get("asset_type") or "EQUITY",
                        item.get("exchange"),
                        item.get("sector"),
                        item.get("industry"),
                        1 if item.get("active", True) else 0,
                        item.get("source") or "configured",
                        now,
                    ),
                )
                connection.execute(
                    self._sql(
                        """INSERT INTO universe_memberships
                           (universe_id, symbol, effective_from, effective_to, source)
                           VALUES (?, ?, ?, NULL, ?)
                           ON CONFLICT(universe_id, symbol, effective_from) DO NOTHING"""
                    ),
                    (universe_id, symbol, now[:10], item.get("source") or "configured"),
                )

    def get_security(self, symbol: str) -> dict[str, Any] | None:
        rows = self._fetchall("SELECT * FROM securities WHERE symbol=?", (symbol.upper(),))
        if not rows:
            return None
        item = rows[0]
        item["active"] = bool(item["active"])
        item["exchange"] = item.pop("exchange_name")
        return item

    def upsert_alert(self, payload: dict[str, Any]) -> None:
        self._execute(
            """INSERT INTO alerts (alert_id, symbol, created_at, payload)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(alert_id) DO UPDATE SET payload=excluded.payload""",
            (
                payload["alert_id"],
                payload["symbol"],
                str(payload["created_at"]),
                json.dumps(payload),
            ),
        )

    def list_alerts(self, limit: int | None = 250) -> list[dict[str, Any]]:
        """Read alerts oldest-to-newest; ``None`` is the migration/audit path.

        Interactive callers stay bounded by default.  Restart migration must
        see every durable alert because paper trades are unbounded and legacy
        rows need the matching alert timestamp to recover their natural key.
        """
        if limit is None:
            rows = self._fetchall("SELECT payload FROM alerts ORDER BY created_at")
            return [json.loads(row["payload"]) for row in rows]
        rows = self._fetchall(
            "SELECT payload FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [json.loads(row["payload"]) for row in reversed(rows)]

    def upsert_paper_trade(self, payload: dict[str, Any]) -> None:
        self._execute(
            """INSERT INTO paper_trades (alert_id, symbol, outcome, updated_at, payload)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(alert_id) DO UPDATE SET
                 outcome=excluded.outcome, updated_at=excluded.updated_at,
                 payload=excluded.payload""",
            (
                payload["alert_id"],
                payload["symbol"],
                payload["outcome"],
                _now(),
                json.dumps(payload),
            ),
        )

    def list_paper_trades(self) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT payload FROM paper_trades ORDER BY updated_at")
        return [json.loads(row["payload"]) for row in rows]

    def put_briefing(self, payload: dict[str, Any]) -> None:
        briefing_id = str(payload.get("date") or payload.get("as_of") or _now()[:10])
        self._execute(
            """INSERT INTO briefings (briefing_id, created_at, payload)
               VALUES (?, ?, ?)
               ON CONFLICT(briefing_id) DO UPDATE SET
                 created_at=excluded.created_at, payload=excluded.payload""",
            (briefing_id, _now(), json.dumps(payload)),
        )

    def latest_briefing(self) -> dict[str, Any] | None:
        rows = self._fetchall(
            "SELECT payload FROM briefings ORDER BY created_at DESC LIMIT 1"
        )
        return json.loads(rows[0]["payload"]) if rows else None

    def put_daily_brief(self, payload: dict[str, Any]) -> None:
        """Persist the normalized external research brief, never raw Markdown."""
        briefing_id = str(payload.get("report_date") or payload.get("generated_at") or _now()[:10])
        self._execute(
            """INSERT INTO daily_briefs (briefing_id, fetched_at, payload)
               VALUES (?, ?, ?)
               ON CONFLICT(briefing_id) DO UPDATE SET
                 fetched_at=excluded.fetched_at, payload=excluded.payload""",
            (briefing_id, _now(), json.dumps(payload)),
        )

    def latest_daily_brief(self) -> dict[str, Any] | None:
        rows = self._fetchall(
            "SELECT payload FROM daily_briefs ORDER BY fetched_at DESC LIMIT 1"
        )
        return json.loads(rows[0]["payload"]) if rows else None

    def create_watchlist(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("watchlist name must be 1–60 characters")
        watchlist_id = _slug(name)
        if not watchlist_id:
            raise ValueError("watchlist name must contain letters or numbers")
        now = _now()
        self._execute(
            """INSERT INTO watchlists (watchlist_id, name, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(watchlist_id) DO UPDATE SET
                 name=excluded.name, updated_at=excluded.updated_at""",
            (watchlist_id, name, now, now),
        )
        return {"watchlist_id": watchlist_id, "name": name, "symbols": []}

    def list_watchlists(self) -> list[dict[str, Any]]:
        watchlists = self._fetchall(
            "SELECT watchlist_id, name, created_at, updated_at FROM watchlists ORDER BY name"
        )
        members = self._fetchall(
            "SELECT watchlist_id, symbol, note, created_at FROM watchlist_members "
            "ORDER BY created_at"
        )
        by_list: dict[str, list[dict[str, Any]]] = {}
        for member in members:
            by_list.setdefault(member["watchlist_id"], []).append(member)
        for item in watchlists:
            item["members"] = by_list.get(item["watchlist_id"], [])
            item["symbols"] = [member["symbol"] for member in item["members"]]
        return watchlists

    def add_watchlist_member(self, watchlist_id: str, symbol: str, note: str = "") -> None:
        if not self._fetchall(
            "SELECT watchlist_id FROM watchlists WHERE watchlist_id=?", (watchlist_id,)
        ):
            raise KeyError("unknown watchlist")
        self._execute(
            """INSERT INTO watchlist_members (watchlist_id, symbol, note, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(watchlist_id, symbol) DO UPDATE SET note=excluded.note""",
            (watchlist_id, symbol.upper(), note.strip()[:500], _now()),
        )

    def remove_watchlist_member(self, watchlist_id: str, symbol: str) -> None:
        self._execute(
            "DELETE FROM watchlist_members WHERE watchlist_id=? AND symbol=?",
            (watchlist_id, symbol.upper()),
        )

    def watchlists_for_symbol(self, symbol: str) -> list[str]:
        rows = self._fetchall(
            "SELECT watchlist_id FROM watchlist_members WHERE symbol=? ORDER BY watchlist_id",
            (symbol.upper(),),
        )
        return [row["watchlist_id"] for row in rows]

    def put_saved_screen(self, name: str, filters: dict[str, Any]) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("screen name must be 1–60 characters")
        screen_id = _slug(name)
        now = _now()
        self._execute(
            """INSERT INTO saved_screens
               (screen_id, name, filters, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(screen_id) DO UPDATE SET
                 name=excluded.name, filters=excluded.filters, updated_at=excluded.updated_at""",
            (screen_id, name, json.dumps(filters), now, now),
        )
        return {"screen_id": screen_id, "name": name, "filters": filters}

    def list_saved_screens(self) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT screen_id, name, filters, created_at, updated_at "
            "FROM saved_screens ORDER BY name"
        )
        for row in rows:
            row["filters"] = json.loads(row["filters"])
        return rows

    def claim_delivery(self, delivery_key: str) -> bool:
        """Atomically claim a one-time outbound delivery across ALL instances.

        Returns True if this caller won the claim (it should send), False if the
        key was already claimed (another instance — e.g. an overlapping rolling
        deploy — or an earlier boot already sent it). The INSERT ... ON CONFLICT
        DO NOTHING RETURNING is atomic in both SQLite (>=3.35) and PostgreSQL, so
        exactly one concurrent caller ever gets a returned row.
        """
        self._ensure()
        with self._connection() as connection:
            rows = connection.execute(
                self._sql(
                    "INSERT INTO delivery_log (delivery_key, created_at) "
                    "VALUES (?, ?) ON CONFLICT(delivery_key) DO NOTHING "
                    "RETURNING delivery_key"
                ),
                (delivery_key, _now()),
            ).fetchall()
        return len(rows) == 1

    def readiness(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "connected": self.initialized and self.last_error is None,
            "deploy_durable": self.deploy_durable,
            "last_error": self.last_error,
            "failure_reason": self._failure_reason,
            "retry_after_seconds": self.retry_after_seconds,
        }
