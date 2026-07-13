"""0DTE multi-strategy tuning harness — honest parameter search (doc 08 §4).

Extends research.py's method to all three 0DTE strategies in ONE engine pass
(shared analytics state, distinct variant keys), with a three-way session
split: first 55% TRAIN, next 25% VALIDATION, last 20% TEST.

Governance:
- Selection happens on VALIDATION only (config must show target WR with
  n >= MIN_VAL_N, positive expectancy, and train WR within 5pts).
- TEST is scored ONCE, only for each family's already-selected winner, and
  reported with a Wilson interval. Nothing is re-selected on test.
- The exact production configs ride along as controls (key *_V00).
- No qualifying config is a valid, reportable outcome — we say so rather
  than loosening the rule after seeing results.

Like research.py, risk-state (concurrency/duplicate/correlation) is not
registered, so variants cannot block each other; any selected winner must
then be confirmed through backtest.py's production-parity gating before a
calibration refit is trusted.

Usage: python -m intellidhan_learning.research_0dte --days 55
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.macro import build_macro_series
from intellidhan_engine.runner import EngineRunner
from intellidhan_engine.strategies import Ema9TrendPullback, OrbBreakout, VwapReclaim
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_learning.paper import PaperExecutor, PaperTrade
from intellidhan_schemas import Timeframe

UNIVERSE = ["QQQ", "SPY", "SMH", "TQQQ", "AAPL", "NVDA", "MSFT", "AMZN",
            "META", "GOOGL", "AMD", "TSLA"]
MIN_VAL_N = 30
TARGET_WR = 0.75
SPLITS = (0.55, 0.80)   # session fractions: train < s1 <= val < s2 <= test


def build_variants() -> tuple[list, dict[str, str]]:
    """All variants for one shared pass; returns (instances, key->family)."""
    variants: list = []
    family: dict[str, str] = {}

    def add(inst, fam):
        variants.append(inst)
        family[inst.key] = fam

    # EMA9_TREND_PULLBACK — V00 is the production default
    ema_grid = itertools.product(
        [(1.0, 1.8, 3.0), (0.8, 1.8, 3.0), (0.7, 1.8, 3.0), (0.6, 1.8, 3.0),
         (0.5, 1.8, 3.0)],                                    # T1 proximity
        [40.0, 60.0],                                          # 5m trend floor
        [0.0, 1.2],                                            # min relative volume
        [False, True],                                         # require 1H alignment
    )
    for i, (t, tr, rv, h1) in enumerate(ema_grid):
        add(Ema9TrendPullback(key=f"EMA9_V{i:02d}", t_mults=t, trend_min=tr,
                              min_relvol=rv, require_h1=h1), "EMA9_TREND_PULLBACK")

    # ORB_BREAKOUT — V00 is the production default
    orb_grid = itertools.product(
        [(0.5, 1.0, 1.75), (0.35, 1.0, 1.75), (0.25, 1.0, 1.75)],  # T1 in range units
        [0.35, 0.5],                                                # stop fraction
        [0.0, 1.3],                                                 # min relative volume
        [0.0, 20.0],                                                # H1 alignment floor
    )
    for i, (t, sf, rv, h1) in enumerate(orb_grid):
        add(OrbBreakout(key=f"ORB_V{i:02d}", t_mults=t, stop_frac=sf,
                        min_relvol=rv, h1_min=h1), "ORB_BREAKOUT")

    # VWAP_RECLAIM — V00 is the production default
    vwap_grid = itertools.product(
        [(1.0, 1.8, 3.0), (0.7, 1.8, 3.0), (0.5, 1.8, 3.0)],   # T1 in ATR units
        [4, 3],                                                 # bars on far side required
        [4, 6],                                                 # stop lookback bars
    )
    for i, (t, mb, sl) in enumerate(vwap_grid):
        add(VwapReclaim(key=f"VWAP_V{i:02d}", t_mults=t, min_before=mb,
                        stop_lookback=sl), "VWAP_RECLAIM")

    return variants, family


def win(t: PaperTrade) -> bool:
    return t.tranches_exited >= 1   # doc 03 §4: TP1 before initial stop


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, c - hw), 3), round(min(1.0, c + hw), 3))


def stats(trades: list[PaperTrade]) -> dict:
    decided = [t for t in trades if t.realized_r is not None
               and t.outcome.value != "EXPIRED_UNFILLED"]
    if not decided:
        return {"n": 0}
    wins = sum(1 for t in decided if win(t))
    avg_r = sum(t.realized_r for t in decided) / len(decided)
    return {"n": len(decided), "wr": round(wins / len(decided), 3),
            "avg_r": round(avg_r, 3), "wilson_95": wilson(wins, len(decided))}


def params_of(v) -> dict:
    keys = ("t_mults", "trend_min", "min_relvol", "require_h1", "stop_frac",
            "h1_min", "min_before", "stop_lookback", "stop_atr_pad", "t15_opp")
    return {k: getattr(v, k) for k in keys if hasattr(v, k)}


async def run(days: int) -> dict:
    provider = YahooProvider()
    clock = MarketClock()
    end = datetime.now(timezone.utc)
    variants, family = build_variants()
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
    s1 = sessions[int(len(sessions) * SPLITS[0])]
    s2 = sessions[int(len(sessions) * SPLITS[1])]
    print(f"sessions={len(sessions)} train<{s1}<=val<{s2}<=test", file=sys.stderr)

    for bar in all_5m:
        executor.on_bar(bar)
        for setup in runner.on_bar_5m(bar):
            alert = composer.compose(setup)
            if alert is not None:
                executor.track(PaperTrade.from_alert(alert, setup))

    by_variant: dict[str, dict[str, list[PaperTrade]]] = defaultdict(
        lambda: {"train": [], "val": [], "test": []})
    for t in executor.trades:
        day = clock.session_id(t.valid_until)   # creation-day proxy (0DTE: same day)
        split = "train" if day < s1 else ("val" if day < s2 else "test")
        by_variant[t.strategy][split].append(t)

    rows = []
    for v in variants:
        tr = stats(by_variant[v.key]["train"])
        va = stats(by_variant[v.key]["val"])
        rows.append({"key": v.key, "family": family[v.key],
                     "params": params_of(v), "train": tr, "val": va})

    def qualifies(r):
        return (r["val"].get("n", 0) >= MIN_VAL_N
                and r["val"].get("wr", 0) >= TARGET_WR
                and r["val"].get("avg_r", -1) > 0
                and r["train"].get("wr", 0) >= TARGET_WR - 0.05)

    result: dict = {"days": days, "universe": UNIVERSE, "sessions": len(sessions),
                    "split_train_lt": s1, "split_val_lt": s2,
                    "min_val_n": MIN_VAL_N, "target_wr": TARGET_WR,
                    "families": {}, "all": rows}
    for fam in ("EMA9_TREND_PULLBACK", "ORB_BREAKOUT", "VWAP_RECLAIM"):
        fam_rows = [r for r in rows if r["family"] == fam]
        fam_rows.sort(key=lambda r: (r["val"].get("wr", 0), r["val"].get("n", 0)),
                      reverse=True)
        qualified = [r for r in fam_rows if qualifies(r)]
        winner = qualified[0] if qualified else None
        entry = {"qualified": qualified, "winner": None, "test": None,
                 "baseline_key": f"{fam.split('_')[0]}_V00"
                 if fam != "EMA9_TREND_PULLBACK" else "EMA9_V00"}
        if winner is not None:
            # TEST scored exactly once, for the selected winner only
            te = stats(by_variant[winner["key"]]["test"])
            entry["winner"] = winner
            entry["test"] = te
            entry["test_verdict"] = (
                "PASS" if te.get("n", 0) >= 15 and te.get("wr", 0) >= TARGET_WR
                else "INSUFFICIENT_N" if te.get("n", 0) < 15
                else "FAIL")
        else:
            entry["note"] = ("no variant met the validation bar "
                             f"(n>={MIN_VAL_N}, wr>={TARGET_WR}, avg_r>0, "
                             "train within 5pts) — honest outcome, not loosened")
        result["families"][fam] = entry
    return result


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
