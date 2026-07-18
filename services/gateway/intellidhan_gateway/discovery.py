"""Compact end-of-day technical discovery service.

This intentionally does not invent fundamentals.  The result separates the
available Trend/Risk evidence from the four unavailable business-data pillars.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any

import numpy as np

from intellidhan_ingestor.providers import YahooProvider
from intellidhan_ingestor.market_clock import ET, MarketClock
from intellidhan_ingestor.sentinel import check_bars
from intellidhan_schemas import Bar, DataQuality, Timeframe
from intellidhan_analytics.smart_scan import SMART_SCAN_VERSION, rank_smart_plays

from intellidhan_gateway.universe import load_live_symbols


PRESETS: dict[str, dict[str, Any]] = {
    "smart_momentum": {"play_type": "MOMENTUM_LEADER"},
    "breakout_watch": {"play_type": "BREAKOUT_WATCH"},
    "trend_pullbacks": {"play_type": "TREND_PULLBACK"},
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

DAILY_SETTLEMENT_DELAY = timedelta(minutes=20)


def _native_json(value: Any) -> Any:
    """Convert NumPy scalars at the analytics boundary before API serialization."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _native_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native_json(item) for item in value]
    return value


def _completed_session_boundary(now: datetime, clock: MarketClock) -> tuple[date, datetime]:
    """Return the latest settled US session and Yahoo's exclusive end boundary."""
    local = now.astimezone(ET)
    candidate = local.date()
    settled_at = datetime.combine(candidate, clock.rth_close(candidate), tzinfo=ET)
    if not clock.is_trading_day(candidate) or local < settled_at + DAILY_SETTLEMENT_DELAY:
        candidate -= timedelta(days=1)
    while not clock.is_trading_day(candidate):
        candidate -= timedelta(days=1)
    exclusive_end = datetime.combine(candidate + timedelta(days=1), dt_time.min, tzinfo=ET)
    return candidate, exclusive_end.astimezone(timezone.utc)


def _return_pct(closes: np.ndarray, sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return None
    return round((closes[-1] / closes[-sessions - 1] - 1.0) * 100.0, 2)


def _skip_month_return_pct(closes: np.ndarray, sessions: int) -> float | None:
    """Return from ``sessions`` ago through 21 sessions ago (for 12–1 momentum)."""
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return None
    return round((closes[-22] / closes[-sessions - 1] - 1.0) * 100.0, 2)


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
    highs = np.asarray([bar.high for bar in ordered], dtype=float)
    lows = np.asarray([bar.low for bar in ordered], dtype=float)
    volumes = np.asarray([bar.volume for bar in ordered], dtype=float)
    if len(closes) < 60:
        raise LookupError(f"insufficient completed daily history for {symbol}")
    price = float(closes[-1])
    sma200 = float(np.mean(closes[-200:])) if len(closes) >= 200 else None
    sma200_prior = float(np.mean(closes[-220:-20])) if len(closes) >= 220 else None
    sma50 = float(np.mean(closes[-50:]))
    sma50_prior = float(np.mean(closes[-70:-20])) if len(closes) >= 70 else None
    high_52w = float(np.max(closes[-252:]))
    distance_high = (price / high_52w - 1.0) * 100.0
    adv_dollars = float(np.mean(closes[-20:] * volumes[-20:]))
    volatility = _annualized_volatility(closes)
    ret_6m = _return_pct(closes, 126)
    ret_12m = _return_pct(closes, 252)
    prior_55d_high = float(np.max(highs[-56:-1]))
    prior_20d_high = float(np.max(highs[-21:-1]))
    prior_20d_low = float(np.min(lows[-21:-1]))
    recent_volume = float(np.mean(volumes[-5:]))
    prior_volume = float(np.mean(volumes[-25:-5]))
    vol_60d = _annualized_volatility(closes, 60)
    true_ranges = np.maximum(
        highs[-14:] - lows[-14:],
        np.maximum(
            np.abs(highs[-14:] - closes[-15:-1]),
            np.abs(lows[-14:] - closes[-15:-1]),
        ),
    )

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

    return _native_json({
        "symbol": symbol,
        "as_of": ordered[-1].ts_close.isoformat(),
        "price": round(price, 2),
        "return_1m_pct": _return_pct(closes, 21),
        "return_3m_pct": _return_pct(closes, 63),
        "return_6m_pct": ret_6m,
        "return_12m_pct": ret_12m,
        "return_3_1_pct": _skip_month_return_pct(closes, 63),
        "return_6_1_pct": _skip_month_return_pct(closes, 126),
        "return_12_1_pct": _skip_month_return_pct(closes, 252),
        "sma_50": round(sma50, 2),
        "distance_from_sma_50_pct": round((price / sma50 - 1.0) * 100.0, 2),
        "sma_50_slope_20d_pct": (
            round((sma50 / sma50_prior - 1.0) * 100.0, 2) if sma50_prior else None
        ),
        "sma_200": round(sma200, 2) if sma200 is not None else None,
        "distance_from_sma_200_pct": (
            round((price / sma200 - 1.0) * 100.0, 2) if sma200 else None
        ),
        "sma_200_slope_20d_pct": (
            round((sma200 / sma200_prior - 1.0) * 100.0, 2)
            if sma200 and sma200_prior
            else None
        ),
        "percent_of_52w_high": round(price / high_52w * 100.0, 2),
        "distance_from_52w_high_pct": round(distance_high, 2),
        "realized_volatility_20d_pct": volatility,
        "realized_volatility_60d_pct": vol_60d,
        "volatility_ratio_20d_to_60d": (
            round(volatility / vol_60d, 2) if volatility and vol_60d else None
        ),
        "atr_14_pct": round(float(np.mean(true_ranges)) / price * 100.0, 2),
        "prior_55d_high": round(prior_55d_high, 2),
        "distance_to_prior_55d_high_pct": round(
            (price / prior_55d_high - 1.0) * 100.0, 2
        ),
        "prior_20d_high": round(prior_20d_high, 2),
        "prior_20d_low": round(prior_20d_low, 2),
        "volume_ratio_5d_to_prior_20d": (
            round(recent_volume / prior_volume, 2) if prior_volume > 0 else None
        ),
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
    })


class DiscoveryService:
    def __init__(self, provider: YahooProvider | None = None, cache_seconds: int = 900) -> None:
        self.provider = provider or YahooProvider()
        self.cache_seconds = cache_seconds
        self.clock = MarketClock()
        self._cache: tuple[float, dict[str, Any]] | None = None

    async def _load_row(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        completed_session: date,
    ) -> dict[str, Any]:
        bars = await self.provider.get_bars(
            symbol, Timeframe.D1, start, end, adjusted=True
        )
        report = check_bars(symbol, bars)
        if report.quality != DataQuality.OK:
            raise LookupError(f"daily data quality rejected: {'; '.join(report.issues)}")
        completed_id = completed_session.isoformat()
        bars = [bar for bar in bars if self.clock.session_id(bar.ts_close) <= completed_id]
        if not bars:
            raise LookupError("no completed adjusted daily history")
        latest_id = self.clock.session_id(max(bars, key=lambda bar: bar.ts_close).ts_close)
        if latest_id != completed_id:
            raise LookupError(
                f"stale daily history: expected {completed_id}, received {latest_id}"
            )
        return screen_row(symbol, bars)

    async def universe_scan(self, refresh: bool = False) -> dict[str, Any]:
        if not refresh and self._cache and time.monotonic() - self._cache[0] < self.cache_seconds:
            return self._cache[1]
        now = datetime.now(timezone.utc)
        completed_session, end = _completed_session_boundary(now, self.clock)
        start = end - timedelta(days=550)
        symbols = load_live_symbols()
        results = await asyncio.gather(
            *(
                self._load_row(symbol, start, end, completed_session)
                for symbol in symbols
            ),
            return_exceptions=True,
        )
        rows: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        for symbol, item in zip(symbols, results):
            if isinstance(item, dict):
                rows.append(item)
            else:
                errors[symbol] = str(item)[:240] or type(item).__name__
        rows = rank_smart_plays(rows)
        scan = {
            "rows": rows,
            "configured_count": len(symbols),
            "attempted_count": len(results),
            "succeeded_count": len(rows),
            "failed_count": len(errors),
            "errors": errors,
            "complete": not errors,
            "completed_session": completed_session.isoformat(),
        }
        # Never preserve a partial provider response as if it were a healthy scan.
        if not errors:
            self._cache = (time.monotonic(), scan)
        return scan

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
        scan = await self.universe_scan(refresh=refresh)
        rows = scan["rows"]

        def included(row: dict[str, Any]) -> bool:
            play_type = filters.get("play_type")
            if play_type and not row.get("plays", {}).get(play_type, {}).get("eligible"):
                return False
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

        selected = [dict(row) for row in rows if included(row)]
        play_type = filters.get("play_type")
        for row in selected:
            row["selected_play"] = (
                row.get("plays", {}).get(play_type) if play_type else row.get("best_play")
            )
        selected.sort(
            key=lambda row: (row.get("selected_play") or {}).get("score", 0.0),
            reverse=True,
        )
        return {
            "as_of": max((row["as_of"] for row in rows), default=None),
            "universe": "configured-live",
            "universe_size": scan["configured_count"],
            "attempted_count": scan["attempted_count"],
            "succeeded_count": scan["succeeded_count"],
            "failed_count": scan["failed_count"],
            "errors": scan["errors"],
            "complete": scan["complete"],
            "completed_session": scan["completed_session"],
            "match_count": len(selected),
            "preset": preset,
            "filters": filters,
            "ranking": SMART_SCAN_VERSION,
            "ranking_scope": "configured-live universe only",
            "coverage_note": (
                "Ranks price trend, relative momentum, breakout participation, and risk. "
                "Fundamental quality and catalysts remain unavailable until point-in-time "
                "fundamental/event feeds are wired."
            ),
            "results": selected,
        }
