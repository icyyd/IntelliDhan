"""Frozen-parameter, after-cost diagnostics; never writes live calibration.

The July 24 ORR profiles and EMA9 defaults are rechecked on later completed
sessions. Cost scenarios are sensitivity tests, not candidates to optimize.
Intraday underlying bars cannot validate option profits or a portfolio return.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from intellidhan_ingestor.market_clock import ET, MarketClock
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_learning.ema9_crossover import (
    Ema9CrossoverConfig,
    backtest_ema9_crossover,
)
from intellidhan_learning.opening_range_reversal import (
    OpeningRangeReversalConfig,
    backtest_opening_range_reversal,
)
from intellidhan_schemas import Bar, Timeframe

FROZEN_AFTER = date(2026, 7, 24)
COST_SCENARIOS = (2.0, 5.0, 10.0)
BOOTSTRAP_SEED = 24092026

# Spell out every historical parameter. Importing mutable production profiles
# or accepting new constructor defaults would silently change this control.
FROZEN_ORR_CONTROL = OpeningRangeReversalConfig(
    daily_atr_period=14, manipulation_fraction=0.20, min_reversal_body_fraction=0.50,
    entry_cutoff_et="11:30", stop_buffer_atr5=0.15, partial_fraction=0.50,
    runner_target_r=2.0, prior_level_filter=False, prior_level_tolerance_atr5=0.25,
    cost_bps_per_side=2.0, slippage_atr5=0.02, flatten_time_et="15:55", min_session_bars=40,
)
FROZEN_ORR_CANDIDATE = replace(
    FROZEN_ORR_CONTROL, manipulation_fraction=0.25, min_reversal_body_fraction=0.25,
    entry_cutoff_et="10:30",
)
FROZEN_EMA9 = Ema9CrossoverConfig(
    timeframe=Timeframe.M30, ema_period=9, atr_period=14, initial_stop_atr=1.25,
    trail_atr=1.50, trail_activation_r=0.75, confirmation_bars=2,
    require_ema21_alignment=True, reentry_cooldown_bars=1, allow_short=False,
    rth_only=True, flatten_time_et="15:55", cost_bps_per_side=2.0,
    stop_limit_offset_atr=0.10,
)


def performance(trades: Iterable, sessions: list[str]) -> dict:
    """Report net wins and an interval retaining same-session correlations.

    A circular moving-block bootstrap resamples five consecutive sessions,
    keeping every symbol's same-session trades together. The interval measures
    mean net R/trade; R totals/DD are not cash returns or portfolio risk.
    """
    rows = list(trades)
    sessions = sorted(set(sessions))
    by_session: dict[str, list[float]] = defaultdict(list)
    by_exit: dict[datetime, float] = defaultdict(float)
    for trade in rows:
        session = trade.entry_ts.astimezone(ET).date().isoformat()
        if session not in sessions:
            raise ValueError("trade entry is outside the supplied session sample")
        by_session[session].append(trade.net_r)
        by_exit[trade.exit_ts] += trade.net_r
    equity = peak = drawdown = 0.0
    for timestamp in sorted(by_exit):
        equity += by_exit[timestamp]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    positive = sum(t.net_r for t in rows if t.net_r > 0)
    negative = -sum(t.net_r for t in rows if t.net_r < 0)
    interval = None
    if len(sessions) >= 10 and rows:
        totals = np.array([sum(by_session.get(s, [])) for s in sessions])
        counts = np.array([len(by_session.get(s, [])) for s in sessions])
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        means = []
        for _ in range(2000):
            starts = rng.integers(0, len(sessions), size=(len(sessions) + 4) // 5)
            indices = ((starts[:, None] + np.arange(5)) % len(sessions)).ravel()
            indices = indices[:len(sessions)]
            count = counts[indices].sum()
            if count:
                means.append(float(totals[indices].sum() / count))
        if means:
            interval = [round(float(x), 6) for x in np.quantile(means, [0.025, 0.975])]
    return {
        "sessions": len(sessions),
        "trading_sessions": len(by_session),
        "trades": len(rows),
        "net_win_rate": round(sum(t.net_r > 0 for t in rows) / len(rows), 4) if rows else None,
        "avg_net_r": round(sum(t.net_r for t in rows) / len(rows), 6) if rows else None,
        "profit_factor": round(positive / negative, 4) if negative else None,
        "total_net_r": round(sum(t.net_r for t in rows), 6),
        "max_drawdown_r": round(drawdown, 6),
        "mean_net_r_95pct_session_block_interval": interval,
        "interval_method": "2000 circular 5-session block resamples; paired symbols; seed 24092026",
        "positive_mean_supported": bool(interval is not None and interval[0] > 0),
    }


def fingerprint(bars: list[Bar]) -> dict:
    ordered = sorted(bars, key=lambda b: (b.symbol, b.timeframe.value, b.ts_close))
    digest = hashlib.sha256()
    for bar in ordered:
        digest.update((bar.model_dump_json() + "\n").encode())
    return {
        "bars": len(ordered),
        "sha256": digest.hexdigest(),
        "first_timestamp": min((b.ts_close for b in ordered), default=None),
        "last_timestamp": max((b.ts_close for b in ordered), default=None),
        "sources": sorted({b.source for b in ordered}),
    }


def completed_intraday(bars: list[Bar], now: datetime) -> list[Bar]:
    """Exclude partial days and off-session bars, including future vendor rows."""
    clock = MarketClock()
    cutoff = clock.latest_completed_session_close(now)
    result = []
    for bar in bars:
        local = bar.ts_close.astimezone(ET)
        if not clock.is_trading_day(local.date()):
            continue
        opened = datetime.combine(local.date(), datetime.min.time(), tzinfo=ET).replace(
            hour=9, minute=30
        )
        closed = datetime.combine(local.date(), clock.rth_close(local.date()), tzinfo=ET)
        if opened < local <= closed and local <= cutoff:
            result.append(bar)
    return result


def contiguous_sessions(bars: list[Bar]) -> tuple[list[Bar], list[str]]:
    """Keep complete regular sessions with every expected bar exactly once."""
    clock = MarketClock()
    grouped: dict[tuple[str, date, Timeframe], list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.symbol, bar.ts_close.astimezone(ET).date(), bar.timeframe)].append(bar)
    accepted = []
    excluded = []
    for (symbol, day, timeframe), session in sorted(grouped.items()):
        opened = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=30)
        closed = datetime.combine(day, clock.rth_close(day), tzinfo=ET)
        count = int((closed - opened).total_seconds() // timeframe.seconds)
        expected = {opened + timedelta(seconds=i * timeframe.seconds) for i in range(1, count + 1)}
        observed = {b.ts_close.astimezone(ET) for b in session}
        if len(session) == count and observed == expected:
            accepted.extend(session)
        else:
            excluded.append(f"{symbol}/{timeframe.value}/{day}")
    return accepted, excluded


async def run(symbols: list[str], days: int = 59) -> dict:
    if not 10 <= days <= 59:
        raise ValueError("days must be 10..59 for the free 5m diagnostic window")
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("supply unique symbols")
    provider = YahooProvider()
    now = datetime.now(timezone.utc)
    latest_session = MarketClock().latest_completed_session_close(now).date()
    data: dict[str, list[Bar]] = {"5m": [], "30m": [], "daily": []}
    provenance = {}
    excluded_sessions = []
    for symbol in symbols:
        for key, timeframe, lookback in (
            ("5m", Timeframe.M5, days),
            ("30m", Timeframe.M30, days),
            ("daily", Timeframe.D1, 500),
        ):
            bars = await provider.get_bars(
                symbol, timeframe,
                (now - timedelta(days=lookback)).replace(hour=0, minute=0, second=0, microsecond=0),
                now, adjusted=False
            )
            bars = (
                [b for b in bars if b.ts_close.astimezone(ET).date() <= latest_session]
                if key == "daily" else completed_intraday(bars, now)
            )
            if key != "daily":
                bars, excluded = contiguous_sessions(bars)
                excluded_sessions.extend(excluded)
            if not bars:
                raise ValueError(f"no completed {key} data for {symbol}; validation aborted")
            if max(b.ts_close.astimezone(ET).date() for b in bars) != latest_session:
                raise ValueError(f"stale {key} data for {symbol}; validation aborted")
            provenance[f"{symbol}/{key}"] = fingerprint(bars)
            data[key].extend(bars)

    # Every candidate receives the same complete symbol/session population.
    coverage = [
        {b.ts_close.astimezone(ET).date().isoformat() for b in data[key] if b.symbol == symbol}
        for key in ("5m", "30m") for symbol in symbols
    ]
    common = set.intersection(*coverage)
    sessions = sorted(day for day in common if day > FROZEN_AFTER.isoformat())
    if len(sessions) < 10:
        raise ValueError("fewer than 10 completed post-freeze sessions; validation aborted")
    strategies = {}
    for key, config in (
        ("ORR_CONTROL", FROZEN_ORR_CONTROL),
        ("ORR_SHADOW_CANDIDATE", FROZEN_ORR_CANDIDATE),
        ("EMA9_CROSSOVER_30M", FROZEN_EMA9),
    ):
        scenarios = []
        for cost in COST_SCENARIOS:
            cfg = replace(config, cost_bps_per_side=cost)
            trades = (
                backtest_ema9_crossover(data["30m"], cfg)
                if key == "EMA9_CROSSOVER_30M"
                else backtest_opening_range_reversal(data["5m"], data["daily"], cfg)
            )
            trades = [t for t in trades if t.entry_ts.astimezone(ET).date().isoformat() in sessions]
            scenarios.append({
                "cost_bps_per_side": cost,
                "overall": performance(trades, sessions),
                "by_symbol": {
                    symbol: performance([t for t in trades if t.symbol == symbol], sessions)
                    for symbol in symbols
                },
                "trades": [asdict(t) for t in trades],
            })
        strategies[key] = {"frozen_parameters": asdict(config), "cost_scenarios": scenarios}
    return {
        "research_type": "frozen-parameter underlying diagnostic",
        "as_of": now,
        "parameter_freeze_date": FROZEN_AFTER,
        "selection_performed": False,
        "promotion_status": "RESEARCH_ONLY",
        "live_eligible": False,
        "requested_symbols": symbols,
        "requested_calendar_days": days,
        "evaluation_first_session": sessions[0],
        "evaluation_last_session": sessions[-1],
        "provenance": provenance,
        "excluded_incomplete_sessions": excluded_sessions,
        "strategies": strategies,
        "limitations": [
            "Yahoo underlying OHLC only; no historical option quotes, spread or Greeks.",
            "Results do not validate 0DTE, swing options or LEAPS profits.",
            "R sums/DD are trade diagnostics, not a concurrency-aware cash portfolio.",
            "Current sessions are excluded; vendor corrections can change later replays.",
            "No new search or promotion: a positive short sample still needs longer data and paper fills.",
            "The July profiles were chosen on prior data; this check consumes its later holdout.",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    parser.add_argument("--days", type=int, default=59)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(run(args.symbols, args.days))
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(f"Research report saved to {args.out}; no live calibration changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
