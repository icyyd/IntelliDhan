"""Compact end-of-day technical discovery service.

This intentionally does not invent fundamentals.  The result separates the
available Trend/Risk evidence from the four unavailable business-data pillars.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from intellidhan_ingestor.providers import YahooProvider
from intellidhan_schemas import Bar, Timeframe

from intellidhan_gateway.universe import load_live_symbols


PRESETS: dict[str, dict[str, Any]] = {
    "trend_leaders": {"above_sma200": True, "min_return_6m": 5.0},
    "pullback_uptrend": {
        "above_sma200": True,
        "min_return_6m": 0.0,
        "max_distance_from_high_pct": 12.0,
    },
    "fresh_breakouts": {
        "above_sma200": True,
        "max_distance_from_high_pct": 3.0,
    },
    "defensive_trend": {
        "above_sma200": True,
        "max_volatility_pct": 35.0,
    },
}


def _return_pct(closes: np.ndarray, sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return None
    return round((closes[-1] / closes[-sessions - 1] - 1.0) * 100.0, 2)


def _annualized_volatility(closes: np.ndarray, sessions: int = 20) -> float | None:
    if len(closes) <= sessions:
        return None
    returns = np.diff(np.log(closes[-(sessions + 1) :]))
    if len(returns) < 2:
        return None
    return round(float(np.std(returns, ddof=1) * math.sqrt(252) * 100), 2)


def _max_drawdown(closes: np.ndarray, sessions: int = 252) -> float | None:
    window = closes[-sessions:]
    if len(window) < 2:
        return None
    peaks = np.maximum.accumulate(window)
    drawdowns = window / peaks - 1.0
    return round(float(np.min(drawdowns) * 100.0), 2)


def screen_row(symbol: str, bars: list[Bar]) -> dict[str, Any]:
    ordered = sorted(bars, key=lambda bar: bar.ts_close)
    closes = np.asarray([bar.close for bar in ordered], dtype=float)
    volumes = np.asarray([bar.volume for bar in ordered], dtype=float)
    if len(closes) < 60:
        raise LookupError(f"insufficient completed daily history for {symbol}")
    price = float(closes[-1])
    sma200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else None
    high_52w = float(np.max(closes[-252:]))
    distance_high = (price / high_52w - 1.0) * 100.0
    adv_dollars = float(np.mean(closes[-20:] * volumes[-20:]))
    volatility = _annualized_volatility(closes)
    ret_6m = _return_pct(closes, 126)
    ret_12m = _return_pct(closes, 252)

    trend_parts = [
        100.0 if sma200 is not None and price > sma200 else 0.0,
        max(0.0, min(100.0, 50.0 + (ret_6m or 0.0) * 1.5)),
        max(0.0, min(100.0, 100.0 + distance_high * 4.0)),
    ]
    trend_score = round(sum(trend_parts) / len(trend_parts), 1)
    risk_score = round(max(0.0, min(100.0, 100.0 - (volatility or 100.0) * 1.25)), 1)
    technical_score = round(trend_score * 0.75 + risk_score * 0.25, 1)
    if price > (sma200 or price + 1) and distance_high >= -3:
        state = "LEADER"
    elif price > (sma200 or price + 1):
        state = "UPTREND"
    elif sma200 is not None and price < sma200:
        state = "BELOW_200D"
    else:
        state = "UNCONFIRMED"

    return {
        "symbol": symbol,
        "as_of": ordered[-1].ts_close.isoformat(),
        "price": round(price, 2),
        "return_1m_pct": _return_pct(closes, 21),
        "return_3m_pct": _return_pct(closes, 63),
        "return_6m_pct": ret_6m,
        "return_12m_pct": ret_12m,
        "sma_200": round(sma200, 2) if sma200 is not None else None,
        "distance_from_sma_200_pct": (
            round((price / sma200 - 1.0) * 100.0, 2) if sma200 else None
        ),
        "percent_of_52w_high": round(price / high_52w * 100.0, 2),
        "distance_from_52w_high_pct": round(distance_high, 2),
        "realized_volatility_20d_pct": volatility,
        "max_drawdown_1y_pct": _max_drawdown(closes),
        "average_dollar_volume_20d": round(adv_dollars, 0),
        "trend_state": state,
        "technical_score": technical_score,
        "pillars": {
            "trend": trend_score,
            "risk": risk_score,
            "quality": None,
            "growth": None,
            "valuation": None,
            "catalyst": None,
        },
        "evidence_coverage": {
            "available_pillars": 2,
            "total_pillars": 6,
            "label": "TECHNICAL_ONLY",
        },
    }


class DiscoveryService:
    def __init__(self, provider: YahooProvider | None = None, cache_seconds: int = 900) -> None:
        self.provider = provider or YahooProvider()
        self.cache_seconds = cache_seconds
        self._cache: tuple[float, list[dict[str, Any]]] | None = None

    async def _load_row(self, symbol: str, start: datetime, end: datetime) -> dict[str, Any]:
        bars = await self.provider.get_bars(symbol, Timeframe.D1, start, end)
        return screen_row(symbol, bars)

    async def universe_rows(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh and self._cache and time.monotonic() - self._cache[0] < self.cache_seconds:
            return self._cache[1]
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=550)
        symbols = load_live_symbols()
        results = await asyncio.gather(
            *(self._load_row(symbol, start, end) for symbol in symbols),
            return_exceptions=True,
        )
        rows = [item for item in results if isinstance(item, dict)]
        rows.sort(key=lambda item: item["technical_score"], reverse=True)
        self._cache = (time.monotonic(), rows)
        return rows

    async def screen(
        self,
        *,
        preset: str | None = None,
        min_price: float | None = None,
        min_adv_dollars: float | None = None,
        above_sma200: bool | None = None,
        min_return_6m: float | None = None,
        max_volatility_pct: float | None = None,
        max_distance_from_high_pct: float | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        filters = dict(PRESETS.get(preset or "", {}))
        supplied = {
            "min_price": min_price,
            "min_adv_dollars": min_adv_dollars,
            "above_sma200": above_sma200,
            "min_return_6m": min_return_6m,
            "max_volatility_pct": max_volatility_pct,
            "max_distance_from_high_pct": max_distance_from_high_pct,
        }
        filters.update({key: value for key, value in supplied.items() if value is not None})
        rows = await self.universe_rows(refresh=refresh)

        def included(row: dict[str, Any]) -> bool:
            if filters.get("min_price") is not None and row["price"] < filters["min_price"]:
                return False
            if filters.get("min_adv_dollars") is not None and (
                row["average_dollar_volume_20d"] < filters["min_adv_dollars"]
            ):
                return False
            if filters.get("above_sma200") is True and (
                row["distance_from_sma_200_pct"] is None
                or row["distance_from_sma_200_pct"] <= 0
            ):
                return False
            if filters.get("min_return_6m") is not None and (
                row["return_6m_pct"] is None
                or row["return_6m_pct"] < filters["min_return_6m"]
            ):
                return False
            if filters.get("max_volatility_pct") is not None and (
                row["realized_volatility_20d_pct"] is None
                or row["realized_volatility_20d_pct"] > filters["max_volatility_pct"]
            ):
                return False
            if filters.get("max_distance_from_high_pct") is not None and (
                abs(row["distance_from_52w_high_pct"])
                > filters["max_distance_from_high_pct"]
            ):
                return False
            return True

        selected = [row for row in rows if included(row)]
        return {
            "as_of": max((row["as_of"] for row in rows), default=None),
            "universe": "configured-live",
            "universe_size": len(rows),
            "match_count": len(selected),
            "preset": preset,
            "filters": filters,
            "ranking": "technical_score_v1",
            "coverage_note": (
                "Ranks Trend and Risk only. Quality, Growth, Valuation, and Catalyst "
                "remain unavailable until point-in-time fundamental/event feeds are wired."
            ),
            "results": selected,
        }
