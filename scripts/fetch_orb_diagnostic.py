#!/usr/bin/env python3
"""Fetch a bounded, research-only Yahoo ORB diagnostic; never a three-year archive.

Run with an explicit end-exclusive date, for example::

    python scripts/fetch_orb_diagnostic.py --as-of 2026-10-06 --days 59 \
        --out-dir data/research/orb

Yahoo/yfinance history is revisable, not a point-in-time feed. No quote, broker,
execution, credential, or live-calibration interfaces are used by this script.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from importlib.metadata import version
import io
import json
import logging
import math
import os
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from intellidhan_schemas import Bar, Timeframe


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
INSTRUMENT_MAPPING = {"SPY": "SPY", "SPX": "^SPX"}
FIELDS = ("Open", "High", "Low", "Close", "Volume")
SOURCE = "yahoo_raw_research_diagnostic"


def _aware_timestamp(value: object) -> datetime:
    if pd.isna(value) or not isinstance(value, datetime):
        raise ValueError("invalid_timestamp")
    stamp = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("naive_timestamp")
    return stamp.astimezone(UTC)


def normalize_history(
    frame: pd.DataFrame, symbol: str, start: date, end: date,
    *, observed_at: datetime,
) -> tuple[list[Bar], dict]:
    """Validate every returned row without rounding, repairing, or filling it."""
    if symbol not in INSTRUMENT_MAPPING:
        raise ValueError("unsupported symbol")
    observed_at = _aware_timestamp(observed_at)
    lower = datetime.combine(start, time.min, ET).astimezone(UTC)
    upper = datetime.combine(end, time.min, ET).astimezone(UTC)
    stamps: list[datetime | None] = []
    timestamp_errors: list[str | None] = []
    for index in frame.index:
        try:
            stamps.append(_aware_timestamp(index))
            timestamp_errors.append(None)
        except (ValueError, TypeError, OverflowError) as exc:
            stamps.append(None)
            timestamp_errors.append(
                "naive_timestamp" if str(exc) == "naive_timestamp" else "invalid_timestamp"
            )
    duplicates = Counter(stamp for stamp in stamps if stamp is not None)
    invalid: list[dict] = []
    bars: list[Bar] = []
    for offset, (index, row) in enumerate(frame.iterrows()):
        stamp = stamps[offset]
        reasons = []
        if timestamp_errors[offset]:
            reasons.append(timestamp_errors[offset])
        if stamp is not None:
            if duplicates[stamp] > 1:
                reasons.append("duplicate_timestamp")
            if not lower <= stamp < upper:
                reasons.append("outside_requested_window")
            if stamp.second or stamp.microsecond or stamp.minute % 5:
                reasons.append("off_grid_timestamp")
            if stamp + timedelta(minutes=5) > min(upper, observed_at):
                reasons.append("incomplete_bar")
        values = None
        try:
            values = tuple(float(row[field]) for field in FIELDS)
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.append("missing_or_nonnumeric_ohlcv")
        if values is not None:
            if not all(math.isfinite(value) for value in values):
                reasons.append("nonfinite_ohlcv")
            else:
                opened, high, low, closed, volume = values
                if min(opened, high, low, closed) <= 0:
                    reasons.append("nonpositive_price")
                if not (low <= opened <= high and low <= closed <= high):
                    reasons.append("invalid_ohlc")
                if volume < 0:
                    reasons.append("negative_volume")
        if reasons:
            invalid.append({
                "provider_row": offset,
                "provider_timestamp": str(index),
                "reasons": sorted(set(reasons)),
            })
            continue
        assert values is not None and stamp is not None
        bars.append(Bar(
            symbol=symbol, timeframe=Timeframe.M5,
            ts_close=stamp + timedelta(minutes=5),
            open=values[0], high=values[1], low=values[2], close=values[3],
            volume=values[4], source=SOURCE,
        ))
    bars.sort(key=lambda bar: bar.ts_close)
    reason_counts = Counter(reason for item in invalid for reason in item["reasons"])
    return bars, {
        "provider_rows": len(frame), "canonical_rows": len(bars),
        "invalid_rows": len(invalid), "invalid_reason_counts": dict(reason_counts),
        "invalid_row_audit": invalid,
        "first_close_utc": bars[0].ts_close.isoformat() if bars else None,
        "last_close_utc": bars[-1].ts_close.isoformat() if bars else None,
        "observed_dates_et": sorted({bar.ts_close.astimezone(ET).date().isoformat()
                                     for bar in bars}),
    }


def collect_action_dates(frame: pd.DataFrame, start: date, end: date) -> dict:
    """Read separately fetched raw daily actions; absence is not verification."""
    columns = ("Dividends", "Stock Splits")
    dates: dict[str, set[str]] = {column: set() for column in columns}
    invalid = []
    missing = [column for column in columns if column not in frame.columns]
    observed_days: set[str] = set()
    for offset, (index, row) in enumerate(frame.iterrows()):
        reasons = []
        day = None
        try:
            day = _aware_timestamp(index).astimezone(ET).date()
            if not start <= day < end:
                reasons.append("outside_requested_window")
            else:
                observed_days.add(day.isoformat())
        except (TypeError, ValueError, OverflowError):
            reasons.append("invalid_or_naive_timestamp")
        actions = {}
        for column in columns:
            try:
                value = float(row[column])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("invalid action")
                actions[column] = value
            except (KeyError, TypeError, ValueError, OverflowError):
                reasons.append("invalid_" + column.lower().replace(" ", "_"))
        if reasons:
            invalid.append({"provider_row": offset, "provider_timestamp": str(index),
                            "reasons": reasons})
        else:
            assert day is not None
            for column, value in actions.items():
                if value > 0:
                    dates[column].add(day.isoformat())
    return {
        "status": "AVAILABLE_UNVERIFIED" if len(frame) and not invalid and not missing
        else "UNAVAILABLE_OR_INCOMPLETE",
        "provider_rows": len(frame), "missing_columns": missing,
        "invalid_rows": len(invalid), "invalid_row_audit": invalid,
        "observed_dates_et": sorted(observed_days),
        "dividend_dates": sorted(dates["Dividends"]),
        "split_dates": sorted(dates["Stock Splits"]),
        "action_dates": sorted(dates["Dividends"] | dates["Stock Splits"]),
        "completeness_verified": False,
        "point_in_time_availability_verified": False,
    }


def _query(start: date, end: date, *, daily: bool, prepost: bool) -> dict:
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "interval": "1d" if daily else "5m", "prepost": prepost,
        "auto_adjust": False, "back_adjust": False, "actions": True,
        "repair": False, "keepna": True, "rounding": False,
        "timeout": 20, "raise_errors": True,
    }


def _history(ticker: str, query: dict, ticker_factory: Callable) -> tuple[pd.DataFrame, str | None]:
    # Provider diagnostics can contain URLs/response contents. Keep only the
    # exception class; never print raw responses, cookies, prices, or metadata.
    logger = logging.getLogger("yfinance")
    was_disabled = logger.disabled
    logger.disabled = True
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return ticker_factory(ticker).history(**query), None
    except Exception as exc:
        return pd.DataFrame(), type(exc).__name__
    finally:
        logger.disabled = was_disabled


def _private_write(path: Path, payload: bytes) -> None:
    # Refuse to overwrite prior evidence or follow an existing file/symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)


def _frame_sha256(frame: pd.DataFrame) -> str:
    return hashlib.sha256(frame.to_csv(index=True).encode("utf-8")).hexdigest()


def fetch_diagnostic(
    *, as_of: date, days: int, out_dir: Path, ticker_factory: Callable | None = None,
    observed_at: datetime | None = None,
) -> dict:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 59:
        raise ValueError("days must be between 1 and 59")
    observed_at = _aware_timestamp(observed_at or datetime.now(UTC))
    if as_of > observed_at.astimezone(ET).date():
        raise ValueError("as-of cannot be a future exchange-local date")
    if ticker_factory is None:
        import yfinance as yf

        ticker_factory = yf.Ticker
    start = as_of - timedelta(days=days)
    action_start = as_of - timedelta(days=120)
    stem = f"orb-diagnostic-{as_of.isoformat()}-{days}d"
    out_dir = Path(out_dir)
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if out_dir.is_symlink() or out_dir.stat().st_mode & 0o077:
        raise ValueError("out-dir must be a private directory (mode 0700), not a symlink")
    bars_path = out_dir / f"{stem}.bars.jsonl"
    manifest_path = out_dir / f"{stem}.manifest.json"
    if bars_path.exists() or manifest_path.exists():
        raise FileExistsError("refusing to overwrite an existing diagnostic artifact")
    manifest = {
        "schema_version": "orb-diagnostic-fetch-v1", "purpose": "SHORT_WINDOW_DIAGNOSTIC",
        "live_eligible": False, "three_year_coverage": False,
        "provider": "Yahoo Finance via yfinance", "provider_version": version("yfinance"),
        "instrument_mapping": dict(INSTRUMENT_MAPPING),
        "as_of_exclusive": as_of.isoformat(), "start_inclusive": start.isoformat(),
        "requested_calendar_days": days, "fetched_at_utc": observed_at.isoformat(),
        "price_basis": "raw",
        "price_basis_details": (
            "Provider OHLC with auto_adjust=False and back_adjust=False; no local adjustment, "
            "repair, rounding, or filling. Not certified as-traded: Yahoo may split-normalize "
            "or revise historical observations."
        ),
        "source_bar_availability": "HISTORICAL_REVISED_UNVERIFIED",
        "corporate_actions_verified": False, "point_in_time_availability_verified": False,
        "timestamp_convention": "provider timezone-aware open + 5 minutes; UTC bar close",
        "source_dataframe_hash_encoding": "UTF-8 pandas.DataFrame.to_csv(index=True)",
        "assumptions": [
            "Yahoo index timestamps identify bar opens; this has not been independently verified.",
            "Five-minute history is a bounded recent-window diagnostic, not three-year evidence.",
            "Fetch time is not historical dissemination time; no point-in-time archive is available.",
            "SPY extended hours are requested; SPX is a cash-index series with no premarket proxy.",
            "Returned invalid rows are audited and excluded without repair; absent rows stay absent.",
            "Session completeness, calendar coverage, and warmup sufficiency require downstream audit.",
            "Raw Yahoo prices are an explicit diagnostic deviation from adjusted-bar promotion rules.",
            "Corporate-action absence does not prove completeness or historical availability.",
        ],
        "symbols": {},
    }
    combined: list[Bar] = []
    for symbol, ticker in INSTRUMENT_MAPPING.items():
        query = _query(start, as_of, daily=False, prepost=symbol == "SPY")
        frame, error = _history(ticker, query, ticker_factory)
        bars, audit = normalize_history(frame, symbol, start, as_of, observed_at=observed_at)
        combined.extend(bars)
        manifest["symbols"][symbol] = {
            "provider_ticker": ticker, "query": query, "provider_error_type": error,
            "source_dataframe_sha256": _frame_sha256(frame), **audit,
        }
    action_query = _query(action_start, as_of, daily=True, prepost=False)
    action_frame, action_error = _history("SPY", action_query, ticker_factory)
    action_audit = collect_action_dates(action_frame, action_start, as_of)
    manifest["spy_corporate_actions"] = {
        "provider_ticker": "SPY", "query": action_query,
        "provider_error_type": action_error,
        "source_dataframe_sha256": _frame_sha256(action_frame), **action_audit,
    }
    manifest["spy_action_dates"] = action_audit["action_dates"]
    combined.sort(key=lambda bar: (bar.symbol, bar.ts_close))
    payload = "".join(bar.model_dump_json() + "\n" for bar in combined).encode("utf-8")
    manifest.update({
        "bars_file": bars_path.name, "bars_sha256": hashlib.sha256(payload).hexdigest(),
        "bar_count": len(combined),
        "invalid_row_count": sum(row["invalid_rows"] for row in manifest["symbols"].values()),
        "status": "FETCHED_UNVERIFIED" if all(
            row["canonical_rows"] and not row["provider_error_type"]
            for row in manifest["symbols"].values()
        ) else "INCOMPLETE_OR_UNAVAILABLE",
    })
    _private_write(bars_path, payload)
    _private_write(manifest_path, (json.dumps(manifest, indent=2, allow_nan=False) + "\n").encode())
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True,
                        help="Exchange-local end-exclusive date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=59, choices=range(1, 60))
    parser.add_argument("--out-dir", type=Path, default=Path("data/research/orb"))
    args = parser.parse_args(argv)
    try:
        result = fetch_diagnostic(as_of=args.as_of, days=args.days, out_dir=args.out_dir)
    except (ValueError, OSError) as exc:
        # Only our stable error classification goes to the terminal.
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__,
                          "live_eligible": False}))
        return 2
    print(json.dumps({key: result[key] for key in (
        "status", "bar_count", "invalid_row_count", "bars_file", "bars_sha256", "live_eligible",
    )}))
    return 0 if result["status"] == "FETCHED_UNVERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
