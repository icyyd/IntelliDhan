"""Cross-ticker validation harness for the fixed on-demand trend methods.

No parameter search is performed.  Every symbol uses the same horizons and
transaction-cost assumption defined in trend_analysis.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from statistics import median

from intellidhan_gateway.stock_analysis import StockAnalysisService

DEFAULT_SYMBOLS = [
    "QQQ", "SPY", "SMH", "TQQQ", "AAPL", "NVDA",
    "MSFT", "AMZN", "META", "GOOGL", "AMD", "TSLA",
]


async def run(symbols: list[str], years: int = 5, cost_bps: float = 10.0) -> dict:
    service = StockAnalysisService()
    semaphore = asyncio.Semaphore(4)

    async def one(symbol: str):
        async with semaphore:
            try:
                return symbol, await service.analyze(
                    symbol,
                    years=years,
                    include_backtest=True,
                    cost_bps=cost_bps,
                ), None
            except Exception as exc:
                return symbol, None, str(exc)

    rows = await asyncio.gather(*(one(symbol) for symbol in symbols))
    reports = {symbol: report for symbol, report, error in rows if report is not None}
    errors = {symbol: error for symbol, report, error in rows if error is not None}
    method_names = [
        "sma_200",
        "time_series_momentum_12m",
        "donchian_55_20",
        "majority_consensus",
    ]
    aggregate = {}
    for method in method_names:
        samples = [report["backtest"]["methods"][method] for report in reports.values()]
        comparisons = [report["backtest"]["buy_and_hold"] for report in reports.values()]
        if not samples:
            continue
        aggregate[method] = {
            "symbols": len(samples),
            "positive_cagr": sum(item["cagr_pct"] > 0 for item in samples),
            "beats_buy_hold_cagr": sum(
                item["cagr_pct"] > buy["cagr_pct"]
                for item, buy in zip(samples, comparisons)
            ),
            "smaller_max_drawdown": sum(
                item["max_drawdown_pct"] > buy["max_drawdown_pct"]
                for item, buy in zip(samples, comparisons)
            ),
            "median_cagr_pct": round(median(item["cagr_pct"] for item in samples), 2),
            "median_sharpe": round(
                median(item["sharpe"] for item in samples if item["sharpe"] is not None), 3
            ),
            "median_max_drawdown_pct": round(
                median(item["max_drawdown_pct"] for item in samples), 2
            ),
            "median_exposure_pct": round(
                median(item["exposure_pct"] for item in samples), 2
            ),
        }
    return {
        "research_type": "fixed-parameter cross-ticker diagnostic",
        "years_requested": years,
        "cost_bps_per_position_change": cost_bps,
        "symbols_requested": symbols,
        "symbols_completed": list(reports),
        "errors": errors,
        "aggregate": aggregate,
        "per_symbol": {
            symbol: {
                "as_of": report["as_of"],
                "bars": report["daily_bars"],
                "consensus": report["consensus"]["label"],
                "methods": report["backtest"]["methods"],
                "buy_and_hold": report["backtest"]["buy_and_hold"],
            }
            for symbol, report in reports.items()
        },
        "promotion_status": "RESEARCH_ONLY",
        "promotion_note": (
            "This diagnostic is not a strategy-promotion test. Require calendar walk-forward "
            "splits, survivorship-safe universes, corporate-action checks, and forward paper."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.symbols, args.years, args.cost_bps)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
