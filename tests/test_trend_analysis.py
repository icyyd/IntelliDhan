"""On-demand trend analysis and fixed-rule backtest tests."""

from datetime import datetime, timedelta, timezone

import pytest

from intellidhan_analytics.trend_analysis import (
    analyze_daily_trend,
    backtest_trend_methods,
)
from intellidhan_gateway.stock_analysis import StockAnalysisService
from intellidhan_schemas import Bar, Timeframe


def daily_bars(n=420, slope=1.0, symbol="TEST"):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        close = 100.0 + slope * i
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=Timeframe.D1,
                ts_close=start + timedelta(days=i),
                open=close - 0.2,
                high=close + 0.2,
                low=close - 0.5,
                close=close,
                volume=1_000_000,
                source="fixture_adjusted",
            )
        )
    return bars


def test_uptrend_report_is_transparent_and_adjusted_source_visible():
    report = analyze_daily_trend(daily_bars(), risk_budget=1000)
    assert report["consensus"]["label"] == "STRONG_UPTREND"
    assert report["methods"]["sma_200_regime"]["signal"] == "BULLISH"
    assert report["methods"]["time_series_momentum"]["positive_horizons"] == 3
    assert report["methods"]["donchian_55_20"]["state"] == "BREAKOUT"
    assert report["source"] == "fixture_adjusted"
    assert report["risk"]["reference_quantity"] > 0
    assert report["analytics_version"] == "trend-analysis-v3"
    assert report["key_levels"]["daily_ema_9"] > 0
    assert report["key_levels"]["breakout_confirmation_55d"] > 0
    assert "not guaranteed" in report["key_levels"]["note"]
    assert report["forecast"]["current_state"] == "UP"
    assert set(report["forecast"]["horizons"]) == {"one_month", "three_months"}


def test_forward_outlook_is_shrunk_non_overlapping_and_walk_forward():
    report = analyze_daily_trend(daily_bars(n=1200, slope=0.1))
    one_month = report["forecast"]["horizons"]["one_month"]
    assert report["forecast"]["sampling"] == "non-overlapping forward windows"
    assert one_month["matched_state_samples"] >= 12
    assert one_month["total_non_overlapping_samples"] < 50
    assert 50 < one_month["probability_positive_pct"] < 100
    low, high = one_month["probability_interval_95_pct"]
    assert 0 <= low <= one_month["probability_positive_pct"] <= high <= 100
    assert one_month["walk_forward_validation"]["evaluation_samples"] >= 12
    assert one_month["confidence"] in {"LOW", "MODERATE", "HIGH"}


def test_downtrend_report_does_not_force_a_bullish_interpretation():
    report = analyze_daily_trend(daily_bars(slope=-0.1))
    assert report["consensus"]["label"] in {"DOWNTREND", "STRONG_DOWNTREND"}
    assert report["methods"]["sma_200_regime"]["signal"] == "BEARISH"
    assert "defensive" in report["interpretation"]


def test_backtest_uses_fixed_methods_costs_and_buy_hold_comparison():
    report = backtest_trend_methods(daily_bars(), cost_bps=12.5)
    assert set(report["methods"]) == {
        "sma_200",
        "time_series_momentum_12m",
        "donchian_55_20",
        "majority_consensus",
    }
    assert report["assumptions"]["round_trip_cost_bps"] == 12.5
    assert report["buy_and_hold"]["exposure_pct"] == 100.0
    assert report["methods"]["majority_consensus"]["cagr_pct"] > 0


def test_short_history_rejected():
    with pytest.raises(ValueError, match="260"):
        analyze_daily_trend(daily_bars(n=200))
    with pytest.raises(ValueError, match="300"):
        backtest_trend_methods(daily_bars(n=280))


class FakeProvider:
    def __init__(self):
        self.calls = 0

    async def get_bars(self, symbol, timeframe, start, end, *, adjusted=False):
        self.calls += 1
        assert timeframe == Timeframe.D1
        assert adjusted is True
        return daily_bars(symbol=symbol)


@pytest.mark.asyncio
async def test_on_demand_service_validates_symbol_and_caches_adjusted_bars():
    provider = FakeProvider()
    service = StockAnalysisService(provider=provider)
    first = await service.analyze(" aapl ", include_backtest=False)
    second = await service.analyze("AAPL", include_backtest=False)
    assert first["symbol"] == "AAPL"
    assert second["backtest"] is None
    assert provider.calls == 1
    with pytest.raises(ValueError, match="symbol"):
        await service.analyze("AAPL<script>")


@pytest.mark.asyncio
async def test_on_demand_service_bounds_its_bar_cache():
    provider = FakeProvider()
    service = StockAnalysisService(provider=provider, max_cache_entries=1)
    await service.analyze("AAPL", include_backtest=False)
    await service.analyze("MSFT", include_backtest=False)
    await service.analyze("AAPL", include_backtest=False)
    assert provider.calls == 3
