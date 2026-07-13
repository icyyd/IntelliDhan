"""SymbolState — the per-symbol working memory the strategies read (doc 01 §1.2, lite).

Fed 5m bars; internally rolls up higher TFs, maintains trend engines per TF,
a daily-pivot level map, session VWAP context, and the opening range.
"""

from __future__ import annotations

from datetime import datetime, time

from intellidhan_analytics.indicators import IndicatorSnapshot
from intellidhan_analytics.levels import LevelMap
from intellidhan_analytics.profile import ProfileBuilder, ProfileState
from intellidhan_analytics.rollup import TimeframeRoller
from intellidhan_analytics.trend import TrendEngine, TrendSnapshot
from intellidhan_ingestor.market_clock import ET, MarketClock
from intellidhan_schemas import Bar, Timeframe

TREND_TFS = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1)


class OpeningRange:
    """9:30–10:00 ET high/low (RULE-T3)."""

    def __init__(self) -> None:
        self.session: str | None = None
        self.high: float | None = None
        self.low: float | None = None
        self.complete = False

    def update(self, bar: Bar, session_id: str) -> None:
        local_t = bar.ts_close.astimezone(ET).time()
        if session_id != self.session:
            self.session, self.high, self.low, self.complete = session_id, None, None, False
        if local_t <= time(10, 0):
            self.high = bar.high if self.high is None else max(self.high, bar.high)
            self.low = bar.low if self.low is None else min(self.low, bar.low)
        elif self.high is not None:
            self.complete = True

    @property
    def mid(self) -> float | None:
        if self.high is None or self.low is None:
            return None
        return (self.high + self.low) / 2


class SymbolState:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.clock = MarketClock()
        self.roller = TimeframeRoller(symbol)
        self.trend: dict[Timeframe, TrendEngine] = {
            tf: TrendEngine(symbol, tf) for tf in TREND_TFS
        }
        self.levels = LevelMap(Timeframe.D1)
        self.profile = ProfileBuilder(symbol)
        self.profile_state: ProfileState | None = None
        self.opening_range = OpeningRange()
        self.last_bar: Bar | None = None
        self.session_id: str | None = None
        self.bars_in_session = 0
        # rolling recent 5m bars for strategy pattern checks (2-candle rule etc.)
        self.recent_5m: list[Bar] = []
        # completed daily bars (seed + live closes) for swing-view charting
        self.recent_daily: list[Bar] = []
        # Session identity, not timestamp, is the D1 uniqueness contract. Yahoo
        # seed bars and replayed 5m rollups use different close timestamps.
        self._daily_sessions: set[str] = set()

    def seed_daily(self, daily_bars: list[Bar]) -> None:
        """Warm-start higher-TF context from backfilled daily history — exactly
        what live boot does before streaming intraday bars (doc 01 §5 Redis-restart
        path). Only bars strictly before the first live bar may be seeded."""
        eng = self.trend[Timeframe.D1]
        for b in sorted(daily_bars, key=lambda x: x.ts_close):
            if b.timeframe != Timeframe.D1 or b.symbol != self.symbol:
                raise ValueError("seed_daily takes this symbol's D1 bars only")
            session = self.clock.session_id(b.ts_close)
            if session in self._daily_sessions:
                continue
            eng.update(b, session)
            self.recent_daily.append(b)
            self._daily_sessions.add(session)
        del self.recent_daily[:-250]
        self._on_daily(eng)

    def on_bar_5m(self, bar: Bar) -> None:
        session = self.clock.session_id(bar.ts_close)
        if session != self.session_id:
            self.session_id = session
            self.bars_in_session = 0
        self.bars_in_session += 1
        self.last_bar = bar
        self.recent_5m.append(bar)
        if len(self.recent_5m) > 100:
            self.recent_5m.pop(0)
        self.opening_range.update(bar, session)
        self.profile_state = self.profile.update(bar, session)
        self.trend[Timeframe.M5].update(bar, session)
        for rolled in self.roller.update(bar):
            if rolled.timeframe in self.trend:
                rolled_session = self.clock.session_id(rolled.ts_close)
                if (
                    rolled.timeframe == Timeframe.D1
                    and rolled_session in self._daily_sessions
                ):
                    continue
                eng = self.trend[rolled.timeframe]
                eng.update(rolled, rolled_session)
                if rolled.timeframe == Timeframe.D1:
                    self.recent_daily.append(rolled)
                    self._daily_sessions.add(rolled_session)
                    if len(self.recent_daily) > 250:
                        self.recent_daily.pop(0)
                    self._on_daily(eng)

    def _on_daily(self, eng: TrendEngine) -> None:
        # feed confirmed daily pivots into the level map with daily ATR widths
        atr = eng.indicators.atr14.value
        for pivot in list(eng.structure.pivots):
            self.levels.on_pivot(pivot, atr)
        if eng.last_indicators is not None:
            self.levels.on_close(eng.last_indicators.close, eng.last_indicators.ts_close)

    # ----- read API for strategies -----

    def mtf_matrix(self) -> dict[Timeframe, float]:
        return {
            tf: eng.snapshot.score
            for tf, eng in self.trend.items()
            if eng.snapshot is not None
        }

    def mtf_states(self) -> dict[str, str]:
        return {
            tf.value: eng.snapshot.state.value
            for tf, eng in self.trend.items()
            if eng.snapshot is not None
        }

    def indicators(self, tf: Timeframe) -> IndicatorSnapshot | None:
        return self.trend[tf].last_indicators

    def trend_snap(self, tf: Timeframe) -> TrendSnapshot | None:
        return self.trend[tf].snapshot

    def trigger_bar(self, tf: Timeframe) -> Bar | None:
        """The genuine completed bar for `tf` — full-bucket OHLC (e.g. the real
        H1 bar), never the raw 5m bar that triggered its rollup. Strategies
        whose trigger_tf != M5 MUST read price/touch geometry from this, not
        from `last_bar`, or entry/stop/target logic silently uses the wrong
        timeframe's range (the exact bug this accessor exists to prevent)."""
        return self.trend[tf].last_bar

    def et_time(self) -> time | None:
        return self.last_bar.ts_close.astimezone(ET).time() if self.last_bar else None

    def ts(self) -> datetime:
        assert self.last_bar is not None
        return self.last_bar.ts_close
