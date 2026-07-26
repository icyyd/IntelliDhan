#!/usr/bin/env python3
"""Reproduce the committed ORR permutation study.

This is an intentionally bounded research sweep, not a production optimizer.
The test window is never used to choose parameters; it is only reported after
the train/validation eligibility check.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from intellidhan_learning.opening_range_reversal import (
    OpeningRangeReversalConfig,
    _fetch as _fetch_primary,
    _session_bars,
    _split_by_session,
    backtest_opening_range_reversal,
    performance_report,
)


GRID: dict[str, tuple[object, ...]] = {
    "manipulation_fraction": (0.15, 0.20, 0.25, 0.30),
    "daily_atr_period": (10, 14, 20),
    "entry_cutoff_et": ("10:30", "11:30"),
    "min_reversal_body_fraction": (0.0, 0.25, 0.5, 0.75),
    "stop_buffer_atr5": (0.15, 0.30),
    "runner_target_r": (1.5, 2.0, 2.5),
    "prior_level_filter": (False, True),
}


async def _fetch(symbols: list[str], days: int):
    """Reuse the primary CLI loader so adjustment policy cannot diverge."""
    return await _fetch_primary(symbols, days)


def _parameters(config: OpeningRangeReversalConfig) -> dict[str, object]:
    return {name: getattr(config, name) for name in GRID}


def _evaluate(config, intraday, daily, session_dates):
    trades = backtest_opening_range_reversal(intraday, daily, config)
    splits = _split_by_session(trades, session_dates)
    return {
        split: performance_report(rows, include_breakdown=False) for split, rows in splits.items()
    }, trades


async def run(symbols: list[str], days: int) -> dict:
    intraday, daily = await _fetch(symbols, days)
    session_dates = sorted(_session_bars(intraday))
    first = session_dates[max(1, int(len(session_dates) * 0.60))]
    second = session_dates[max(2, int(len(session_dates) * 0.80))]
    variants = []
    keys = list(GRID)
    for values in itertools.product(*(GRID[key] for key in keys)):
        config = replace(OpeningRangeReversalConfig(), **dict(zip(keys, values)))
        reports, trades = _evaluate(config, intraday, daily, session_dates)
        if (
            reports["train"]["trades"] >= 20
            and reports["validation"]["trades"] >= 10
            and reports["train"]["avg_net_r"] > 0
            and reports["validation"]["avg_net_r"] > 0
        ):
            variants.append(
                {
                    "parameters": _parameters(config),
                    **reports,
                    "all": performance_report(trades, include_breakdown=False),
                }
            )
    variants.sort(
        key=lambda row: (
            row["validation"]["avg_net_r"],
            row["validation"]["win_rate"],
            row["validation"]["trades"],
        ),
        reverse=True,
    )
    return {
        "strategy": "ORB_REVERSAL_15M",
        "status": "FORWARD_SHADOW_ONLY",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "days": days,
        "bars_5m": len(intraday),
        "daily_bars": len(daily),
        "session_count": len(session_dates),
        "session_first": session_dates[0] if session_dates else None,
        "session_last": session_dates[-1] if session_dates else None,
        "split_boundaries": {
            "train_sessions": f"<{first}",
            "validation_sessions": f"{first} <= date < {second}",
            "test_sessions": f">= {second}",
            "counts": [
                session_dates.index(first),
                session_dates.index(second) - session_dates.index(first),
                len(session_dates) - session_dates.index(second),
            ],
        },
        "grid": GRID,
        "variant_count": 4 * 3 * 2 * 4 * 2 * 3 * 2,
        "eligible_variant_count": len(variants),
        "selection_rule": "train n>=20 and validation n>=10 with positive net expectancy; rank validation avg_net_r, then win rate; test is reported once",
        "top_variants": variants[:30],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    parser.add_argument("--days", type=int, default=59)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(run(args.symbols, args.days))
    args.out.write_text(json.dumps(report, indent=2, default=str))
    selected = report["top_variants"][0] if report["top_variants"] else None
    print(
        json.dumps(
            {
                "summary": {
                    key: report[key]
                    for key in (
                        "variant_count",
                        "eligible_variant_count",
                        "session_count",
                        "split_boundaries",
                    )
                },
                "selected": selected,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
