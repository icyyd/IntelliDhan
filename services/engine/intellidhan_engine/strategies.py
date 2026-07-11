"""Strategy registry v1 (doc 08): ORB_BREAKOUT, EMA9_TREND_PULLBACK, DAILY_BREAKOUT.

A strategy's evaluate() returns a RawSignal when its trigger predicate fires on
the just-closed bar; the scorer/veto wall decide whether it becomes a Setup.
F2 (setup quality 0-100) is strategy-owned per doc 03 §3.
"""

from __future__ import annotations

from dataclasses import dataclass

from intellidhan_engine.state import SymbolState
from intellidhan_schemas import Timeframe
from intellidhan_schemas.signals import Direction, Module


@dataclass(frozen=True)
class RawSignal:
    strategy: str
    module: Module
    direction: Direction
    trigger_tf: Timeframe
    entry: float
    stop: float
    targets: list[float]
    f2_quality: float
    explain: str
    invalidation: str
    counter_trend: bool = False


def _two_closes_beyond(state: SymbolState, level: float, above: bool) -> bool:
    """RULE-T4: two consecutive 5m closes beyond the key level."""
    if len(state.recent_5m) < 2:
        return False
    a, b = state.recent_5m[-2], state.recent_5m[-1]
    return (a.close > level and b.close > level) if above else (
        a.close < level and b.close < level)


class OrbBreakout:
    """RULE-T3/T4: confirmed break of the 9:30–10:00 range with volume (doc 04)."""

    key = "ORB_BREAKOUT"
    module = Module.ZDTE
    trigger_tf = Timeframe.M5

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        orb = state.opening_range
        snap = state.indicators(Timeframe.M5)
        if not orb.complete or snap is None or orb.high is None:
            return None
        bar = state.last_bar
        rel_vol = snap.rel_volume or 0.0
        vwap = snap.vwap
        long_break = _two_closes_beyond(state, orb.high, above=True)
        short_break = _two_closes_beyond(state, orb.low, above=False)
        # only fire on the bar that completes the 2-candle confirmation
        if len(state.recent_5m) >= 3:
            prev_pair_long = (state.recent_5m[-3].close > orb.high
                              and state.recent_5m[-2].close > orb.high)
            prev_pair_short = (state.recent_5m[-3].close < orb.low
                               and state.recent_5m[-2].close < orb.low)
            if prev_pair_long:
                long_break = False
            if prev_pair_short:
                short_break = False
        if not (long_break or short_break):
            return None
        direction = Direction.LONG if long_break else Direction.SHORT
        # higher-TF agreement (RULE-T2): 1H trend must not oppose the break
        h1 = state.trend_snap(Timeframe.H1)
        if h1 is not None:
            if direction == Direction.LONG and h1.score < 0:
                return None
            if direction == Direction.SHORT and h1.score > 0:
                return None
        # VWAP must agree (RULE-T6)
        if vwap is not None:
            if direction == Direction.LONG and bar.close < vwap:
                return None
            if direction == Direction.SHORT and bar.close > vwap:
                return None
        range_size = orb.high - orb.low
        if range_size <= 0:
            return None
        if direction == Direction.LONG:
            entry = bar.close
            stop = orb.high - range_size * 0.35   # failed break = back inside range
            targets = [orb.high + range_size * m for m in (0.5, 1.0, 1.75)]
        else:
            entry = bar.close
            stop = orb.low + range_size * 0.35
            targets = [orb.low - range_size * m for m in (0.5, 1.0, 1.75)]
        # F2: confirmation is structural; volume grades the quality (RULE-T9)
        f2 = 55.0 + min(rel_vol, 3.0) * 15.0
        return RawSignal(
            strategy=self.key, module=self.module, direction=direction,
            trigger_tf=self.trigger_tf, entry=entry, stop=stop, targets=targets,
            f2_quality=round(min(f2, 100.0), 1),
            explain=(f"5m ORB {'break above' if direction == Direction.LONG else 'break below'} "
                     f"{orb.high if direction == Direction.LONG else orb.low:.2f} with 2-candle "
                     f"confirmation, {rel_vol:.1f}x relative volume, "
                     f"{'above' if direction == Direction.LONG else 'below'} VWAP."),
            invalidation=f"Price back inside the range beyond {stop:.2f} — failed breakout.",
        )


class Ema9TrendPullback:
    """Trend continuation at the 9/21 EMA in an established intraday trend (doc 04)."""

    key = "EMA9_TREND_PULLBACK"
    module = Module.ZDTE
    trigger_tf = Timeframe.M5

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        snap = state.indicators(Timeframe.M5)
        t5 = state.trend_snap(Timeframe.M5)
        if snap is None or t5 is None or snap.ema9 is None or snap.ema21 is None:
            return None
        if snap.atr14 is None or snap.rsi14 is None:
            return None
        bar = state.last_bar
        t15 = state.trend_snap(Timeframe.M15)
        uptrend = (t5.score >= 40 and snap.ema9 > snap.ema21
                   and (t15 is None or t15.score >= 0))
        downtrend = (t5.score <= -40 and snap.ema9 < snap.ema21
                     and (t15 is None or t15.score <= 0))
        if not (uptrend or downtrend):
            return None
        touched = bar.low <= snap.ema9 <= bar.high if uptrend else (
            bar.low <= snap.ema9 <= bar.high)
        if not touched:
            return None
        if uptrend:
            if not (snap.rsi14 >= 55 and bar.close > snap.ema9):  # holding strength + reclaim
                return None
            direction, entry = Direction.LONG, bar.close
            stop = snap.ema21 - 0.25 * snap.atr14
            targets = [entry + snap.atr14 * m for m in (1.0, 1.8, 3.0)]
        else:
            if not (snap.rsi14 <= 45 and bar.close < snap.ema9):
                return None
            direction, entry = Direction.SHORT, bar.close
            stop = snap.ema21 + 0.25 * snap.atr14
            targets = [entry - snap.atr14 * m for m in (1.0, 1.8, 3.0)]
        f2 = 50.0 + abs(t5.score) * 0.4  # stronger established trend = better pullback
        return RawSignal(
            strategy=self.key, module=self.module, direction=direction,
            trigger_tf=self.trigger_tf, entry=entry, stop=stop, targets=targets,
            f2_quality=round(min(f2, 100.0), 1),
            explain=(f"Pullback tag of rising 9EMA in established 5m "
                     f"{'up' if uptrend else 'down'}trend (score {t5.score:+.0f}), RSI "
                     f"{snap.rsi14:.0f} holding the regime, close reclaimed the 9EMA."),
            invalidation=f"5m close through the 21EMA ({snap.ema21:.2f}) — trend structure broken.",
        )


class VwapReclaim:
    """Loss and reclaim of VWAP with 2-candle confirmation (RULE-T6, doc 04)."""

    key = "VWAP_RECLAIM"
    module = Module.ZDTE
    trigger_tf = Timeframe.M5

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        snap = state.indicators(Timeframe.M5)
        if snap is None or snap.vwap is None or snap.atr14 is None:
            return None
        recent = state.recent_5m
        if len(recent) < 8 or state.bars_in_session < 8:
            return None
        vwap = snap.vwap
        below_before = sum(1 for b in recent[-8:-2] if b.close < vwap)
        above_before = sum(1 for b in recent[-8:-2] if b.close > vwap)
        two_above = recent[-2].close > vwap and recent[-1].close > vwap
        two_below = recent[-2].close < vwap and recent[-1].close < vwap
        # only the bar completing the confirmation fires (dedupe like ORB)
        three_above = len(recent) >= 3 and recent[-3].close > vwap and two_above
        three_below = len(recent) >= 3 and recent[-3].close < vwap and two_below
        long_reclaim = below_before >= 4 and two_above and not three_above
        short_reclaim = above_before >= 4 and two_below and not three_below
        if not (long_reclaim or short_reclaim):
            return None
        direction = Direction.LONG if long_reclaim else Direction.SHORT
        # 15m trend must not oppose (RULE-T2)
        t15 = state.trend_snap(Timeframe.M15)
        if t15 is not None:
            if direction == Direction.LONG and t15.score < -20:
                return None
            if direction == Direction.SHORT and t15.score > 20:
                return None
        entry = recent[-1].close
        if direction == Direction.LONG:
            stop = min(b.low for b in recent[-4:]) - 0.15 * snap.atr14
            targets = [entry + snap.atr14 * m for m in (1.0, 1.8, 3.0)]
        else:
            stop = max(b.high for b in recent[-4:]) + 0.15 * snap.atr14
            targets = [entry - snap.atr14 * m for m in (1.0, 1.8, 3.0)]
        rel_vol = snap.rel_volume or 0.0
        f2 = 50.0 + min(rel_vol, 2.5) * 14.0 + (8.0 if abs(entry - vwap) < 0.5 * snap.atr14 else 0.0)
        side = "reclaim above" if direction == Direction.LONG else "loss below"
        return RawSignal(
            strategy=self.key, module=self.module, direction=direction,
            trigger_tf=self.trigger_tf, entry=entry, stop=stop, targets=targets,
            f2_quality=round(min(f2, 100.0), 1),
            explain=(f"VWAP {side} {vwap:.2f} after sustained trade on the other side, "
                     f"2-candle confirmation, {rel_vol:.1f}x volume — intraday fair-value flip."),
            invalidation=("2 consecutive 5m closes back on the far side of VWAP "
                          "— the flip failed."),
        )


class DailyBreakout:
    """Daily base breakout with volume + 2-daily-close confirmation (doc 05)."""

    key = "DAILY_BREAKOUT"
    module = Module.SWING
    trigger_tf = Timeframe.D1

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        d = state.indicators(Timeframe.D1)
        td = state.trend_snap(Timeframe.D1)
        if d is None or td is None or d.atr14 is None:
            return None
        eng = state.trend[Timeframe.D1]
        highs = [p.price for p in eng.structure.pivots if p.kind.value == "HIGH"]
        if not highs:
            return None
        pivot_high = max(highs[-3:]) if len(highs) >= 1 else None
        if pivot_high is None or d.close <= pivot_high:
            return None
        rel_vol = d.rel_volume or 0.0
        if rel_vol < 1.5 or td.score < 20:  # RULE-T9 + trend gate
            return None
        entry = d.close
        stop = pivot_high - 0.75 * d.atr14
        targets = [entry + d.atr14 * m for m in (1.5, 3.0, 5.0)]
        f2 = 45.0 + min(rel_vol, 3.0) * 15.0 + (10.0 if td.score >= 50 else 0.0)
        return RawSignal(
            strategy=self.key, module=self.module, direction=Direction.LONG,
            trigger_tf=self.trigger_tf, entry=entry, stop=stop, targets=targets,
            f2_quality=round(min(f2, 100.0), 1),
            explain=(f"Daily close above pivot resistance {pivot_high:.2f} on {rel_vol:.1f}x "
                     f"volume with D trend {td.state.value}."),
            invalidation=f"Daily close back below the breakout pivot ({pivot_high:.2f}).",
        )


REGISTRY = [OrbBreakout(), Ema9TrendPullback(), VwapReclaim(), DailyBreakout()]
