#!/usr/bin/env python3
"""Run the frozen, research-only SPY/SPX opening-range pattern screen.

Input is private canonical Bar JSONL plus its source manifest, not production
alerts or execution configuration. Output never authorizes a real trade.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import hashlib
import json
import os
from pathlib import Path

from intellidhan_learning.orb_data import (
    ATR_PERIOD, SUPPORTED_END_EXCLUSIVE, SUPPORTED_START, load_orb_sessions,
)
from intellidhan_learning.orb_patterns import (
    BARRIER_ATR, EVENT_CUTOFF, LABEL_MINUTES, NARROW_OR_ATR_MAX, OPENING_WINDOWS,
    PATTERN_IDS, PRIMARY_OPENING_MINUTES, WIDE_OR_ATR_MIN, pattern_observations,
)
from intellidhan_learning.orb_report import build_report
from intellidhan_schemas import Bar

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/research/orb-pattern-protocol.v1.json"


def validate_protocol(protocol: dict) -> None:
    fixed = {
        "patterns": list(PATTERN_IDS), "symbols": ["SPY", "SPX"],
        "study_start": SUPPORTED_START.isoformat(),
        "study_end_exclusive": SUPPORTED_END_EXCLUSIVE.isoformat(),
        "primary_opening_minutes": PRIMARY_OPENING_MINUTES,
        "bar_minutes": 5, "premarket_et": ["04:00", "09:30"],
        "premarket_symbols": ["SPY"], "atr_period": ATR_PERIOD,
        "atr_method": "SMA_TRUE_RANGE", "horizon_minutes": LABEL_MINUTES,
        "barrier_prior_atr_fraction": BARRIER_ATR,
        "event_cutoff_exclusive_et": EVENT_CUTOFF.strftime("%H:%M"),
        "narrow_or_atr_max": NARROW_OR_ATR_MAX, "wide_or_atr_min": WIDE_OR_ATR_MIN,
        "live_eligible": False, "execution_authorized": False,
    }
    if any(protocol.get(key) != value for key, value in fixed.items()) or sorted([
        protocol["primary_opening_minutes"], *protocol["exploratory_opening_minutes"]
    ]) != list(OPENING_WINDOWS):
        raise ValueError("frozen protocol does not match executable study constants")


def load_archive(path: Path, manifest: dict) -> list[Bar]:
    expected = manifest.get("bars_sha256")
    if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("input hash missing or does not match source manifest")
    if manifest.get("price_basis") != "raw":
        raise ValueError("only explicitly raw, unadjusted archives are supported in v1")
    if not manifest.get("provider") or not manifest.get("instrument_mapping"):
        raise ValueError("provider and instrument mapping required")
    bars = []
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                bar = Bar.model_validate_json(line)
                if bar.ts_close.utcoffset() is None:
                    raise ValueError("naive time")
                bars.append(bar)
            except ValueError as exc:
                raise ValueError(f"invalid archive row {line_number}; repair upstream") from exc
    return bars


def _check_paths(inputs: list[Path], outputs: list[Path], overwrite: bool) -> None:
    resolved = [path.resolve() for path in outputs]
    if len(set(resolved)) != len(resolved) or set(resolved) & {
        path.resolve() for path in inputs
    }:
        raise ValueError("output paths must not alias inputs, protocol, or each other")
    for index, output in enumerate(outputs):
        for other in [*inputs, *outputs[:index]]:
            if output.exists() and other.exists() and output.samefile(other):
                raise ValueError("output paths must not be hard-linked to protected files")
    if any(path.is_symlink() for path in outputs):
        raise ValueError("output symlinks are not supported")
    if not overwrite and any(path.exists() for path in outputs):
        raise FileExistsError("results exist; use a new path or --overwrite-results explicitly")


def _write_result(path: Path, payload: str, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "w") as stream:
        stream.write(payload)


def run(input_path: Path, manifest_path: Path, out_path: Path,
        ledger_path: Path | None = None, evidence_path: Path | None = None,
        *, overwrite_results: bool = False) -> dict:
    _check_paths([input_path, manifest_path, PROTOCOL],
                 [p for p in (out_path, ledger_path, evidence_path) if p], overwrite_results)
    protocol = json.loads(PROTOCOL.read_text())
    validate_protocol(protocol)
    manifest = json.loads(manifest_path.read_text())
    bars = load_archive(input_path, manifest)
    actions = {date.fromisoformat(day) for day in manifest.get("spy_action_dates", [])}
    start = date.fromisoformat(protocol["study_start"])
    end = date.fromisoformat(protocol["study_end_exclusive"])
    observations, audit = [], {}
    for symbol in protocol["symbols"]:
        sessions, quality = load_orb_sessions(
            bars, start, end, symbol=symbol, ex_div_dates=actions,
        )
        audit[symbol] = asdict(quality)
        for session in sessions:
            for minutes in [protocol["primary_opening_minutes"],
                            *protocol["exploratory_opening_minutes"]]:
                observations.extend(pattern_observations(session, opening_minutes=minutes))
    report = build_report(observations, audit, protocol, manifest)
    report["archive_coverage_complete"] = all(
        details["complete"] == details["total_expected"] and details["total_expected"] > 0
        for details in audit.values()
    )
    report["premarket_coverage_complete"] = (
        audit["SPY"]["pm_available"] == audit["SPY"]["total_expected"]
        and audit["SPY"]["total_expected"] > 0
    )
    report["full_study_blockers"] = [
        reason for condition, reason in [
            (not report["archive_coverage_complete"], "Incomplete three-year RTH archive"),
            (not report["premarket_coverage_complete"], "Incomplete SPY premarket archive"),
            (not manifest.get("corporate_actions_verified", False),
             "Point-in-time corporate-action coverage unverified"),
            (not manifest.get("point_in_time_availability_verified", False),
             "Historical data publication/availability not verified"),
            (True, "Confirmatory contrasts and net execution/option replay not performed"),
        ] if condition
    ]
    _write_result(out_path, json.dumps(report, indent=2, default=str) + "\n", overwrite_results)
    if ledger_path:
        _write_result(ledger_path, "".join(json.dumps(row, default=str) + "\n"
                                           for row in observations), overwrite_results)
    if evidence_path:
        # Share all diagnostic cells, not a cherry-picked leaderboard. Leave
        # source bars, event prices and the full private manifest out of Git.
        evidence = {
            key: report[key] for key in (
                "protocol_id", "protocol_sha256", "requested_window", "as_of", "status",
                "three_year_test_complete", "archive_coverage_complete",
                "premarket_coverage_complete", "full_study_blockers", "limitations",
                "execution_authorized", "live_eligible", "selected_strategy",
                "observation_sha256",
            )
        }
        evidence["source"] = {key: manifest.get(key) for key in (
            "provider", "bars_sha256", "price_basis", "instrument_mapping",
            "point_in_time_availability_verified", "corporate_actions_verified",
        )}
        evidence["data_audit"] = {
            symbol: {key: value for key, value in details.items() if not key.endswith("days")}
            for symbol, details in audit.items()
        }
        evidence["cells"] = [cell for cell in report["cells"]
                             if cell["split"] == "all_available_diagnostic"]
        evidence["split_observation_counts"] = {
            split: sum(cell["session_rows"] for cell in report["cells"]
                       if cell["split"] == split)
            for split in ("development", "validation", "retrospective_test")
        }
        _write_result(evidence_path, json.dumps(evidence, indent=2, default=str) + "\n",
                      overwrite_results)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--evidence", type=Path, help="Compact aggregate evidence; no raw bar prices")
    parser.add_argument("--overwrite-results", action="store_true",
                        help="Explicitly replace result files; never inputs or the protocol")
    args = parser.parse_args(argv)
    result = run(args.input, args.manifest, args.out, args.ledger, args.evidence,
                 overwrite_results=args.overwrite_results)
    print(json.dumps({
        "status": result["status"],
        "three_year_test_complete": result["three_year_test_complete"],
        "full_study_blockers": result["full_study_blockers"],
        "data": {symbol: {k: values[k] for k in
                          ("total_expected", "complete", "missing", "invalid", "pm_available")}
                 for symbol, values in result["data_audit"].items()},
        "report": str(args.out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
