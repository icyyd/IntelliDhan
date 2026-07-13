"""Run the on-demand daily trend analyzer from the command line."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from intellidhan_gateway.stock_analysis import StockAnalysisService


async def run(args) -> dict:
    return await StockAnalysisService().analyze(
        args.symbol,
        years=args.years,
        risk_budget=args.risk_budget,
        include_backtest=not args.no_backtest,
        cost_bps=args.cost_bps,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Corporate-action-adjusted trend analysis and fixed-rule backtest"
    )
    parser.add_argument("symbol")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--risk-budget", type=float, default=None)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--no-backtest", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    output = json.dumps(asyncio.run(run(args)), indent=2)
    print(output)
    if args.out:
        args.out.write_text(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
