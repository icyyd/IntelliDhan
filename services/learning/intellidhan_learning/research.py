"""Strategy tuning harness — honest parameter search (doc 08 §4 governance).

Method:
- Expanded universe (12 liquid symbols) to multiply sample size.
- ALL variants run in ONE engine pass (shared analytics state, distinct keys).
- Sessions split by date: first 60% = TRAIN, last 40% = VALIDATION.
- A config is acceptable only if the VALIDATION set independently shows the
  target TP1-win rate with n >= MIN_VAL_N and positive expectancy — selecting
  on train and confirming on validation is the overfitting guard this small
  dataset allows. Anything less is curve fitting, and we say so in the output.

Usage: python -m intellidhan_learning.research --days 55
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.macro import build_macro_series
from intellidhan_engine.runner import EngineRunner
from intellidhan_engine.strategies import Ema9TrendPullback
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_learning.paper import PaperExecutor, PaperTrade
from intellidhan_schemas import Timeframe

UNIVERSE = ["QQQ", "SPY", "SMH", "TQQQ", "AAPL", "NVDA", "MSFT", "AMZN",
            "META", "GOOGL", "AMD", "TSLA"]
MIN_VAL_N = 30
TARGET_WR = 0.75


def build_variants() -> list[Ema9TrendPullback]:
    grid = itertools.product(
        [(0.5, 1.2, 2.2), (0.7, 1.5, 2.6), (1.0, 1.8, 3.0)],   # target mults (T1 proximity)
        [40.0, 60.0],                                            # 5m trend strength floor
        [1.0, 1.3],                                              # min relative volume
        [False, True],                                           # require 1H alignment
    )
    variants = []
    for i, (t_mults, trend_min, min_rv, req_h1) in enumerate(grid):
        variants.append(Ema9TrendPullback(
            key=f"EMA9_V{i:02d}", t_mults=t_mults, trend_min=trend_min,
            min_relvol=min_rv, require_h1=req_h1))
    return variants


def win(t: PaperTrade) -> bool:
    return t.tranches_exited >= 1  # doc 03 §4: TP1 before initial stop


def stats(trades: list[PaperTrade]) -> dict:
    decided = [t for t in trades if t.realized_r is not None
               and t.outcome.value != "EXPIRED_UNFILLED"]
    if not decided:
        return {"n": 0}
    wins = sum(1 for t in decided if win(t))
    avg_r = sum(t.realized_r for t in decided) / len(decided)
    return {"n": len(decided), "wr": round(wins / len(decided), 3),
            "avg_r": round(avg_r, 3)}


async def run(days: int) -> dict:
    provider = YahooProvider()
    clock = MarketClock()
    end = datetime.now(timezone.utc)
    variants = build_variants()
    runner = EngineRunner(UNIVERSE, strategies=variants, shadow=True)
    composer = Composer(Budgets(), option_selector=None)
    executor = PaperExecutor()

    vix = await provider.get_bars("VIX", Timeframe.D1, end - timedelta(days=1200), end)
    runner.set_macro_series(build_macro_series(vix))

    all_5m = []
    for sym in UNIVERSE:
        daily = await provider.get_bars(sym, Timeframe.D1, end - timedelta(days=730), end)
        cutoff = end - timedelta(days=days + 1)
        runner.seed_daily(sym, [b for b in daily if b.ts_close < cutoff])
        bars = await provider.get_bars(sym, Timeframe.M5, end - timedelta(days=days), end)
        all_5m.extend(bars)
        print(f"{sym}: {len(bars)} bars", file=sys.stderr)
    all_5m.sort(key=lambda b: (b.ts_close, b.symbol))

    sessions = sorted({clock.session_id(b.ts_close) for b in all_5m})
    split_day = sessions[int(len(sessions) * 0.6)]
    print(f"sessions={len(sessions)} train<{split_day}<=val", file=sys.stderr)

    setup_by_alert: dict[str, object] = {}
    for bar in all_5m:
        executor.on_bar(bar)
        for setup in runner.on_bar_5m(bar):
            alert = composer.compose(setup)
            if alert is not None:
                setup_by_alert[alert.alert_id] = setup
                executor.track(PaperTrade.from_alert(alert, setup))

    by_variant: dict[str, dict[str, list[PaperTrade]]] = defaultdict(
        lambda: {"train": [], "val": []})
    for t in executor.trades:
        day = clock.session_id(t.valid_until)  # creation-day proxy
        split = "train" if day < split_day else "val"
        by_variant[t.strategy][split].append(t)

    rows = []
    for v in variants:
        tr = stats(by_variant[v.key]["train"])
        va = stats(by_variant[v.key]["val"])
        rows.append({
            "key": v.key,
            "params": {"t_mults": v.t_mults, "trend_min": v.trend_min,
                       "min_relvol": v.min_relvol, "require_h1": v.require_h1},
            "train": tr, "val": va,
        })

    qualified = [r for r in rows
                 if r["val"].get("n", 0) >= MIN_VAL_N
                 and r["val"].get("wr", 0) >= TARGET_WR
                 and r["val"].get("avg_r", -1) > 0
                 and r["train"].get("wr", 0) >= TARGET_WR - 0.05]
    qualified.sort(key=lambda r: (r["val"]["wr"], r["val"]["n"]), reverse=True)
    rows.sort(key=lambda r: r["val"].get("wr", 0), reverse=True)
    return {"days": days, "universe": UNIVERSE, "sessions": len(sessions),
            "split_day": split_day, "qualified": qualified, "all": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=55)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    report = asyncio.run(run(args.days))
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
