"""TimescaleDB persistence — bars hypertable + indicator snapshots (doc 01 §3).

Off the hot path by design: writers batch; readers are backfill/replay/backtest.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from intellidhan_schemas import Bar, Timeframe

DDL = """
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS bars (
    symbol      text        NOT NULL,
    timeframe   text        NOT NULL,
    ts_close    timestamptz NOT NULL,
    open        double precision NOT NULL,
    high        double precision NOT NULL,
    low         double precision NOT NULL,
    close       double precision NOT NULL,
    volume      double precision NOT NULL,
    source      text        NOT NULL,
    inserted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, timeframe, ts_close)
);
SELECT create_hypertable('bars', 'ts_close', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS indicator_snapshots (
    symbol      text        NOT NULL,
    timeframe   text        NOT NULL,
    ts_close    timestamptz NOT NULL,
    payload     jsonb       NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts_close)
);
SELECT create_hypertable('indicator_snapshots', 'ts_close', if_not_exists => TRUE);
"""


def dsn_from_env() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD")
    if not pw:
        raise RuntimeError("POSTGRES_PASSWORD not set (see deploy/.env)")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    return f"postgresql://intellidhan:{pw}@{host}:5432/intellidhan"


class Storage:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    def init_schema(self) -> None:
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            conn.execute(DDL)

    def write_bars(self, bars: list[Bar]) -> int:
        """Idempotent upsert — replays and provider overlaps never duplicate (doc 01 §1)."""
        if not bars:
            return 0
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO bars (symbol, timeframe, ts_close, open, high, low, close, volume, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, timeframe, ts_close) DO NOTHING
                    """,
                    [
                        (b.symbol, b.timeframe.value, b.ts_close, b.open, b.high, b.low,
                         b.close, b.volume, b.source)
                        for b in bars
                    ],
                )
            conn.commit()
        return len(bars)

    def read_bars(self, symbol: str, timeframe: Timeframe) -> list[Bar]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM bars WHERE symbol=%s AND timeframe=%s ORDER BY ts_close",
                (symbol, timeframe.value),
            ).fetchall()
        return [
            Bar(
                symbol=r["symbol"], timeframe=Timeframe(r["timeframe"]), ts_close=r["ts_close"],
                open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                volume=r["volume"], source=r["source"],
            )
            for r in rows
        ]

    def write_snapshot(self, symbol: str, timeframe: str, ts_close, payload_json: str) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO indicator_snapshots (symbol, timeframe, ts_close, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (symbol, timeframe, ts_close) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (symbol, timeframe, ts_close, payload_json),
            )
            conn.commit()

    def count(self, table: str) -> int:
        if table not in {"bars", "indicator_snapshots"}:
            raise ValueError(table)
        with psycopg.connect(self._dsn) as conn:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
