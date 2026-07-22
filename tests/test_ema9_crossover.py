"""Regression tests for the literal close-cross and continuous stop model."""

from datetime import datetime, timedelta, timezone

import pytest

from intellidhan_learning.ema9_crossover import (
    Ema9CrossoverConfig,
    _is_flatten_bar,
    backtest_ema9_crossover,
    performance_report,
    tune_ema9_crossover,
)
from intellidhan_schemas import Bar, Timeframe


def bar(i: int, close: float, *, open_: float | None = None,
        high: float | None = None, low: float | None = None) -> Bar:
    open_ = close if open_ is None else open_
    high = max(open_, close) + 0.25 if high is None else high
    low = min(open_, close) - 0.25 if low is None else low
    return Bar(
        symbol="SPY", timeframe=Timeframe.M30,
        ts_close=datetime(2025, 1, 2, tzinfo=timezone.utc) + timedelta(minutes=30 * i),
        open=open_, high=high, low=low, close=close, volume=100_000, source="test",
    )


def cfg(**overrides) -> Ema9CrossoverConfig:
    values = {"timeframe": Timeframe.M30, "rth_only": False,
              "cost_bps_per_side": 0, "require_ema21_alignment": False}
    values.update(overrides)
    return Ema9CrossoverConfig(**values)


def test_coarse_bar_straddling_flatten_deadline_is_flagged():
    # 15:30–16:00 ET 30m bar: flattening must happen at its open, not after
    # letting the position traverse the entire coarse candle.
    ts_close = datetime(2025, 1, 2, 21, 0, tzinfo=timezone.utc)
    coarse = Bar(symbol="SPY", timeframe=Timeframe.M30, ts_close=ts_close,
                 open=100, high=101, low=99, close=100.5, volume=100, source="test")
    assert _is_flatten_bar(coarse, cfg()) is True


def test_no_signal_before_ema_and_next_bar_entry():
    # 14 flat bars warm the ATR, then the first close above EMA9 arms a buy.
    bars = [bar(i, 100.0) for i in range(20)]
    bars += [bar(20, 102.0), bar(21, 103.0), bar(22, 104.0)]
    trades = backtest_ema9_crossover(bars, cfg(confirmation_bars=1))
    assert len(trades) == 1
    assert trades[0].signal_ts == bars[20].ts_close
    assert trades[0].entry_ts == bars[21].ts_close - timedelta(minutes=30)
    assert trades[0].entry == pytest.approx(bars[21].open)


def test_two_close_confirmation_delays_entry():
    bars = [bar(i, 100.0) for i in range(20)]
    bars += [bar(20, 102.0), bar(21, 103.0), bar(22, 104.0), bar(23, 105.0)]
    trades = backtest_ema9_crossover(bars, cfg(confirmation_bars=2))
    assert len(trades) == 1
    assert trades[0].signal_ts == bars[21].ts_close
    assert trades[0].entry_ts == bars[22].ts_close - timedelta(minutes=30)


def test_stop_is_continuous_monotonic_and_stop_first():
    bars = [bar(i, 100.0) for i in range(20)]
    bars += [bar(20, 102.0), bar(21, 104.0), bar(22, 105.0)]
    # The next bar trades through the ratcheted stop.  It also closes below
    # EMA9; stop-first must win and the stop cannot widen back toward 100.
    bars += [bar(23, 101.0, high=105.0, low=100.0)]
    trades = backtest_ema9_crossover(bars, cfg(confirmation_bars=1, trail_atr=1.5))
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "TRAIL_STOP"
    assert trade.final_stop >= trade.initial_stop
    assert trade.stop_updates >= 1


def test_gap_through_stop_records_fallback():
    bars = [bar(i, 100.0) for i in range(20)]
    bars += [bar(20, 102.0), bar(21, 104.0)]
    # The opening gap is materially below the stop-limit offset, requiring the
    # conservative market fallback at the open.
    bars += [bar(22, 95.0, open_=95.0, high=96.0, low=94.0)]
    trades = backtest_ema9_crossover(bars, cfg(confirmation_bars=1))
    assert len(trades) == 1
    assert trades[0].gap_fallback is True
    assert trades[0].exit == pytest.approx(95.0)


def test_report_is_cost_aware():
    bars = [bar(i, 100.0) for i in range(20)]
    bars += [bar(20, 102.0), bar(21, 104.0), bar(22, 105.0), bar(23, 101.0)]
    free = performance_report(backtest_ema9_crossover(bars, cfg(confirmation_bars=1)))
    charged = performance_report(backtest_ema9_crossover(
        bars, cfg(confirmation_bars=1, cost_bps_per_side=10)))
    assert charged["avg_net_r"] < free["avg_net_r"]


def test_tuner_does_not_promote_small_samples():
    bars = [bar(i, 100.0 + (i % 2) * 0.5) for i in range(40)]
    report = tune_ema9_crossover(bars, cfg(confirmation_bars=1))
    assert report["winner"] is None
    assert "n>=30" in report["selection_rule"]
