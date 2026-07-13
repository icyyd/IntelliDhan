"""Durable operational state for the personal terminal.

PostgreSQL is selected when ``DATABASE_URL``/``INTELLIDHAN_DATABASE_URL`` is
configured.  A SQLite database is the zero-infrastructure local fallback.  The
schema deliberately stores audit payloads verbatim while normalizing securities,
universe membership, watchlists, and saved screens for the discovery workflow.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = "-".join(value.strip().lower().split())
    return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-_")[:64]


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
        if self.postgres:
            with psycopg.connect(self.location, row_factory=dict_row) as connection:
                yield connection
                connection.commit()
            return
        path = Path(self.location)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

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
            self.last_error = None
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

    def list_alerts(self, limit: int = 250) -> list[dict[str, Any]]:
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

    def readiness(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "connected": self.initialized and self.last_error is None,
            "deploy_durable": self.deploy_durable,
            "last_error": self.last_error,
        }
