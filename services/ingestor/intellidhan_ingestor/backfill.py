"""Historical backfill + session recorder.

Usage:
    python -m intellidhan_ingestor.backfill --symbols QQQ SPY SMH TQQQ --tf 5m --days 5
    python -m intellidhan_ingestor.backfill --record fixtures/golden-sessions/qqq-complex-5m.jsonl ...

Fetches bars, sentinel-checks them (degraded series are stored but flagged),
writes to TimescaleDB, and optionally records a JSONL fixture for the replay
harness — recordings are the raw material of the determinism tests (doc 01 §7).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intellidhan_ingestor.providers import YahooProvider
from intellidhan_ingestor.sentinel import check_bars
from intellidhan_schemas import Bar, Timeframe


def write_recording(path: Path, bars: list[Bar]) -> None:
    """One JSON object per line, globally sorted by ts_close for replay ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for b in sorted(bars, key=lambda b: (b.ts_close, b.symbol)):
            f.write(b.model_dump_json() + "\n")


def read_recording(path: Path) -> list[Bar]:
    return [Bar.model_validate_json(line) for line in path.read_text().splitlines() if line]


async def backfill(
    symbols: list[str], timeframe: Timeframe, days: int,
    record_path: Path | None, persist: bool,
) -> list[Bar]:
    provider = YahooProvider()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    all_bars: list[Bar] = []
    for symbol in symbols:
        bars = await provider.get_bars(symbol, timeframe, start, end)
        report = check_bars(symbol, bars)
        status = report.quality.value
        print(f"{symbol}: {len(bars)} bars [{status}]"
              + (f" issues={report.issues[:3]}" if report.issues else ""))
        all_bars.extend(bars)
    if persist:
        from intellidhan_ingestor.storage import Storage

        storage = Storage()
        storage.init_schema()
        n = storage.write_bars(all_bars)
        print(f"persisted {n} bars to TimescaleDB")
    if record_path is not None:
        write_recording(record_path, all_bars)
        print(f"recorded {len(all_bars)} bars -> {record_path}")
    return all_bars


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--tf", default="5m", choices=[t.value for t in Timeframe])
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--record", type=Path, default=None)
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args(argv)
    asyncio.run(
        backfill(args.symbols, Timeframe(args.tf), args.days, args.record, not args.no_persist)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
