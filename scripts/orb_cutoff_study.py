"""Offline, paired study of one change: stop new entries at 11:15 instead of 10:30 ET."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, time
import hashlib
import json
from pathlib import Path

from orb_regime_study import (SYMBOLS, VARIANTS, iter_mornings, metrics, paired_interval,
                              sniper_paths, study_inputs)
from paper_trading.strategy import _evaluate

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "docs/evidence/orb-sniper-v2-2026-11-19.json"
CONTROL_END, EXTENDED_END = time(10, 30), time(11, 15)


def historical_1115(symbol, bars, quote, now, *, last_exit_at):
    """Offline setup reconstruction only. Cannot accept a live price or return an ENTRY."""
    if quote is not None:
        raise ValueError("Historical window comparison uses completed bars, never a live quote")
    return _evaluate(symbol, bars, None, now, sniper=True, last_exit_at=last_exit_at,
                     entry_cutoff=EXTENDED_END)


def verify_prefix(control, extended):
    for arm in VARIANTS:
        trades = extended[arm]
        if (any(t["entry"].time() >= EXTENDED_END for t in trades) or
                [t for t in trades if t["entry"].time() < CONTROL_END] != control[arm]):
            raise ValueError(f"Cutoff experiment changed the reference path or exceeded 11:15: {arm}")


def recost(series, cost):
    return [[{**t, "net_bps": t["gross_bps"] - cost} for t in day] for day in series]


def summarize(control, extended):
    # Index within the whole session (not within the late subset): a late
    # first is only a first when this arm had no earlier trade that day.
    marked = [[(t, i) for i, t in enumerate(day) if t["entry"].time() >= CONTROL_END] for day in extended]

    def cohort(predicate):
        return [[t for t, i in day if predicate(t, i)] for day in marked]

    added = cohort(lambda t, i: True)
    first = cohort(lambda t, i: i == 0)
    repeat = cohort(lambda t, i: i > 0)
    bins = {}
    for lo, hi in ((CONTROL_END, time(10, 45)), (time(10, 45), time(11)), (time(11), EXTENDED_END)):
        series = cohort(lambda t, i: lo <= t["entry"].time() < hi)
        bins[f"{lo:%H:%M}–{hi:%H:%M}"] = concise(series)
    return {
        "control": metrics(control), "extended": metrics(extended), "added": metrics(added),
        "newly_active_sessions": sum(not c and bool(e) for c, e in zip(control, extended, strict=True)),
        "added_first": concise(first), "added_repeat": concise(repeat), "added_by_entry_ET": bins,
        "paired_extended_minus_control": {f"cost_{c}bps": paired_interval(recost(extended,c), recost(control,c))
                                           for c in (2, 5)},
    }


def concise(series):
    m = metrics(series)
    return {key: m[key] for key in ("trades", "flat_sessions", "win_pct", "mean_net_bps_per_trade",
                                    "total_net_bps", "net_total_at_5bps_round_trip", "by_symbol")}


def run(cache, *, reference=CONTROL):
    left, start, split, right = date(2023,10,19), date(2023,11,20), date(2026,1,2), date(2026,11,19)
    raw, vix, days, paths = study_inputs(cache, left, right)
    counts, excluded, book = defaultdict(Counter), [], {}
    for day, inputs, factors in iter_mornings(raw, vix, days, start, excluded, counts):
        control = sniper_paths(inputs, factors)
        extended = sniper_paths(inputs, factors, evaluator=historical_1115, entry_indices=range(5, 21))
        verify_prefix(control, extended)
        book[day] = (control, extended)
    prior = json.loads(reference.read_text())
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    if (prior["input_sha256"] != hashes or prior["missing_mornings_excluded"] != excluded
            or prior["usable_sessions"] != len(book) or prior["factor_symbol_session_counts"] != dict(counts)):
        raise ValueError("Source files/session and feature coverage do not match the reference study")
    periods = {}
    for label, subset in (("development", [d for d in book if d < split]),
                          ("held_later", [d for d in book if d >= split])):
        arms = {arm: summarize([book[d][0][arm] for d in subset], [book[d][1][arm] for d in subset])
                for arm in VARIANTS}
        if any(arms[arm]["control"] != prior[label]["arms"][arm] for arm in VARIANTS):
            raise ValueError("10:30 control no longer reproduces published V2 aggregates")
        periods[label] = {"from": subset[0].isoformat(), "through": subset[-1].isoformat(),
                          "sessions": len(subset), "arms": arms}
    return {
        "study": "ORB_RETEST_SNIPER_V2_PAIRED_1030_VS_1115", "evidence": "UNDERLYING_ONLY",
        "live_eligible": False, "symbols": list(SYMBOLS), "input_sha256": hashes,
        "control_reference": {"file": reference.name, "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
                              "all_arms_metrics_match": True, "all_session_early_trade_paths_match": True},
        "usable_sessions": len(book), "expected_sessions": len(book)+len(excluded),
        "missing_mornings_excluded": excluded, "factor_symbol_session_counts": counts,
        "method": {**prior["method"],
                   "only_difference": "Last new-entry bar: 10:25 ET control, 11:10 ET extended. Both flatten by the 11:30 open.",
                   "rearm": "Neither arm has a count quota; after each exit require a full new 5m inside-range bar, fresh breakout/retest and trigger before that arm's own cutoff.",
                   "paired": "Extended minus same arm at 10:30 on every session, including flat days. 2,000 five-session moving-block bootstrap resamples. Same fixed seed at 2 and 5 bps round trip.",
                   "added": "10:30 through 11:10 entry starts only, even when a prior trade exits later; first/repeat determined by sequence within the extended arm. Bins have exclusive upper bounds.",
                   "scope": "One additional cutoff, not parameter optimization. Previously inspected periods; intervals descriptive, not simultaneous or independent confirmation. SPY/QQQ only. No SPX, option chain or brokerage orders."},
        **periods,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT/"data/orb-study")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(run(args.cache), allow_nan=False, indent=2) + "\n"
    if args.output:
        # Do not accidentally replace the published baseline with a different study.
        if args.output.resolve() in {CONTROL, CONTROL.with_name("orb-regime-2026-11-19.json")}:
            parser.error("Refusing to replace an original evidence file")
        args.output.write_text(result)
    else:
        print(result, end="")
