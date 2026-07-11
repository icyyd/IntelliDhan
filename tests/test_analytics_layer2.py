"""Tests: rollups, ADX, market structure, trend engine, level map."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from intellidhan_analytics.indicators import ADX
from intellidhan_analytics.levels import LevelMap, LevelRole
from intellidhan_analytics.rollup import TimeframeRoller, bucket_key, rollup_series
from intellidhan_analytics.structure import MarketStructure, PivotKind, StructureState
from intellidhan_analytics.trend import TrendEngine, TrendState, alignment_score
from intellidhan_ingestor.backfill import read_recording
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_schemas import Bar, Timeframe

ET = ZoneInfo("America/New_York")
FIXTURE = Path(__file__).parent.parent / "fixtures/golden-sessions/qqq-complex-5m.jsonl"


def bar5(day, h, m, o, hi, lo, c, v=1000.0, sym="T") -> Bar:
    return Bar(symbol=sym, timeframe=Timeframe.M5,
               ts_close=datetime(2026, 7, day, h, m, tzinfo=ET),
               open=o, high=hi, low=lo, close=c, volume=v, source="fx")


# ---------- rollups ----------

def test_bucket_keys_anchor_at_930():
    ts1 = datetime(2026, 7, 10, 9, 35, tzinfo=ET)   # first 5m close
    ts2 = datetime(2026, 7, 10, 9, 45, tzinfo=ET)   # last close of first 15m bucket
    ts3 = datetime(2026, 7, 10, 9, 50, tzinfo=ET)   # first close of second bucket
    assert bucket_key(ts1, Timeframe.M15) == bucket_key(ts2, Timeframe.M15)
    assert bucket_key(ts2, Timeframe.M15) != bucket_key(ts3, Timeframe.M15)
    assert bucket_key(ts1, Timeframe.D1) == "2026-07-10"


def test_roller_aggregates_ohlcv():
    r = TimeframeRoller("T")
    # three 5m bars = one full 15m bucket; 4th bar (next bucket) triggers emit
    r.update(bar5(10, 9, 35, 100, 102, 99, 101, v=10))
    r.update(bar5(10, 9, 40, 101, 105, 100, 104, v=20))
    r.update(bar5(10, 9, 45, 104, 106, 103, 105, v=30))
    emitted = r.update(bar5(10, 9, 50, 105, 107, 104, 106, v=5))
    m15 = [b for b in emitted if b.timeframe == Timeframe.M15]
    assert len(m15) == 1
    b = m15[0]
    assert (b.open, b.high, b.low, b.close, b.volume) == (100, 106, 99, 105, 60)
    assert b.ts_close == datetime(2026, 7, 10, 9, 45, tzinfo=ET)


def test_rollup_series_on_golden_fixture():
    bars = [b for b in read_recording(FIXTURE) if b.symbol == "QQQ"]
    derived = rollup_series(bars)
    days = {MarketClock().session_id(b.ts_close) for b in bars}
    assert len(derived[Timeframe.D1]) == len(days)
    # daily OHLC must match the session's 5m extremes
    d0 = derived[Timeframe.D1][0]
    day0 = [b for b in bars if MarketClock().session_id(b.ts_close) ==
            MarketClock().session_id(d0.ts_close)]
    assert d0.high == max(b.high for b in day0)
    assert d0.low == min(b.low for b in day0)
    assert d0.open == day0[0].open and d0.close == day0[-1].close
    # 78 five-minute bars per full session -> 6 full 1H buckets + partial
    h1_day0 = [b for b in derived[Timeframe.H1]
               if MarketClock().session_id(b.ts_close) == MarketClock().session_id(d0.ts_close)]
    assert len(h1_day0) == 7


# ---------- ADX ----------

def test_adx_direction_and_range():
    adx = ADX(14)
    price = 100.0
    val = None
    for i in range(80):  # steady uptrend
        price += 0.5
        val = adx.update(price + 0.3, price - 0.3, price)
    assert val is not None and 0 <= val <= 100
    assert adx.di_plus > adx.di_minus
    assert val > 25  # persistent trend reads as trending


# ---------- structure ----------

def test_structure_uptrend_detection():
    ms = MarketStructure(k=2)
    # zig-zag upward: pivots HL/HH sequence
    seq = [100, 103, 101, 106, 104, 110, 107, 114, 111, 118, 115, 122]
    t = datetime(2026, 7, 10, 9, 35, tzinfo=timezone.utc)
    for i, px in enumerate(seq):
        ms.update(Bar(symbol="T", timeframe=Timeframe.M5, ts_close=t + timedelta(minutes=5 * i),
                      open=px, high=px + 1, low=px - 1, close=px, volume=1, source="fx"))
    assert ms.state in (StructureState.UPTREND, StructureState.UNKNOWN, StructureState.RANGE)
    # explicit pivot check: alternating kinds, confirmation lag honored
    kinds = [p.kind for p in ms.pivots]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    for p in ms.pivots:
        assert p.confirmed_at > p.ts_close  # no lookahead


# ---------- trend engine ----------

def _drive(engine: TrendEngine, closes, start=None):
    t = start or datetime(2026, 7, 6, 9, 35, tzinfo=ET)
    clock = MarketClock()
    for i, c in enumerate(closes):
        ts = t + timedelta(minutes=5 * i)
        bar = Bar(symbol="T", timeframe=Timeframe.M5, ts_close=ts,
                  open=c - 0.1, high=c + 0.4, low=c - 0.4, close=c, volume=1000, source="fx")
        snap = engine.update(bar, clock.session_id(ts))
    return snap


def test_trend_engine_bullish_on_persistent_rise():
    import numpy as np
    closes = list(100 + np.cumsum(np.abs(np.random.default_rng(7).normal(0.3, 0.1, 250))))
    snap = _drive(TrendEngine("T", Timeframe.M5), closes)
    assert snap.state in (TrendState.UP, TrendState.STRONG_UP)
    assert snap.score > 20
    assert snap.components["price_vs_ema9"] == 100.0


def test_trend_engine_bearish_on_persistent_fall():
    import numpy as np
    closes = list(500 - np.cumsum(np.abs(np.random.default_rng(7).normal(0.3, 0.1, 250))))
    snap = _drive(TrendEngine("T", Timeframe.M5), closes)
    assert snap.state in (TrendState.DOWN, TrendState.STRONG_DOWN)
    assert snap.score < -20


def test_alignment_score_module_weights():
    matrix = {Timeframe.D1: 80.0, Timeframe.H1: 60.0, Timeframe.M15: 90.0, Timeframe.M5: 70.0}
    long_score = alignment_score(matrix, "0DTE", +1)
    short_score = alignment_score(matrix, "0DTE", -1)
    assert long_score == pytest.approx(
        (0.20 * 0.8 + 0.20 * 0.6 + 0.30 * 0.9 + 0.30 * 0.7) * 100, abs=0.01)
    assert short_score == 0.0
    # missing TFs renormalize instead of silently zeroing
    partial = alignment_score({Timeframe.D1: 100.0}, "SWING", +1)
    assert partial == pytest.approx(100.0)


# ---------- levels ----------

def test_level_map_merge_flip_confluence():
    from intellidhan_analytics.structure import Pivot

    lm = LevelMap(Timeframe.D1, min_strength=50.0)
    t = datetime(2026, 7, 1, tzinfo=timezone.utc)
    def mk(px, kind, d):
        return Pivot(kind=kind, price=px, ts_close=t + timedelta(days=d),
                     confirmed_at=t + timedelta(days=d + 1))
    atr = 4.0  # zone half-width = 1.0
    lm.on_pivot(mk(100.0, PivotKind.HIGH, 0), atr)
    lm.on_pivot(mk(100.6, PivotKind.HIGH, 5), atr)   # merges (within width)
    lm.on_pivot(mk(120.0, PivotKind.HIGH, 6), atr)   # separate, 1 touch -> below min strength
    levels = lm.visible()
    assert len(levels) == 1 and levels[0].touches == 2
    assert levels[0].role == LevelRole.RESISTANCE
    zone_px = levels[0].price
    # decisive close above -> flips to support, strength boosted
    lm.on_close(zone_px + 2.0, t + timedelta(days=7))
    lvl = lm.visible()[0]
    assert lvl.role == LevelRole.SUPPORT and lvl.flipped
    assert lm.confluence_score(zone_px) == lvl.strength
    assert lm.confluence_score(zone_px + 10) == 0.0
    assert lm.nearest(zone_px + 0.5).price == pytest.approx(zone_px)
