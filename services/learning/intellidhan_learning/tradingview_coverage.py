"""Audit exported COV1 marker metadata, never strategy performance or prices."""

from __future__ import annotations

import csv
from datetime import date, datetime, time
import hashlib
import io
import json
import os
from pathlib import Path
import re

from intellidhan_learning.orb_data import (
    ET, SUPPORTED_END_EXCLUSIVE, SUPPORTED_START, _get_xnys_calendar,
    _schedule_days, _schedule_digest, _schedule_source_version,
)

FIELDS = frozenset("sym day r rf rl rg p pf pl pg end warm".split())
SOURCE_IDS = {"AMEX:SPY", "BATS:SPY", "SP:SPX", "SP_DLY:SPX"}
END_REASONS = {"RTH_END", "NEXT_DAY", "WINDOW_END", "DATA_END"}
LIMITATIONS = [
    "Only self-reported COV1 session-count, timestamp and gap metadata are audited.",
    "No raw OHLCV, prices, feed entitlement, corporate actions or ATR warmup are validated.",
    "TradingView symbol IDs identify the chart source, not independently verified feed provenance.",
    "SP_DLY:SPX is a separately labelled delayed S&P index feed, not realtime SP:SPX.",
    "Marker orders are accounting devices; all exported performance and fills are meaningless.",
    "No strategy effectiveness, option profitability or execution eligibility is established.",
]


def _integer(value: str, field: str, *, nullable: bool = False) -> int | None:
    if nullable and value == "na":
        return None
    if not re.fullmatch(r"0|[1-9][0-9]{0,15}", value):
        raise ValueError(f"invalid COV1 integer: {field}")
    return int(value)


def _identity(fields: dict) -> date:
    if fields["sym"] not in SOURCE_IDS:
        raise ValueError("probe record requires an allowlisted cash SPY/SPX chart source ID")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", fields["day"]):
        raise ValueError("invalid probe ISO day")
    day = date.fromisoformat(fields["day"])
    if not SUPPORTED_START <= day < SUPPORTED_END_EXCLUSIVE:
        raise ValueError("probe day outside frozen study window")
    return day


def _parse_begin(signal: str) -> tuple[date, str]:
    parts = signal.split("|")
    if len(parts) != 3 or parts[0] != "BEGIN1":
        raise ValueError("invalid BEGIN1 record shape")
    fields = {}
    for part in parts[1:]:
        if part.count("=") != 1:
            raise ValueError("invalid BEGIN1 key/value")
        key, value = part.split("=")
        if key in fields or key not in {"sym", "day"}:
            raise ValueError("duplicate or unknown BEGIN1 field")
        fields[key] = value
    if set(fields) != {"sym", "day"}:
        raise ValueError("BEGIN1 requires exactly source and day")
    return _identity(fields), fields["sym"]


def parse_record(signal: str) -> dict:
    """Reject malformed or future schemas rather than interpreting them loosely."""
    parts = signal.split("|")
    if parts[0] != "COV1" or len(parts) != len(FIELDS) + 1:
        raise ValueError("invalid COV1 record shape")
    fields = {}
    for part in parts[1:]:
        if part.count("=") != 1:
            raise ValueError("invalid COV1 key/value")
        key, value = part.split("=")
        if key in fields or key not in FIELDS:
            raise ValueError("duplicate or unknown COV1 field")
        fields[key] = value
    if set(fields) != FIELDS:
        raise ValueError("missing COV1 fields")
    day = _identity(fields)
    row = {**fields, "day": day, "symbol": fields["sym"].split(":")[1]}
    for field in ("r", "rf", "rl", "rg", "warm", "p", "pf", "pl", "pg"):
        row[field] = _integer(fields[field], field, nullable=field in {"p", "pf", "pl", "pg"})
    if not 1 <= row["r"] <= 10000 or row["rg"] not in {0, 1}:
        raise ValueError("invalid COV1 regular-session count/flag")
    if row["end"] not in END_REASONS:
        raise ValueError("invalid COV1 end reason")
    pm_fields = (row["p"], row["pf"], row["pl"], row["pg"])
    if row["symbol"] == "SPX":
        if pm_fields != (None, None, None, None):
            raise ValueError("SPX premarket metadata must all be na")
    elif row["p"] is None or not 0 <= row["p"] <= 10000 or row["pg"] not in {0, 1}:
        raise ValueError("invalid SPY premarket count/flag")
    elif (row["p"] == 0 and pm_fields[1:] != (None, None, 1)) or (
        row["p"] > 0 and (row["pf"] is None or row["pl"] is None)
    ):
        raise ValueError("inconsistent SPY premarket metadata")
    return row


def read_probe_csv(path: Path) -> tuple[list[dict], dict]:
    """Only named Type/Signal columns are trusted; other numeric results are ignored."""
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
    columns = reader.fieldnames or []
    if len(columns) != len(set(columns)) or not {"Type", "Signal"} <= set(columns):
        raise ValueError("expected a TradingView Trades CSV with unique Type and Signal columns")
    records, days, sources, entries = [], set(), set(), {}
    for number, values in enumerate(reader, 2):
        if None in values or any(value is None for value in values.values()):
            raise ValueError(f"malformed CSV row {number}")
        signal, kind = values["Signal"], values["Type"]
        if re.fullmatch(r"Entry long", kind, re.IGNORECASE) and signal.startswith("BEGIN1|"):
            day, source = _parse_begin(signal)
            if day in entries:
                raise ValueError("duplicate BEGIN1 day")
            entries[day] = source
            sources.add(source)
            continue
        if not re.fullmatch(r"Exit long", kind, re.IGNORECASE) or not signal.startswith("COV1|"):
            raise ValueError(f"unrelated or unclosed probe trade at row {number}")
        row = parse_record(signal)
        if row["day"] in days:
            raise ValueError("duplicate COV1 day")
        days.add(row["day"])
        sources.add(row["sym"])
        records.append(row)
    if not records or len(sources) != 1:
        raise ValueError("CSV requires COV1 exits from exactly one source/instrument")
    if set(entries) != days:
        raise ValueError("BEGIN1 entries and COV1 exits must pair one-to-one by day/source")
    return records, {"file_name": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                     "chart_source_id": records[0]["sym"]}


def _epoch(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def audit_files(paths: list[Path], *, calendar=None) -> dict:
    if not paths:
        raise ValueError("at least one input CSV is required")
    calendar = calendar if calendar is not None else _get_xnys_calendar(
        SUPPORTED_START, SUPPORTED_END_EXCLUSIVE,
    )
    schedule = _schedule_days(calendar, SUPPORTED_START, SUPPORTED_END_EXCLUSIVE)
    expected = {item.day: item for item in schedule}
    reports, sources = {}, []
    for path in paths:
        rows, source = read_probe_csv(path)
        symbol = rows[0]["symbol"]
        if symbol in reports:
            raise ValueError("supply only one CSV per symbol; duplicate/mixed source exports rejected")
        sources.append(source)
        by_day = {row["day"]: row for row in rows}
        missing = sorted(set(expected) - set(by_day))
        valid, invalid, pm_valid, pm_invalid, end_counts = [], [], [], [], {}
        for day, row in sorted(by_day.items()):
            end_counts[row["end"]] = end_counts.get(row["end"], 0) + 1
            reasons, pm_reasons = [], []
            session = expected.get(day)
            if session is None:
                reasons.append("not_an_exchange_session")
            else:
                for failed, reason in (
                    (row["r"] != len(session.rth_grid), "rth_count_mismatch"),
                    (row["rf"] != _epoch(session.open_et), "rth_first_open_mismatch"),
                    (row["rl"] != _epoch(session.close_et), "rth_last_close_mismatch"),
                    (row["rg"] != 0, "rth_gap_or_malformed_bar"),
                    (row["end"] == "DATA_END", "provisional_data_end"),
                ):
                    if failed:
                        reasons.append(reason)
                if symbol == "SPY":
                    pre_open = datetime.combine(day, time(4), tzinfo=ET)
                    for failed, reason in (
                        (row["p"] != len(session.pre_grid), "premarket_count_mismatch"),
                        (row["pf"] != _epoch(pre_open), "premarket_first_open_mismatch"),
                        (row["pl"] != _epoch(session.open_et), "premarket_last_close_mismatch"),
                        (row["pg"] != 0, "premarket_gap_or_malformed_bar"),
                    ):
                        if failed:
                            pm_reasons.append(reason)
            if reasons:
                invalid.append({"day": day.isoformat(), "reasons": reasons})
            else:
                valid.append(day.isoformat())
            if symbol == "SPY":
                if session is None:
                    pm_reasons.append("not_an_exchange_session")
                if pm_reasons:
                    pm_invalid.append({"day": day.isoformat(), "reasons": pm_reasons})
                else:
                    pm_valid.append(day.isoformat())
        reports[symbol] = {
            "chart_source_id": source["chart_source_id"],
            "expected_sessions": len(expected), "observed_sessions": len(rows),
            "expected_rth_bars": sum(len(item.rth_grid) for item in schedule),
            "reported_rth_bars": sum(row["r"] for row in rows),
            "first_observed_day": min(by_day).isoformat(),
            "last_observed_day": max(by_day).isoformat(),
            "complete_rth_metadata_days": valid,
            "missing_days": [day.isoformat() for day in missing], "invalid_days": invalid,
            "rth_metadata_complete": not missing and not invalid,
            "premarket": {"applicable": symbol == "SPY", "complete_metadata_days": pm_valid,
                          "invalid_days": pm_invalid,
                          "missing_days": [day.isoformat() for day in missing] if symbol == "SPY" else [],
                          "metadata_complete": not missing and not pm_invalid if symbol == "SPY" else None},
            "end_reason_counts": end_counts,
            "warm_observed_min": min(row["warm"] for row in rows),
            "warm_observed_max": max(row["warm"] for row in rows),
            "warmup_validated": False,
        }
    return {
        "schema": "tradingview-coverage-audit.v1", "coverage_metadata_only": True,
        "study_start": SUPPORTED_START.isoformat(),
        "study_end_exclusive": SUPPORTED_END_EXCLUSIVE.isoformat(),
        "calendar": _schedule_source_version(), "schedule_sha256": _schedule_digest(schedule),
        "sources": sources, "instruments": reports, "limitations": LIMITATIONS,
        "both_instruments_supplied": set(reports) == {"SPY", "SPX"},
        "three_year_test_complete": False, "live_eligible": False, "execution_authorized": False,
    }


def write_audit(paths: list[Path], output: Path) -> dict:
    """Write a private new result; existing files, aliases and symlinks are protected."""
    if output.is_symlink() or output.exists() or any(
        output.resolve() == path.resolve() for path in paths
    ):
        raise FileExistsError("output must be a new path and cannot alias input")
    report = audit_files(paths)
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(output, flags, 0o600), "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return report
