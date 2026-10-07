"""Descriptive ORB screen. Never ranks winners or authorizes execution.

Pattern frequency is not a trading win rate. Confidence intervals below are
pointwise exploratory intervals, not proof of significance after a search.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date

import numpy as np

SUCCESS = {"FAVORABLE_FIRST", "CONTAINED"}
KNOWN = SUCCESS | {"ADVERSE_FIRST", "NEITHER", "BREACHED"}


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def split_for_day(day: str, protocol: dict) -> str:
    if not protocol["study_start"] <= day < protocol["study_end_exclusive"]:
        raise ValueError("observation outside protocol date window")
    if day < protocol["validation_start"]:
        return "development"
    if day < protocol["historical_test_start"]:
        return "validation"
    return "retrospective_test"


def summarize(rows: list[dict], *, resamples: int = 1000) -> dict:
    """One pattern/symbol/window, one row per session; no independent-bar claims."""
    ordered = sorted(rows, key=lambda row: row["session_date"])
    if len({row["session_date"] for row in rows}) != len(rows):
        raise ValueError("summary must contain one pattern observation per session")
    statuses = Counter(row["status"] for row in rows)
    observed = [row for row in rows if row["status"] == "OBSERVED"]
    outcomes = Counter(row["outcome"] for row in observed)
    known = sum(outcomes[key] for key in KNOWN)
    successes = sum(outcomes[key] for key in SUCCESS)
    ambiguous = outcomes["AMBIGUOUS"]
    interval = None
    # Preserve no-event days in the blocks: sparse event clusters are not IID trades.
    if known >= 10 and len(ordered) >= 20 and resamples > 0:
        values = np.array([
            [int(row["status"] == "OBSERVED" and row["outcome"] in SUCCESS),
             int(row["status"] == "OBSERVED" and row["outcome"] in KNOWN)]
            for row in ordered
        ])
        rng = np.random.default_rng(1729)
        estimates = []
        n = len(values)
        for _ in range(resamples):
            starts = rng.integers(0, n - 4, size=(n + 4) // 5)
            indices = (starts[:, None] + np.arange(5)).flatten()[:n]
            wins, count = values[indices].sum(axis=0)
            if count:
                estimates.append(wins / count)
        if estimates:
            interval = [round(float(x), 4) for x in np.quantile(estimates, [.025, .975])]
    total = known + ambiguous
    return {
        "session_rows": len(rows),
        "statuses": dict(sorted(statuses.items())),
        "ineligible_reasons": dict(sorted(Counter(
            row.get("reason", "unknown") for row in rows if row["status"] == "INELIGIBLE"
        ).items())),
        "observed_events": len(observed),
        "event_frequency_of_available_sessions": len(observed) / len(rows) if rows else None,
        "outcomes": dict(sorted(outcomes.items())),
        "known_outcomes": known,
        "success_fraction_known": successes / known if known else None,
        "success_fraction_ambiguity_bounds": (
            [successes / total, (successes + ambiguous) / total] if total else None
        ),
        "pointwise_block_bootstrap_95pct": interval,
        "uncertainty_note": "Exploratory, five-session blocks, not multiplicity-adjusted; sparse/constant samples may give unreliable intervals.",
        "trading_win_rate": None,
        "net_profit": None,
    }


def build_report(observations: list[dict], audit: dict, protocol: dict,
                 provenance: dict, *, resamples: int = 1000) -> dict:
    """Keep all cells and fixed time splits; no leaderboard or test-data tuning."""
    grouped: dict[tuple, list] = defaultdict(list)
    seen = set()
    for row in observations:
        key = (row["symbol"], row["opening_minutes"], row["pattern_id"])
        identity = (*key, row["session_date"])
        if identity in seen:
            raise ValueError("duplicate pattern observation")
        seen.add(identity)
        if row["symbol"] not in protocol["symbols"]:
            raise ValueError("unregistered symbol")
        if row["pattern_id"] not in protocol["patterns"]:
            raise ValueError("unregistered pattern")
        split = split_for_day(row["session_date"], protocol)
        grouped[(*key, split)].append(row)
        grouped[(*key, "all_available_diagnostic")].append(row)
    cells = []
    for symbol in protocol["symbols"]:
        for minutes in [protocol["primary_opening_minutes"],
                        *protocol["exploratory_opening_minutes"]]:
            for pattern in protocol["patterns"]:
                for split in ["development", "validation", "retrospective_test",
                              "all_available_diagnostic"]:
                    cells.append({
                        "symbol": symbol, "opening_minutes": minutes, "pattern_id": pattern,
                        "split": split, "primary": minutes == protocol["primary_opening_minutes"],
                        **summarize(grouped[(symbol, minutes, pattern, split)],
                                    resamples=resamples),
                    })
    return {
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": fingerprint(protocol),
        "requested_window": [protocol["study_start"], protocol["study_end_exclusive"]],
        "as_of": date.fromisoformat(protocol["study_end_exclusive"]).isoformat(),
        "status": "DESCRIPTIVE_RESEARCH_ONLY",
        "three_year_test_complete": False,
        "completeness_note": "A coverage audit and descriptive screen do not complete the planned confirmatory/after-cost study.",
        "execution_authorized": False,
        "live_eligible": False,
        "selected_strategy": None,
        "limitations": [
            "Underlying price-path labels, not orders, options returns, or trading win rates.",
            "SPY and SPX reported separately; they are correlated and are not independent replications.",
            "Three OR durations and 12 patterns create 72 asset-level cells, not 72 independent edges.",
            "All-available cells are diagnostics only; never select parameters on historical test results.",
            "Pointwise intervals do not correct for searching many hypotheses; no significance claim.",
            "Retrospective test data has prior project exposure; fresh prospective confirmation required.",
        ],
        "data_audit": audit,
        "provenance": provenance,
        "observation_sha256": fingerprint(observations),
        "cells": cells,
    }
