"""Timeframe rollup — higher-TF bars derived from 5m, never fetched (doc 02).

Intraday buckets align to ET clock boundaries anchored at 9:30 (15m: :30/:45/:00/:15;
1H: 9:30-10:30, ...; 4H: 9:30-13:30, 13:30-close). Daily bars complete at session end.
A bucket emits when the first bar of the *next* bucket arrives, or on flush().
"""

from __future__ import annotations

from datetime import datetime, time

from intellidhan_ingestor.market_clock import ET, MarketClock
from intellidhan_schemas import Bar, Timeframe

_clock = MarketClock()

DERIVED: tuple[Timeframe, ...] = (Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1)


def bucket_key(ts_close: datetime, tf: Timeframe) -> str:
    """Stable bucket id for the bar-close instant, session-anchored at 9:30 ET."""
    local = ts_close.astimezone(ET)
    day = local.date().isoformat()
    if tf == Timeframe.D1:
        return day
    session_open = local.replace(hour=9, minute=30, second=0, microsecond=0)
    # ts_close is exclusive end; the instant 9:35 belongs to the first bucket
    elapsed = (local - session_open).total_seconds()
    idx = int((elapsed - 1) // tf.seconds)
    return f"{day}#{idx}"


class TimeframeRoller:
    """Aggregates one symbol's 5m bars into all derived TFs."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._acc: dict[Timeframe, dict] = {}

    def update(self, bar: Bar) -> list[Bar]:
        if bar.timeframe != Timeframe.M5 or bar.symbol != self.symbol:
            raise ValueError("roller consumes this symbol's 5m bars only")
        out: list[Bar] = []
        for tf in DERIVED:
            key = bucket_key(bar.ts_close, tf)
            acc = self._acc.get(tf)
            if acc is not None and acc["key"] != key:
                out.append(self._emit(tf))
                acc = None
            if acc is None:
                self._acc[tf] = {
                    "key": key, "open": bar.open, "high": bar.high, "low": bar.low,
                    "close": bar.close, "volume": bar.volume, "ts_close": bar.ts_close,
                    "source": bar.source,
                }
            else:
                acc["high"] = max(acc["high"], bar.high)
                acc["low"] = min(acc["low"], bar.low)
                acc["close"] = bar.close
                acc["volume"] += bar.volume
                acc["ts_close"] = bar.ts_close
        return out

    def flush(self) -> list[Bar]:
        """Emit all in-progress buckets (end of stream / session close)."""
        out = [self._emit(tf) for tf in list(self._acc)]
        return out

    def _emit(self, tf: Timeframe) -> Bar:
        acc = self._acc.pop(tf)
        return Bar(
            symbol=self.symbol, timeframe=tf, ts_close=acc["ts_close"],
            open=acc["open"], high=acc["high"], low=acc["low"], close=acc["close"],
            volume=acc["volume"], source=f"{acc['source']}+rollup",
        )


def rollup_series(bars_5m: list[Bar]) -> dict[Timeframe, list[Bar]]:
    """Batch helper for backfill/replay: full derived series per TF."""
    by_symbol: dict[str, TimeframeRoller] = {}
    out: dict[Timeframe, list[Bar]] = {tf: [] for tf in DERIVED}
    for bar in bars_5m:
        roller = by_symbol.setdefault(bar.symbol, TimeframeRoller(bar.symbol))
        for emitted in roller.update(bar):
            out[emitted.timeframe].append(emitted)
    for roller in by_symbol.values():
        for emitted in roller.flush():
            out[emitted.timeframe].append(emitted)
    for tf in out:
        out[tf].sort(key=lambda b: (b.ts_close, b.symbol))
    return out


def expected_session_buckets(day: datetime, tf: Timeframe) -> int:
    """How many buckets a full regular session produces (for sentinel checks)."""
    d = day.astimezone(ET).date()
    close = _clock.rth_close(d)
    session_seconds = (
        datetime.combine(d, close, tzinfo=ET) - datetime.combine(d, time(9, 30), tzinfo=ET)
    ).total_seconds()
    return max(1, int(session_seconds // tf.seconds) + (1 if session_seconds % tf.seconds else 0))
