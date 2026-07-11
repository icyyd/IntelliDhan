"""Indicator golden tests: incremental engines vs independent vectorized references.

The references are written with pandas primitives (ewm/rolling), formulated
independently from the incremental code paths — an incremental-update bug
(wrong seed, off-by-one, state leak) diverges immediately.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from intellidhan_analytics import ATR, EMA, MACD, RSI, SMA, IndicatorEngine, SessionVWAP
from intellidhan_schemas import Bar, Timeframe

rng = np.random.default_rng(42)
N = 600
_closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, N)))
_highs = _closes * (1 + np.abs(rng.normal(0, 0.004, N)))
_lows = _closes * (1 - np.abs(rng.normal(0, 0.004, N)))
_vols = rng.integers(1_000, 50_000, N).astype(float)


def ref_ema(series: pd.Series, n: int) -> pd.Series:
    """SMA-seeded EMA (TradingView convention)."""
    sma = series.rolling(n).mean()
    ema = pd.Series(index=series.index, dtype=float)
    k = 2 / (n + 1)
    for i in range(len(series)):
        if i < n - 1:
            continue
        if i == n - 1 or pd.isna(ema.iloc[i - 1]):
            ema.iloc[i] = sma.iloc[i]
        else:
            ema.iloc[i] = ema.iloc[i - 1] + k * (series.iloc[i] - ema.iloc[i - 1])
    return ema


def ref_rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing == ewm(alpha=1/n) seeded with the simple mean of first n changes
    ag = pd.Series(index=series.index, dtype=float)
    al = pd.Series(index=series.index, dtype=float)
    g_seed = gain.iloc[1 : n + 1].mean()
    l_seed = loss.iloc[1 : n + 1].mean()
    ag.iloc[n] = g_seed
    al.iloc[n] = l_seed
    for i in range(n + 1, len(series)):
        ag.iloc[i] = (ag.iloc[i - 1] * (n - 1) + gain.iloc[i]) / n
        al.iloc[i] = (al.iloc[i - 1] * (n - 1) + loss.iloc[i]) / n
    rs = ag / al
    out = 100 - 100 / (1 + rs)
    out[al == 0] = 100.0
    return out


def ref_atr(h: pd.Series, lo: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    prev_c = c.shift()
    tr = pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - lo.iloc[0]
    atr = pd.Series(index=c.index, dtype=float)
    atr.iloc[n - 1] = tr.iloc[:n].mean()
    for i in range(n, len(c)):
        atr.iloc[i] = (atr.iloc[i - 1] * (n - 1) + tr.iloc[i]) / n
    return atr


@pytest.mark.parametrize("n", [9, 21, 50])
def test_ema_matches_reference(n):
    s = pd.Series(_closes)
    expected = ref_ema(s, n)
    ema = EMA(n)
    for i, x in enumerate(_closes):
        got = ema.update(float(x))
        if i >= n - 1:
            assert got == pytest.approx(expected.iloc[i], rel=1e-9)


def test_rsi_matches_reference():
    s = pd.Series(_closes)
    expected = ref_rsi(s, 14)
    rsi = RSI(14)
    for i, x in enumerate(_closes):
        got = rsi.update(float(x))
        if i >= 14:
            assert got == pytest.approx(expected.iloc[i], rel=1e-9)


def test_rsi_bounds_and_regimes():
    rsi = RSI(14)
    for x in np.linspace(100, 200, 50):  # monotonic rise → RSI 100
        val = rsi.update(float(x))
    assert val == pytest.approx(100.0)


def test_atr_matches_reference():
    h, lo, c = pd.Series(_highs), pd.Series(_lows), pd.Series(_closes)
    expected = ref_atr(h, lo, c, 14)
    atr = ATR(14)
    for i in range(N):
        got = atr.update(float(_highs[i]), float(_lows[i]), float(_closes[i]))
        if i >= 13:
            assert got == pytest.approx(expected.iloc[i], rel=1e-9)


def test_macd_matches_reference():
    s = pd.Series(_closes)
    macd_ref = ref_ema(s, 12) - ref_ema(s, 26)
    macd = MACD()
    for i, x in enumerate(_closes):
        m, _, _ = macd.update(float(x))
        if i >= 25:
            assert m == pytest.approx(macd_ref.iloc[i], rel=1e-9)


def test_vwap_resets_per_session():
    vwap = SessionVWAP()
    vwap.update(101, 99, 100, 1000, "2026-07-09")
    day1 = vwap.update(103, 101, 102, 1000, "2026-07-09")
    assert day1 == pytest.approx(((101 + 99 + 100) / 3 + (103 + 101 + 102) / 3) / 2)
    day2 = vwap.update(50, 48, 49, 500, "2026-07-10")  # new session → full reset
    assert day2 == pytest.approx((50 + 48 + 49) / 3)


def test_sma_window():
    sma = SMA(3)
    assert sma.update(1) is None
    assert sma.update(2) is None
    assert sma.update(3) == pytest.approx(2.0)
    assert sma.update(7) == pytest.approx(4.0)


def test_indicator_engine_end_to_end():
    eng = IndicatorEngine("TEST", Timeframe.M5)
    t0 = datetime(2026, 7, 10, 13, 35, tzinfo=timezone.utc)
    snap = None
    for i in range(N):
        bar = Bar(
            symbol="TEST", timeframe=Timeframe.M5,
            ts_close=t0 + timedelta(minutes=5 * i),
            open=float(_closes[i - 1] if i else _closes[0]),
            high=float(max(_highs[i], _closes[i - 1] if i else _closes[0])),
            low=float(min(_lows[i], _closes[i - 1] if i else _closes[0])),
            close=float(_closes[i]), volume=float(_vols[i]), source="fixture",
        )
        snap = eng.update(bar, session_id="2026-07-10")
    assert snap.ema9 is not None and snap.ema200 is not None
    assert 0 <= snap.rsi14 <= 100
    assert snap.atr14 > 0 and snap.vwap > 0 and snap.rel_volume > 0
    # warmup discipline: a fresh engine's 5th bar has no ema9 yet
    eng2 = IndicatorEngine("TEST", Timeframe.M5)
    for i in range(5):
        bar = Bar(
            symbol="TEST", timeframe=Timeframe.M5, ts_close=t0 + timedelta(minutes=5 * i),
            open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0, source="fixture",
        )
        s5 = eng2.update(bar, session_id="s")
    assert s5.ema9 is None and s5.rsi14 is None


def test_engine_rejects_misrouted_bar():
    eng = IndicatorEngine("AAPL", Timeframe.M5)
    bar = Bar(
        symbol="MSFT", timeframe=Timeframe.M5,
        ts_close=datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc),
        open=1, high=2, low=0.5, close=1.5, volume=10, source="x",
    )
    with pytest.raises(ValueError):
        eng.update(bar, session_id="s")
