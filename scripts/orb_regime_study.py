"""Frozen underlying-only ORB ablation. Raw licensed bars are never an artifact."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from functools import lru_cache
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import fmean

import exchange_calendars as xcals

from paper_trading.ledger import ET
from paper_trading.regime import RULE_VERSION, regime_context
from paper_trading.strategy import evaluate

SYMBOLS = ("SPY", "QQQ")
VARIANTS = ("baseline", "rvol", "vix", "combined")
FIVE = timedelta(minutes=5)


def read_opend(path):
    """OpenD RTH history is right-labeled; keep partial days visible, never pad."""
    days = defaultdict(list)
    seen = set()
    with path.open() as file:
        for row in csv.DictReader(file):
            end = datetime.strptime(row["time_key"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
            if end in seen or end.minute % 5 or end.second or not time(9, 35) <= end.time() <= time(16):
                raise ValueError("Duplicate/misaligned or non-end-labeled RTH history")
            seen.add(end)
            bar = {k: float(row[k]) for k in ("open", "high", "low", "close", "volume")}
            if not all(math.isfinite(v) for v in bar.values()) or bar["volume"] < 0 or not (
                0 < bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]
            ):
                raise ValueError("Invalid OHLCV")
            days[end.date()].append(dict(bar, started_at=end-FIVE))
    return {day: sorted(rows, key=lambda bar: bar["started_at"]) for day, rows in days.items()}


def read_vix(path):
    result = {}
    with path.open(encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            day = datetime.strptime(row["DATE"], "%m/%d/%Y").date()
            close = float(row["CLOSE"])
            if day in result or not math.isfinite(close) or close <= 0:
                raise ValueError("Duplicate or invalid Cboe VIX history")
            result[day] = {"session_date": day.isoformat(), "source": "CBOE_DAILY_HISTORY",
                           "close": close, "available_at": datetime.combine(day+timedelta(days=1), time(), ET)}
    return result


def fill_path(signal, rows, *, cost_bps=2.0):
    """Stop entry on next candle. Both-hit candles take loss; EMA uses next open."""
    bar = rows[0]
    call = signal["direction"] == "CALL"
    trigger, stop, target = (signal[k] for k in ("trigger", "stop", "target"))
    crossed = bar["high"] > trigger if call else bar["low"] < trigger
    if not crossed or (bar["open"] <= stop or bar["open"] >= target if call
                       else bar["open"] >= stop or bar["open"] <= target):
        return None
    entry = max(bar["open"], trigger) if call else min(bar["open"], trigger)
    # Tick crossing approximated by boundary + round-trip cost, not a broker fill.
    entered = bar["started_at"]
    ema = signal["evidence"]["ema9"]
    pending = False
    for i, row in enumerate(rows):
        at, op = row["started_at"], row["open"]
        if pending or at-entered >= timedelta(minutes=60) or at.time() >= time(11, 30):
            price, reason = op, "EMA9" if pending else "TIME"
            end = at
            break
        hit_stop = row["low"] <= stop if call else row["high"] >= stop
        hit_target = row["high"] >= target if call else row["low"] <= target
        if hit_stop:  # adverse precedence on ambiguous OHLC, including entry bar
            price = min(stop, op) if call else max(stop, op)
            # If later bar opens beyond target, first observable price is the open.
            if i and (op >= target if call else op <= target):
                price, reason = target, "TARGET"
            else:
                reason = "STOP"
            end = at+FIVE
            break
        if hit_target:
            reason, price, end = "TARGET", target, at+FIVE
            break
        ema = 0.2*row["close"] + 0.8*ema
        pending = row["close"] < ema if call else row["close"] > ema
    else:
        raise ValueError("Missing exit coverage")
    gross = (1 if call else -1) * (price-entry) / entry * 10000
    return {"symbol": signal["symbol"], "entry": entered, "exit": end, "reason": reason,
            "gross_bps": gross, "net_bps": gross-cost_bps}


def replay_symbol(symbol, warmup, session):
    hi, lo = max(b["high"] for b in session[:3]), min(b["low"] for b in session[:3])
    for i in range(5, 12):
        row = session[i-1]  # A valid entry requires a just-completed retest.
        if not (row["low"] <= hi < row["close"] or row["high"] >= lo > row["close"]):
            continue
        now = session[i]["started_at"]
        result = evaluate(symbol, warmup+session[:i], None, now)
        if result["status"] == "BLOCKED":
            raise ValueError("Baseline evaluation blocked: "+result["reason"])
        signal = result["signal"]
        if signal:
            return fill_path(signal, session[i:])  # only the first setup is permitted
    return None


def portfolio(candidates, variant):
    selected = []
    for row in sorted(candidates, key=lambda v: (v["entry"], SYMBOLS.index(v["symbol"]))):
        if variant != "baseline" and row[variant] != "PASS":
            continue
        # Disallow time ties at exit as intrabar fill order is unknown.
        if not selected or row["entry"] > selected[-1]["exit"]:
            selected.append(row)
    return selected


def metrics(series):
    vals = [v["net_bps"] for day in series for v in day]
    days = [sum(v["net_bps"] for v in day) for day in series]
    peak = value = drawdown = 0.
    for change in days:
        value += change
        peak = max(peak, value)
        drawdown = max(drawdown, peak-value)
    def rnd(x):
        return round(x, 3)
    by_symbol = {}
    for symbol in SYMBOLS:
        sv = [v["net_bps"] for day in series for v in day if v["symbol"] == symbol]
        by_symbol[symbol] = {"trades": len(sv), "net_bps": rnd(sum(sv)),
                             "mean_net_bps": rnd(fmean(sv)) if sv else None}
    return {
        "sessions": len(days), "trades": len(vals), "flat_sessions": sum(not d for d in series),
        "repeat_entry_sessions": sum(len(day) > 1 for day in series),
        "maximum_entries_one_session": max(map(len, series), default=0),
        "green_sessions": sum(d > 0 for d in days), "red_sessions": sum(d < 0 for d in days),
        "win_pct": rnd(100*sum(v > 0 for v in vals)/len(vals)) if vals else None,
        "mean_net_bps_per_trade": rnd(fmean(vals)) if vals else None,
        "mean_net_bps_per_session": rnd(fmean(days)) if days else None,
        "total_net_bps": rnd(sum(vals)), "max_drawdown_daily_additive_bps": rnd(drawdown),
        "net_total_at_5bps_round_trip": rnd(sum(vals)-3*len(vals)),
        "by_symbol": by_symbol,
        "exit_counts": dict(Counter(v["reason"] for day in series for v in day)),
    }


def paired_interval(a, b):
    """Moving-block bootstrap of session differences, including paired flat days."""
    diffs = [sum(v["net_bps"] for v in aa)-sum(v["net_bps"] for v in bb) for aa, bb in zip(a, b, strict=True)]
    n = len(diffs)
    if n < 10:
        return None
    rng = random.Random(20261119)
    estimates = []
    for _ in range(2000):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n-4)
            sample.extend(diffs[start:start+5])
        estimates.append(fmean(sample[:n]))
    estimates.sort()
    return {"mean_bps_per_session": round(fmean(diffs), 3), "paired_95pct_block5_bps": [round(estimates[49], 3), round(estimates[1949], 3)]}


def sniper_paths(inputs, factors, *, evaluator=None, entry_indices=range(5, 12)):
    """Independent causal path per arm. Use the runner's actual V2 rearm rule."""
    if evaluator is None:
        from paper_trading.strategy import evaluate_sniper as evaluator

    @lru_cache(maxsize=None)
    def opportunity(symbol, i, last_exit):
        warm, session = inputs[symbol]
        row = session[i-1]
        hi, lo = max(b["high"] for b in session[:3]), min(b["low"] for b in session[:3])
        if not (row["low"] <= hi < row["close"] or row["high"] >= lo > row["close"]):
            return None
        decision = evaluator(symbol, warm+session[:i], None, session[i]["started_at"], last_exit_at=last_exit)
        if decision["status"] == "BLOCKED":
            raise ValueError("V2 evaluation blocked: "+decision["reason"])
        if decision["signal"]:
            return fill_path(decision["signal"], session[i:])
        return None

    arms = {}
    for variant in VARIANTS:
        trades = []
        last_exit = None
        for i in entry_indices:
            for symbol in SYMBOLS:
                if last_exit is not None and inputs[symbol][1][i]["started_at"] <= last_exit:
                    continue
                if variant != "baseline" and factors[symbol][variant] != "PASS":
                    continue
                trade = opportunity(symbol, i, last_exit)
                if trade:
                    trades.append({**trade, **factors[symbol]})
                    last_exit = trade["exit"]
                    break
        arms[variant] = trades
    return arms


def study_inputs(cache, left, right):
    paths = {sym: cache/f"{sym}_{left}_{right}.csv" for sym in SYMBOLS}
    vix_file = cache/"VIX_History.csv"
    raw = {sym: read_opend(path) for sym, path in paths.items()}
    vix = read_vix(vix_file)
    cal = xcals.get_calendar("XNYS")
    days = [d.date() for d in cal.sessions if left <= d.date() <= right]
    return raw, vix, days, [*paths.values(), vix_file]


def iter_mornings(raw, vix, days, start, excluded, factor_counts):
    """Identical, strictly trailing feature and coverage inputs for the frozen replays."""
    for idx, day in enumerate(days):
        if day < start:
            continue
        morning = datetime.combine(day, time(9, 30), ET)
        expected = [morning+i*FIVE for i in range(25)]  # includes 11:30 exit open
        if idx < 3 or any([b["started_at"] for b in raw[s].get(day, [])[:25]] != expected for s in SYMBOLS):
            excluded.append(day.isoformat())
            continue
        factors, inputs = {}, {}
        for symbol in SYMBOLS:
            history = raw[symbol]
            warm = [b for d in days[max(0, idx-16):idx] for b in history.get(d, [])][-988:]
            if len(warm) < 15:
                raise ValueError("Insufficient indicator warmup")
            bars = history[day]
            inputs[symbol] = (warm, bars)
            # No intraday price from the future goes into any entry condition.
            openings = [b for d in days[max(0, idx-20):idx] for b in history.get(d, [])[:3]] + bars[:3]
            context = regime_context(symbol, openings, morning+3*FIVE, vix=vix.get(days[idx-1]), volume_source="OPEND_RTH_QFQ_5M")
            for key in ["rvol", "vix", "gex"]:
                factor_counts[key][context[key]["status"]] += 1
            factors[symbol] = dict(rvol=context["rvol"]["status"], vix=context["vix"]["status"], combined=context["combined"])
        yield day, inputs, factors


def run(cache, left, right, start, split, *, sniper=False):
    if not left < start < split <= right:
        raise ValueError("Need warmup, development and held-later dates in order")
    raw, vix, days, paths = study_inputs(cache, left, right)
    book, excluded = {}, []
    factor_counts = defaultdict(Counter)
    for day, inputs, factors in iter_mornings(raw, vix, days, start, excluded, factor_counts):
        candidates = []
        if not sniper:
            for symbol in SYMBOLS:
                trade = replay_symbol(symbol, *inputs[symbol])
                if trade:
                    candidates.append({**trade, **factors[symbol]})
        book[day] = sniper_paths(inputs, factors) if sniper else {name: portfolio(candidates, name) for name in VARIANTS}
        # Transport diagnostic only: the forward observer can see actual baseline
        # fills, not alternate trades opened by a hypothetical free position.
        book[day]["matched_baseline_combined"] = [t for t in book[day]["baseline"] if t["combined"] == "PASS"]
    report = {}
    for label, subset in [("development", [d for d in book if d < split]), ("held_later", [d for d in book if d >= split])]:
        if not subset:
            raise ValueError("Empty chronological period")
        series = {v: [book[d][v] for d in subset] for v in VARIANTS}
        report[label] = {"from": subset[0].isoformat(), "through": subset[-1].isoformat(),
                         "arms": {v: metrics(series[v]) for v in VARIANTS},
                         "matched_baseline_combined_diagnostic": metrics([book[d]["matched_baseline_combined"] for d in subset]),
                         "paired_vs_baseline": {v: paired_interval(series[v], series["baseline"]) for v in VARIANTS[1:]},
                         "combined_vs_cash": paired_interval(series["combined"], [[] for _ in subset])}
    return {"rule_version": RULE_VERSION, "evidence": "UNDERLYING_ONLY", "live_eligible": False,
            "setup_model": "V2_REPEATING_SNIPER" if sniper else "V1_FIRST_SETUP",
            "study_start": start.isoformat(), "held_later_start": split.isoformat(), "end": right.isoformat(),
            "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            "input_bars": {s: sum(map(len, raw[s].values())) for s in SYMBOLS},
            "usable_sessions": len(book), "missing_mornings_excluded": excluded,
            "factor_symbol_session_counts": {k: dict(v) for k,v in factor_counts.items()},
            "method": {"entry": "shared 15m ORB/retest EMA9 + VWAP; next five-minute candle break; gap fill at worse open, no chase at target",
                       "exit": "same-bar ties stop first; count ambiguous entry/stop as loss; EMA9 exit next open, max 60min or 11:30 ET",
                       "costs": "2 bps round trip underlying, stress 5 bps; not option returns or account equity",
                       "portfolio": "at most one overlapping underlying position; SPY first on ties, reject a same-bar exit/re-entry",
                       "rearm": ("Uncapped count; after each position exit, require a complete fresh 5m inside-range bar then a fresh breakout/retest/trigger in the original window." if sniper else "At most the first clean setup per symbol per session."),
                       "split_interpretation": ("Calendar holdout reused after the V1 results were seen; comparative, not a fresh untouched test." if sniper else "Time-separated comparison; frozen rules, not a prospective forward test."),
                       "volume": "09:30-09:45 vs trailing 20 XNYS sessions, median; minimum five complete openings",
                       "vix": "exact previous XNYS daily close, modeled known at midnight ET after close; no intraday or same-day VIX",
                       "data": "OpenD 5m RTH QFQ end labels minus 5m; Cboe daily VIX historical download; no archived as-published revisions",
                       "gex": "unavailable: no archived time-matched complete chain + published open interest; never assign dealer sign",
                       "net_bps": "fixed unit notional, additive non-compounded. No 0DTE greeks, NBBO, affordability, settlement or capital modeled."},
            **report}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache", type=Path, default=Path("data/orb-study"))
    p.add_argument("--input-start", type=date.fromisoformat, default=date(2023, 10, 19))
    p.add_argument("--start", type=date.fromisoformat, default=date(2023, 11, 20))
    p.add_argument("--split", type=date.fromisoformat, default=date(2026, 1, 2))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 11, 19))
    p.add_argument("--output", type=Path)
    p.add_argument("--sniper", action="store_true", help="Version 2 repeating-entry model; use a distinct artifact")
    a = p.parse_args()
    result = json.dumps(run(a.cache, a.input_start, a.end, a.start, a.split, sniper=a.sniper), indent=2, allow_nan=False)+"\n"
    if a.output:
        a.output.write_text(result)
    else:
        print(result)


if __name__ == "__main__":
    main()
