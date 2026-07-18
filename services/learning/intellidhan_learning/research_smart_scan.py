"""Fixed-parameter walk-forward diagnostic for cross-universe smart momentum.

This is a research harness, not a strategy-promotion result.  It intentionally
uses the current configured universe, reports that survivorship limitation, and
never tunes parameters from the returned performance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from intellidhan_gateway.universe import load_live_symbols
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_schemas import Timeframe


def _performance(returns: pd.Series) -> dict:
    returns = returns.dropna()
    equity = (1.0 + returns).cumprod()
    years = len(returns) / 252.0
    cagr = equity.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else 0.0
    volatility = float(returns.std(ddof=1) * math.sqrt(252))
    sharpe = (
        float(returns.mean() / returns.std(ddof=1) * math.sqrt(252))
        if returns.std(ddof=1) > 0
        else None
    )
    # Keep the initial capital of 1.0 in the running peak so an immediate loss
    # is represented in drawdown rather than becoming the first artificial peak.
    drawdown = equity / equity.cummax().clip(lower=1.0) - 1.0
    return {
        "total_return_pct": round(float((equity.iloc[-1] - 1.0) * 100.0), 2),
        "cagr_pct": round(float(cagr * 100.0), 2),
        "annual_volatility_pct": round(volatility * 100.0, 2),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown_pct": round(float(drawdown.min() * 100.0), 2),
    }


def _date_string(value) -> str:
    return str(value.date()) if hasattr(value, "date") else str(value)


def backtest_smart_momentum(
    closes: pd.DataFrame,
    *,
    benchmark_symbol: str = "SPY",
    top_n: int = 3,
    rebalance_sessions: int = 21,
    cost_bps: float = 10.0,
) -> dict:
    """Monthly top-N 12–1 momentum with rising-trend and next-close execution."""
    if len(closes) < 300:
        raise ValueError("at least 300 common daily observations are required")
    if benchmark_symbol not in closes:
        raise ValueError("benchmark symbol must be present in closes")
    if top_n < 1:
        raise ValueError("top_n must be positive")
    closes = closes.sort_index().astype(float).dropna()
    returns = closes.pct_change().fillna(0.0)
    weights = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    selections: list[dict] = []

    for signal_index in range(252, len(closes) - 2, rebalance_sessions):
        current = closes.iloc[signal_index]
        return_12_1 = closes.iloc[signal_index - 21] / closes.iloc[signal_index - 252] - 1.0
        return_6_1 = closes.iloc[signal_index - 21] / closes.iloc[signal_index - 126] - 1.0
        sma200 = closes.iloc[signal_index - 199 : signal_index + 1].mean()
        prior_sma200 = closes.iloc[signal_index - 219 : signal_index - 19].mean()
        yearly_high = closes.iloc[signal_index - 251 : signal_index + 1].max()
        recent_vol = np.log(
            closes.iloc[signal_index - 20 : signal_index + 1]
        ).diff().std(ddof=1)
        eligible = (
            (return_12_1 > 0)
            & (return_6_1 > 0)
            & (current > sma200)
            & (sma200 > prior_sma200)
            & (current >= 5.0)
        )
        ranks = pd.DataFrame(
            {
                "r12": return_12_1.rank(pct=True),
                "r6": return_6_1.rank(pct=True),
                "high": (current / yearly_high).rank(pct=True),
                "risk": 1.0 - recent_vol.rank(pct=True),
            }
        )
        score = ranks["r12"] * 0.40 + ranks["r6"] * 0.25 + ranks["high"] * 0.20 + ranks["risk"] * 0.15
        selected = list(score[eligible].nlargest(top_n).index)
        execution_index = signal_index + 1
        next_execution = min(execution_index + rebalance_sessions, len(closes))
        if selected:
            weights.iloc[execution_index:next_execution, :] = 0.0
            weights.loc[weights.index[execution_index:next_execution], selected] = 1.0 / len(selected)
        selections.append(
            {
                "signal_date": _date_string(closes.index[signal_index]),
                "execution_date": _date_string(closes.index[execution_index]),
                "symbols": selected,
            }
        )

    applied_weights = weights.shift(1).fillna(0.0)
    turnover = weights.diff().abs().sum(axis=1)
    strategy_returns = (applied_weights * returns).sum(axis=1) - turnover * (cost_bps / 10_000.0)
    # Fix the out-of-sample window before observing whether the model selects
    # a position. An eligible cash regime is a strategy outcome, not missing data.
    first_execution = 253
    evaluation = strategy_returns.iloc[first_execution:]
    benchmark = returns[benchmark_symbol].iloc[first_execution:]
    benchmark = benchmark.copy()
    benchmark.iloc[0] = 0.0  # benchmark is also bought at the execution close
    active_days = (weights.sum(axis=1).iloc[first_execution:] > 0).mean()
    return {
        "research_type": "fixed-parameter configured-universe walk-forward diagnostic",
        "signal": "12–1 and 6–1 cross-sectional momentum above a rising 200-day average",
        "execution": "signal at close; rebalance at next close; returns begin the following session",
        "parameters": {
            "top_n": top_n,
            "rebalance_sessions": rebalance_sessions,
            "cost_bps_per_one_way_turnover": cost_bps,
        },
        "observations": len(evaluation),
        "evaluation_start": _date_string(closes.index[first_execution]),
        "first_signal": selections[0]["signal_date"] if selections else None,
        "last_signal": selections[-1]["signal_date"] if selections else None,
        "average_exposure_pct": round(float(active_days * 100.0), 2),
        "strategy": _performance(evaluation),
        "benchmark": {"symbol": benchmark_symbol, **_performance(benchmark)},
        "rebalances": selections,
        "promotion_status": "RESEARCH_ONLY",
        "limitations": [
            "The configured universe is current and survivorship-biased.",
            "The small, technology-heavy universe is not a broad-market validation sample.",
            "Historical dollar-volume data is not modeled; live plays still require $5M ADV.",
            "Adjusted daily data cannot model intraday fills, taxes, borrow, or market impact.",
            "Promotion still requires a point-in-time constituent universe and forward paper period.",
        ],
    }


async def run(
    symbols: list[str], years: int = 5, cost_bps: float = 10.0, top_n: int = 3
) -> dict:
    provider = YahooProvider()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=years * 366)

    async def one(symbol: str):
        bars = await provider.get_bars(symbol, Timeframe.D1, start, end, adjusted=True)
        return symbol, pd.Series(
            {bar.ts_close.date(): bar.close for bar in bars}, name=symbol, dtype=float
        )

    histories = await asyncio.gather(*(one(symbol) for symbol in symbols))
    closes = pd.concat([series for _, series in histories], axis=1, join="inner").dropna()
    report = backtest_smart_momentum(
        closes, cost_bps=cost_bps, top_n=top_n
    )
    report["symbols"] = symbols
    report["data_start"] = str(closes.index[0])
    report["data_end"] = str(closes.index[-1])
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=load_live_symbols())
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--top-n", type=int, default=3)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(args.symbols, args.years, args.cost_bps, args.top_n)),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
