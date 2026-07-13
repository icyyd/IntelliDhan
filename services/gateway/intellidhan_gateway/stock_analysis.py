"""On-demand corporate-action-adjusted daily trend analysis service."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any

from intellidhan_analytics.trend_analysis import analyze_daily_trend, backtest_trend_methods
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_schemas import Bar, Timeframe

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")


class StockAnalysisService:
    def __init__(
        self,
        provider=None,
        cache_seconds: int = 300,
        max_cache_entries: int = 128,
        max_provider_concurrency: int = 4,
    ) -> None:
        if max_cache_entries < 1:
            raise ValueError("max_cache_entries must be positive")
        if max_provider_concurrency < 1:
            raise ValueError("max_provider_concurrency must be positive")
        self.provider = provider or YahooProvider()
        self.cache_seconds = cache_seconds
        self.max_cache_entries = max_cache_entries
        self._provider_slots = asyncio.Semaphore(max_provider_concurrency)
        self._cache: OrderedDict[
            tuple[str, int], tuple[datetime, list[Bar]]
        ] = OrderedDict()

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        normalized = symbol.strip().upper()
        if not SYMBOL_RE.fullmatch(normalized):
            raise ValueError("symbol must be 1-12 letters, numbers, dots, or hyphens")
        return normalized

    async def analyze(
        self,
        symbol: str,
        *,
        years: int = 10,
        risk_budget: float | None = None,
        include_backtest: bool = True,
        cost_bps: float = 10.0,
    ) -> dict[str, Any]:
        symbol = self.normalize_symbol(symbol)
        if not 2 <= years <= 15:
            raise ValueError("years must be between 2 and 15")
        if risk_budget is not None and not 0 < risk_budget <= 1_000_000:
            raise ValueError("risk_budget must be positive and no more than 1,000,000")
        if not 0 <= cost_bps <= 100:
            raise ValueError("cost_bps must be between 0 and 100")
        bars = await self._daily_bars(symbol, years)
        if not bars:
            raise LookupError(f"no completed adjusted daily bars found for {symbol}")
        report = analyze_daily_trend(bars, risk_budget=risk_budget)
        report["request"] = {
            "years": years,
            "risk_budget": risk_budget,
            "include_backtest": include_backtest,
            "cost_bps": cost_bps,
        }
        report["backtest"] = (
            backtest_trend_methods(bars, cost_bps=cost_bps) if include_backtest else None
        )
        return report

    async def _daily_bars(self, symbol: str, years: int) -> list[Bar]:
        key = (symbol, years)
        now = datetime.now(timezone.utc)
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            self._cache.move_to_end(key)
            return cached[1]
        if cached:
            del self._cache[key]
        # Midnight UTC makes Yahoo's end bound exclude today's possibly incomplete daily bar.
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=years * 366)
        async with self._provider_slots:
            bars = await self.provider.get_bars(
                symbol, Timeframe.D1, start, end, adjusted=True
            )
        self._cache[key] = (now + timedelta(seconds=self.cache_seconds), bars)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_cache_entries:
            self._cache.popitem(last=False)
        return bars
