"""Walk-forward backtest — full pipeline over real history (doc 03 §7, doc 08 §4).

Usage:
    python -m intellidhan_learning.backtest --symbols QQQ SPY SMH TQQQ --days 55

Pipeline per symbol: 2y daily warm-start -> stream 5m bars through
EngineRunner -> Composer (equity vehicle; option translation needs chain
history) -> PaperExecutor per the alert's own plan -> per-strategy report.
Alerts backtested here seed the calibration tables (bucket -> realized WR).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.runner import EngineRunner
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_learning.paper import PaperExecutor, PaperTrade, performance_report
from intellidhan_schemas import Timeframe


async def run_backtest(symbols: list[str], days: int, shadow: bool = False,
                       fit_calibration: bool = False) -> dict:
    provider = YahooProvider()
    end = datetime.now(timezone.utc)
    runner = EngineRunner(symbols, shadow=shadow)
    composer = Composer(Budgets(), option_selector=None)
    executor = PaperExecutor()

    all_5m = []
    for sym in symbols:
        daily = await provider.get_bars(sym, Timeframe.D1, end - timedelta(days=730), end)
        cutoff = end - timedelta(days=days + 1)
        runner.seed_daily(sym, [b for b in daily if b.ts_close < cutoff])
        bars = await provider.get_bars(sym, Timeframe.M5, end - timedelta(days=days), end)
        all_5m.extend(bars)
        print(f"{sym}: {len(bars)} 5m bars, daily warm-start "
              f"{sum(1 for b in daily if b.ts_close < cutoff)} bars", file=sys.stderr)
    all_5m.sort(key=lambda b: (b.ts_close, b.symbol))

    alerts = []
    for bar in all_5m:
        executor.on_bar(bar)  # manage opens BEFORE new signals on the same bar
        for setup in runner.on_bar_5m(bar):
            alert = composer.compose(setup)
            if alert is not None:
                alerts.append(alert)
                executor.track(PaperTrade.from_alert(alert, setup))

    by_strategy: dict[str, list[PaperTrade]] = defaultdict(list)
    for t in executor.trades:
        by_strategy[t.strategy].append(t)

    # calibration seed: 5-pt confidence buckets -> realized WR (doc 03 §4)
    buckets: dict[str, dict] = {}
    decided = [t for t in executor.trades if t.realized_r is not None]
    for t in decided:
        b = f"{int(t.confidence * 100) // 5 * 5}-{int(t.confidence * 100) // 5 * 5 + 5}"
        buckets.setdefault(b, {"n": 0, "wins": 0})
        buckets[b]["n"] += 1
        buckets[b]["wins"] += 1 if t.realized_r > 0 else 0

    fitted = {}
    if fit_calibration:
        from intellidhan_engine.calibration import CalibrationMap
        by_strat_samples: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        for t in decided:
            if t.composite is not None:
                # doc 03 §4 win definition: TP1 reached before the initial stop
                by_strat_samples[t.strategy].append((t.composite, t.tranches_exited >= 1))
        for strat, samples in by_strat_samples.items():
            cal = CalibrationMap.fit(strat, samples, meta={
                "fitted_from": f"shadow backtest {days}d {'+'.join(symbols)}",
                "n_samples": len(samples), "analytics_version": "v1",
            })
            path = cal.save()
            fitted[strat] = str(path)

    return {
        "period_days": days,
        "mode": "SHADOW" if shadow else "GATED",
        "calibration_fitted": fitted,
        "symbols": symbols,
        "bars_processed": len(all_5m),
        "setups_emitted": len(runner.setups),
        "alerts_composed": len(alerts),
        "suppressions": _gate_counts(runner),
        "overall": performance_report(executor.trades),
        "by_strategy": {k: performance_report(v) for k, v in by_strategy.items()},
        "calibration_seed": {
            b: {"n": v["n"], "realized_wr": round(v["wins"] / v["n"], 3)}
            for b, v in sorted(buckets.items()) if v["n"] > 0
        },
    }


def _gate_counts(runner: EngineRunner) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in runner.suppressed:
        counts[s.gate] = counts.get(s.gate, 0) + 1
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["QQQ", "SPY", "SMH", "TQQQ"])
    ap.add_argument("--days", type=int, default=55)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--shadow", action="store_true")
    ap.add_argument("--fit-calibration", action="store_true")
    args = ap.parse_args(argv)
    report = asyncio.run(run_backtest(args.symbols, args.days, args.shadow,
                                      args.fit_calibration))
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
