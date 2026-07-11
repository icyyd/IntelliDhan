"""Incremental indicators — O(1) per bar, deterministic, no wall-clock (doc 01 §7).

Conventions (locked, tested):
- EMA seeds with the SMA of the first `n` values (TradingView convention), then
  ema = prev + k*(x - prev), k = 2/(n+1).
- RSI and ATR use Wilder smoothing seeded with the simple mean of the first `n`
  changes / true ranges.
- Session VWAP resets on session_id change and uses typical price (H+L+C)/3.

Every update() is called exactly once per closed bar (bar-close semantics,
RULE-T4). Values are None until the indicator has enough data — consumers must
handle warmup explicitly; emitting a half-warmed number would violate G7.
"""

from __future__ import annotations

from collections import deque

from pydantic import BaseModel

from intellidhan_schemas import Bar, Timeframe


class SMA:
    def __init__(self, n: int) -> None:
        self.n = n
        self._window: deque[float] = deque(maxlen=n)
        self._sum = 0.0

    def update(self, x: float) -> float | None:
        if len(self._window) == self.n:
            self._sum -= self._window[0]
        self._window.append(x)
        self._sum += x
        return self.value

    @property
    def value(self) -> float | None:
        return self._sum / self.n if len(self._window) == self.n else None


class EMA:
    def __init__(self, n: int) -> None:
        self.n = n
        self._k = 2.0 / (n + 1)
        self._seed = SMA(n)
        self._value: float | None = None

    def update(self, x: float) -> float | None:
        if self._value is None:
            seeded = self._seed.update(x)
            if seeded is not None:
                self._value = seeded
        else:
            self._value += self._k * (x - self._value)
        return self._value

    @property
    def value(self) -> float | None:
        return self._value


class _WilderSmoother:
    """avg = (prev*(n-1) + x) / n, seeded with the mean of the first n samples."""

    def __init__(self, n: int) -> None:
        self.n = n
        self._seed_sum = 0.0
        self._seed_count = 0
        self._value: float | None = None

    def update(self, x: float) -> float | None:
        if self._value is None:
            self._seed_sum += x
            self._seed_count += 1
            if self._seed_count == self.n:
                self._value = self._seed_sum / self.n
        else:
            self._value = (self._value * (self.n - 1) + x) / self.n
        return self._value

    @property
    def value(self) -> float | None:
        return self._value


class RSI:
    def __init__(self, n: int = 14) -> None:
        self.n = n
        self._gain = _WilderSmoother(n)
        self._loss = _WilderSmoother(n)
        self._prev_close: float | None = None
        self._value: float | None = None

    def update(self, close: float) -> float | None:
        if self._prev_close is not None:
            change = close - self._prev_close
            avg_gain = self._gain.update(max(change, 0.0))
            avg_loss = self._loss.update(max(-change, 0.0))
            if avg_gain is not None and avg_loss is not None:
                self._value = (
                    100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
                )
        self._prev_close = close
        return self._value

    @property
    def value(self) -> float | None:
        return self._value


class MACD:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self._fast = EMA(fast)
        self._slow = EMA(slow)
        self._signal = EMA(signal)
        self.macd: float | None = None
        self.signal: float | None = None
        self.histogram: float | None = None

    def update(self, close: float) -> tuple[float | None, float | None, float | None]:
        f = self._fast.update(close)
        s = self._slow.update(close)
        if f is not None and s is not None:
            self.macd = f - s
            self.signal = self._signal.update(self.macd)
            if self.signal is not None:
                self.histogram = self.macd - self.signal
        return self.macd, self.signal, self.histogram


class ATR:
    def __init__(self, n: int = 14) -> None:
        self.n = n
        self._smoother = _WilderSmoother(n)
        self._prev_close: float | None = None
        self._value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._value = self._smoother.update(tr)
        self._prev_close = close
        return self._value

    @property
    def value(self) -> float | None:
        return self._value


class SessionVWAP:
    """Intraday fair value (RULE-T6); resets when session_id changes."""

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._pv = 0.0
        self._vol = 0.0

    def update(self, high: float, low: float, close: float, volume: float, session_id: str) -> float | None:
        if session_id != self._session_id:
            self._session_id, self._pv, self._vol = session_id, 0.0, 0.0
        typical = (high + low + close) / 3.0
        self._pv += typical * volume
        self._vol += volume
        return self.value

    @property
    def value(self) -> float | None:
        return self._pv / self._vol if self._vol > 0 else None


class IndicatorSnapshot(BaseModel):
    """Payload for state.indicators.{symbol}.{tf} (doc 01 §1.1)."""

    symbol: str
    timeframe: Timeframe
    ts_close: object  # datetime; kept loose here, validated by schema package in Phase 1
    close: float
    ema9: float | None
    ema21: float | None
    ema50: float | None
    ema200: float | None
    rsi14: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    atr14: float | None
    vwap: float | None
    rel_volume: float | None


class IndicatorEngine:
    """Per symbol×timeframe indicator stack; one instance per stream (doc 01 §2②)."""

    def __init__(self, symbol: str, timeframe: Timeframe) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.ema9, self.ema21, self.ema50, self.ema200 = EMA(9), EMA(21), EMA(50), EMA(200)
        self.rsi14 = RSI(14)
        self.macd = MACD()
        self.atr14 = ATR(14)
        self.vwap = SessionVWAP()
        self._vol_sma20 = SMA(20)

    def update(self, bar: Bar, session_id: str) -> IndicatorSnapshot:
        if bar.symbol != self.symbol or bar.timeframe != self.timeframe:
            raise ValueError("bar routed to wrong engine instance")
        c = bar.close
        prior_avg_vol = self._vol_sma20.value  # relative volume compares to *prior* 20 bars
        self._vol_sma20.update(bar.volume)
        macd, macd_sig, macd_hist = self.macd.update(c)
        return IndicatorSnapshot(
            symbol=bar.symbol,
            timeframe=bar.timeframe,
            ts_close=bar.ts_close,
            close=c,
            ema9=self.ema9.update(c),
            ema21=self.ema21.update(c),
            ema50=self.ema50.update(c),
            ema200=self.ema200.update(c),
            rsi14=self.rsi14.update(c),
            macd=macd,
            macd_signal=macd_sig,
            macd_histogram=macd_hist,
            atr14=self.atr14.update(bar.high, bar.low, c),
            vwap=self.vwap.update(bar.high, bar.low, c, bar.volume, session_id),
            rel_volume=(bar.volume / prior_avg_vol if prior_avg_vol else None),
        )
