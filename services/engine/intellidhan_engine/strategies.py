"""Strategy registry v1 (doc 08): ORB_BREAKOUT, EMA9_TREND_PULLBACK, DAILY_BREAKOUT.

A strategy's evaluate() returns a RawSignal when its trigger predicate fires on
the just-closed bar; the scorer/veto wall decide whether it becomes a Setup.
F2 (setup quality 0-100) is strategy-owned per doc 03 §3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

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
    pop_based: bool = False       # doc 08 rr_metric POP_BASED: gate on calibrated
                                  # probability instead of the 2:1 R:R rule
    live_eligible: bool = True    # False = SHADOW-only: still evaluated and paper-
                                  # tracked for research, but structurally cannot
                                  # produce a gated/live alert (doc 08 §4 governance;
                                  # see docs/18-enhancement-review.md for why)
    shadow_monitor: bool = False  # Persist qualifying research setups and paper
                                  # outcomes without making them executable.


def _two_closes_beyond(state: SymbolState, level: float, above: bool) -> bool:
    """RULE-T4: two consecutive 5m closes beyond the key level."""
    if len(state.recent_5m) < 2:
        return False
    a, b = state.recent_5m[-2], state.recent_5m[-1]
    return (a.close > level and b.close > level) if above else (
        a.close < level and b.close < level)


class OrbBreakout:
    """RULE-T3/T4: confirmed break of the 9:30–10:00 range with volume (doc 04).

    Parameterized for the tuning harness; defaults are the production config.
    """

    key = "ORB_BREAKOUT"
    module = Module.ZDTE
    trigger_tf = Timeframe.M5

    def __init__(self, key: str = "ORB_BREAKOUT", *, stop_frac: float = 0.35,
                 t_mults=(0.5, 1.0, 1.75), min_relvol: float = 1.5,
                 h1_min: float = 0.0) -> None:
        self.key = key
        self.stop_frac = stop_frac
        self.t_mults = t_mults
        self.min_relvol = min_relvol
        self.h1_min = h1_min

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        orb = state.opening_range
        snap = state.indicators(Timeframe.M5)
        if not orb.complete or snap is None or orb.high is None:
            return None
        bar = state.last_bar
        rel_vol = snap.rel_volume or 0.0
        if self.min_relvol and rel_vol < self.min_relvol:
            return None
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
        # (h1_min > 0 additionally demands positive alignment, not mere non-opposition)
        h1 = state.trend_snap(Timeframe.H1)
        if h1 is not None:
            if direction == Direction.LONG and h1.score < self.h1_min:
                return None
            if direction == Direction.SHORT and h1.score > -self.h1_min:
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
            stop = orb.high - range_size * self.stop_frac  # failed break = back inside range
            targets = [orb.high + range_size * m for m in self.t_mults]
        else:
            entry = bar.close
            stop = orb.low + range_size * self.stop_frac
            targets = [orb.low - range_size * m for m in self.t_mults]
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
    """Trend continuation at the 9/21 EMA in an established intraday trend (doc 04).

    Parameterized for the tuning harness; defaults are the production config.
    """

    module = Module.ZDTE
    trigger_tf = Timeframe.M5

    def __init__(self, key: str = "EMA9_TREND_PULLBACK", *, t_mults=(1.0, 1.8, 3.0),
                 trend_min: float = 40.0, rsi_long: float = 55.0, rsi_short: float = 45.0,
                 min_relvol: float = 0.0, min_trend_day_prob: float = 0.0,
                 require_h1: bool = False) -> None:
        self.key = key
        self.t_mults = t_mults
        self.trend_min = trend_min
        self.rsi_long = rsi_long
        self.rsi_short = rsi_short
        self.min_relvol = min_relvol
        self.min_trend_day_prob = min_trend_day_prob
        self.require_h1 = require_h1

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        snap = state.indicators(Timeframe.M5)
        t5 = state.trend_snap(Timeframe.M5)
        if snap is None or t5 is None or snap.ema9 is None or snap.ema21 is None:
            return None
        if snap.atr14 is None or snap.rsi14 is None:
            return None
        bar = state.last_bar
        if self.min_relvol and (snap.rel_volume or 0.0) < self.min_relvol:
            return None
        if self.min_trend_day_prob:
            prof = state.profile_state
            if prof is None or prof.trend_day_probability < self.min_trend_day_prob:
                return None
        t15 = state.trend_snap(Timeframe.M15)
        h1 = state.trend_snap(Timeframe.H1)
        uptrend = (t5.score >= self.trend_min and snap.ema9 > snap.ema21
                   and (t15 is None or t15.score >= 0)
                   and (not self.require_h1 or (h1 is not None and h1.score >= 20)))
        downtrend = (t5.score <= -self.trend_min and snap.ema9 < snap.ema21
                     and (t15 is None or t15.score <= 0)
                     and (not self.require_h1 or (h1 is not None and h1.score <= -20)))
        if not (uptrend or downtrend):
            return None
        touched = bar.low <= snap.ema9 <= bar.high if uptrend else (
            bar.low <= snap.ema9 <= bar.high)
        if not touched:
            return None
        if uptrend:
            if not (snap.rsi14 >= self.rsi_long and bar.close > snap.ema9):
                return None
            direction, entry = Direction.LONG, bar.close
            stop = snap.ema21 - 0.25 * snap.atr14
            targets = [entry + snap.atr14 * m for m in self.t_mults]
        else:
            if not (snap.rsi14 <= self.rsi_short and bar.close < snap.ema9):
                return None
            direction, entry = Direction.SHORT, bar.close
            stop = snap.ema21 + 0.25 * snap.atr14
            targets = [entry - snap.atr14 * m for m in self.t_mults]
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


class Ema9MtfZeroDte:
    """Conservative SPY/QQQ 9EMA reclaim monitored across four timeframes.

    This is intentionally research-only.  It addresses the largest defects in
    the original EMA strategy: partial higher-timeframe context, loose "touch"
    entries, no liquid-underlying allowlist, and repeated late-day triggers.
    Promotion requires held-out evidence plus forward SHADOW results.
    """

    key = "EMA9_MTF_0DTE"
    module = Module.ZDTE
    trigger_tf = Timeframe.M5

    def __init__(
        self,
        *,
        key: str = "EMA9_MTF_0DTE",
        symbols: tuple[str, ...] = ("SPY", "QQQ"),
        min_relvol: float = 0.8,
        m5_trend: float = 40.0,
        higher_trend: float = 20.0,
        stop_atr_pad: float = 0.15,
        target_rs: tuple[float, ...] = (1.0, 2.0, 3.0),
    ) -> None:
        self.key = key
        self.symbols = frozenset(symbols)
        self.min_relvol = min_relvol
        self.m5_trend = m5_trend
        self.higher_trend = higher_trend
        self.stop_atr_pad = stop_atr_pad
        self.target_rs = target_rs

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        if state.symbol.upper() not in self.symbols or len(state.recent_5m) < 2:
            return None
        local_time = state.et_time()
        # Avoid the opening-price discovery window and late-session 0DTE decay.
        if local_time is None or not (time(10, 0) <= local_time <= time(15, 15)):
            return None

        required = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.D1)
        trends = {tf: state.trend_snap(tf) for tf in required}
        snaps = {tf: state.indicators(tf) for tf in required}
        if any(trends[tf] is None or snaps[tf] is None for tf in required):
            return None
        m5 = snaps[Timeframe.M5]
        if any(
            snap.ema9 is None or snap.ema21 is None
            for snap in snaps.values()
        ):
            return None
        if m5.atr14 is None or m5.rsi14 is None or m5.vwap is None:
            return None
        relvol = m5.rel_volume or 0.0
        if relvol < self.min_relvol:
            return None

        t5 = trends[Timeframe.M5].score
        t15 = trends[Timeframe.M15].score
        h1 = trends[Timeframe.H1].score
        d1 = trends[Timeframe.D1].score
        long_aligned = (
            t5 >= self.m5_trend
            and t15 >= self.higher_trend
            and h1 >= self.higher_trend
            and d1 >= 0
            and all(snaps[tf].ema9 > snaps[tf].ema21 for tf in required[:3])
        )
        short_aligned = (
            t5 <= -self.m5_trend
            and t15 <= -self.higher_trend
            and h1 <= -self.higher_trend
            and d1 <= 0
            and all(snaps[tf].ema9 < snaps[tf].ema21 for tf in required[:3])
        )
        if not (long_aligned or short_aligned):
            return None

        bar = state.recent_5m[-1]
        candle_range = bar.high - bar.low
        if candle_range <= 0:
            return None
        close_location = (bar.close - bar.low) / candle_range
        long_reclaim = (
            long_aligned
            and bar.low <= m5.ema9 < bar.close
            and bar.close > bar.open
            and bar.close > m5.vwap
            and 52 <= m5.rsi14 <= 76
            and close_location >= 0.60
        )
        short_reclaim = (
            short_aligned
            and bar.high >= m5.ema9 > bar.close
            and bar.close < bar.open
            and bar.close < m5.vwap
            and 24 <= m5.rsi14 <= 48
            and close_location <= 0.40
        )
        if not (long_reclaim or short_reclaim):
            return None

        direction = Direction.LONG if long_reclaim else Direction.SHORT
        entry = bar.close
        if direction == Direction.LONG:
            stop = min(bar.low, m5.ema21) - self.stop_atr_pad * m5.atr14
            risk = entry - stop
            targets = [entry + risk * multiple for multiple in self.target_rs]
        else:
            stop = max(bar.high, m5.ema21) + self.stop_atr_pad * m5.atr14
            risk = stop - entry
            targets = [entry - risk * multiple for multiple in self.target_rs]
        if risk <= 0:
            return None

        alignment_strength = min(
            100.0,
            (abs(t5) + abs(t15) + abs(h1) + abs(d1)) / 4,
        )
        quality = min(100.0, 55 + 0.25 * alignment_strength + 10 * min(relvol, 2.0))
        side = "bullish" if direction == Direction.LONG else "bearish"
        return RawSignal(
            strategy=self.key,
            module=self.module,
            direction=direction,
            trigger_tf=self.trigger_tf,
            entry=entry,
            stop=stop,
            targets=targets,
            f2_quality=round(quality, 1),
            explain=(
                f"SPY/QQQ 5m 9EMA {side} reclaim with 15m, 1h, and daily "
                f"trend confirmation; {relvol:.1f}x relative volume and RSI "
                f"{m5.rsi14:.0f}."
            ),
            invalidation=(
                f"5m trend structure fails beyond the 21EMA/ATR stop at {stop:.2f}."
            ),
            live_eligible=False,
            shadow_monitor=True,
        )


class VwapReclaim:
    """Loss and reclaim of VWAP with 2-candle confirmation (RULE-T6, doc 04).

    Parameterized for the tuning harness; defaults are the production config.
    """

    key = "VWAP_RECLAIM"
    module = Module.ZDTE
    trigger_tf = Timeframe.M5

    def __init__(self, key: str = "VWAP_RECLAIM", *, lookback: int = 8,
                 min_before: int = 4, t_mults=(1.0, 1.8, 3.0),
                 stop_lookback: int = 4, stop_atr_pad: float = 0.15,
                 t15_opp: float = 20.0, min_relvol: float = 0.0) -> None:
        self.key = key
        self.lookback = lookback
        self.min_before = min_before
        self.t_mults = t_mults
        self.stop_lookback = stop_lookback
        self.stop_atr_pad = stop_atr_pad
        self.t15_opp = t15_opp
        self.min_relvol = min_relvol

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        snap = state.indicators(Timeframe.M5)
        if snap is None or snap.vwap is None or snap.atr14 is None:
            return None
        recent = state.recent_5m
        if len(recent) < self.lookback or state.bars_in_session < self.lookback:
            return None
        if self.min_relvol and (snap.rel_volume or 0.0) < self.min_relvol:
            return None
        vwap = snap.vwap
        window = recent[-self.lookback:-2]
        below_before = sum(1 for b in window if b.close < vwap)
        above_before = sum(1 for b in window if b.close > vwap)
        two_above = recent[-2].close > vwap and recent[-1].close > vwap
        two_below = recent[-2].close < vwap and recent[-1].close < vwap
        # only the bar completing the confirmation fires (dedupe like ORB)
        three_above = len(recent) >= 3 and recent[-3].close > vwap and two_above
        three_below = len(recent) >= 3 and recent[-3].close < vwap and two_below
        long_reclaim = below_before >= self.min_before and two_above and not three_above
        short_reclaim = above_before >= self.min_before and two_below and not three_below
        if not (long_reclaim or short_reclaim):
            return None
        direction = Direction.LONG if long_reclaim else Direction.SHORT
        # 15m trend must not oppose (RULE-T2)
        t15 = state.trend_snap(Timeframe.M15)
        if t15 is not None:
            if direction == Direction.LONG and t15.score < -self.t15_opp:
                return None
            if direction == Direction.SHORT and t15.score > self.t15_opp:
                return None
        entry = recent[-1].close
        if direction == Direction.LONG:
            stop = min(b.low for b in recent[-self.stop_lookback:]) - self.stop_atr_pad * snap.atr14
            targets = [entry + snap.atr14 * m for m in self.t_mults]
        else:
            stop = max(b.high for b in recent[-self.stop_lookback:]) + self.stop_atr_pad * snap.atr14
            targets = [entry - snap.atr14 * m for m in self.t_mults]
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


class PullbackContinuation:
    """Doc 05 PULLBACK_CONTINUATION — daily uptrend, 1H pullback into the
    21/50 EMA zone, RSI holding, close reclaims the 9EMA. Long-only.

    Parameters are config SW15 from research_swing (2y, 12 symbols):
    train 75.7% / val 76.5% / test 78.1% TP1-win rate, PF 1.15-1.20 —
    the platform's first strategy with held-out evidence >= the 75% bar.
    High-POP / modest-R class: pop_based (doc 08 rr_metric).
    """

    key = "PULLBACK_CONTINUATION"
    module = Module.SWING
    trigger_tf = Timeframe.H1
    T_MULTS = (0.8, 1.6, 2.8)
    D_TREND_MIN = 45.0
    RSI_MIN = 52.0
    STOP_ATR = 1.0

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        ind = state.indicators(Timeframe.H1)
        d_snap = state.trend_snap(Timeframe.D1)
        if (ind is None or d_snap is None or ind.ema9 is None or ind.ema21 is None
                or ind.ema50 is None or ind.atr14 is None or ind.rsi14 is None):
            return None
        if d_snap.score < self.D_TREND_MIN or ind.ema21 <= ind.ema50:
            return None
        bar = state.trigger_bar(Timeframe.H1)  # true H1 OHLC, not the 5m rollup trigger
        if bar is None:
            return None
        zone_lo, zone_hi = ind.ema50, ind.ema21
        touched = bar.low <= zone_hi and bar.low >= zone_lo - 0.5 * ind.atr14
        if not (touched and ind.rsi14 >= self.RSI_MIN and bar.close > ind.ema9):
            return None
        entry = bar.close
        stop = ind.ema50 - self.STOP_ATR * ind.atr14
        if entry <= stop:
            return None
        targets = [entry + m * ind.atr14 for m in self.T_MULTS]
        return RawSignal(
            strategy=self.key, module=self.module, direction=Direction.LONG,
            trigger_tf=self.trigger_tf, entry=entry, stop=stop, targets=targets,
            f2_quality=round(min(50.0 + d_snap.score * 0.5, 100.0), 1),
            pop_based=True,
            live_eligible=False,  # BLOCKED pending full research/production parity —
                                  # see docs/18-enhancement-review.md §3.3/§12. The
                                  # direct-H1 research population and the production
                                  # 5m-rollup population do not yet agree; the 76.5%
                                  # calibration must not gate live alerts until they do.
                                  # Still evaluated + paper-tracked in SHADOW for
                                  # continued forward-evidence accumulation.
            explain=(f"1H pullback into the 21/50 EMA zone within a daily uptrend "
                     f"(D score {d_snap.score:+.0f}), RSI {ind.rsi14:.0f} holding, "
                     f"close reclaimed the 9EMA. High-probability continuation class."),
            invalidation=(f"1H close below {stop:.2f} (EMA50 − {self.STOP_ATR}×ATR) "
                          f"— pullback became a breakdown."),
        )


class PullbackContinuationMacd(PullbackContinuation):
    """Research/shadow variant: baseline pullback plus H1 MACD histogram > 0.

    Frozen from docs/18-enhancement-review.md §12.5–12.6. Separate strategy
    identity so it never reuses PULLBACK_CONTINUATION calibration. Not live
    eligible; requires its own forward-shadow evidence before any promotion.
    """

    key = "PULLBACK_CONTINUATION_MACD"

    def evaluate(self, state: SymbolState) -> RawSignal | None:
        base = super().evaluate(state)
        if base is None:
            return None
        ind = state.indicators(Timeframe.H1)
        if ind is None or ind.macd_histogram is None or ind.macd_histogram <= 0:
            return None
        return RawSignal(
            strategy=self.key,
            module=base.module,
            direction=base.direction,
            trigger_tf=base.trigger_tf,
            entry=base.entry,
            stop=base.stop,
            targets=list(base.targets),
            f2_quality=round(min(base.f2_quality + 5.0, 100.0), 1),
            pop_based=True,
            live_eligible=False,
            shadow_monitor=True,
            explain=(
                base.explain
                + f" H1 MACD histogram positive ({ind.macd_histogram:+.4f}) — momentum "
                "resumption filter (research/shadow identity)."
            ),
            invalidation=base.invalidation,
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
        recent_daily = state.recent_daily
        if pivot_high is None or len(recent_daily) < 2:
            return None
        two_above = all(bar.close > pivot_high for bar in recent_daily[-2:])
        # Emit only when the second confirming daily close completes.
        already_confirmed = (
            len(recent_daily) >= 3
            and recent_daily[-3].close > pivot_high
            and recent_daily[-2].close > pivot_high
        )
        if not two_above or already_confirmed:
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
            explain=(f"Two daily closes above pivot resistance {pivot_high:.2f}; latest "
                     f"volume {rel_vol:.1f}x with D trend {td.state.value}."),
            invalidation=f"Daily close back below the breakout pivot ({pivot_high:.2f}).",
        )


REGISTRY = [
    OrbBreakout(),
    Ema9TrendPullback(),
    Ema9MtfZeroDte(),
    VwapReclaim(),
    PullbackContinuation(),
    PullbackContinuationMacd(),
    DailyBreakout(),
]
