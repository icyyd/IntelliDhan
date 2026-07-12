"""MTF trend engine — doc 03 §2, exact weights.

Per timeframe: weighted vote of EMA stack (0.30), price vs 9EMA (0.15),
market structure (0.25), RSI regime (0.15), ADX/DMI (0.15) → score −100..+100
and a TrendState. The alignment matrix aggregates per-module TF weights.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from intellidhan_analytics.indicators import IndicatorEngine, IndicatorSnapshot
from intellidhan_analytics.structure import MarketStructure, StructureState
from intellidhan_schemas import Bar, Timeframe


class TrendState(str, Enum):
    STRONG_UP = "STRONG_UP"
    UP = "UP"
    NEUTRAL = "NEUTRAL"
    DOWN = "DOWN"
    STRONG_DOWN = "STRONG_DOWN"


def state_from_score(score: float) -> TrendState:
    if score >= 60:
        return TrendState.STRONG_UP
    if score >= 20:
        return TrendState.UP
    if score <= -60:
        return TrendState.STRONG_DOWN
    if score <= -20:
        return TrendState.DOWN
    return TrendState.NEUTRAL


class TrendSnapshot(BaseModel):
    symbol: str
    timeframe: Timeframe
    score: float                 # −100..+100
    state: TrendState
    components: dict[str, float]  # each −100..+100 pre-weight, for UI popovers


# doc 03 §2 module TF weights
MODULE_TF_WEIGHTS: dict[str, dict[Timeframe, float]] = {
    "0DTE": {Timeframe.D1: 0.20, Timeframe.H1: 0.20, Timeframe.M15: 0.30, Timeframe.M5: 0.30},
    "SWING": {Timeframe.W1: 0.20, Timeframe.D1: 0.35, Timeframe.H4: 0.30, Timeframe.H1: 0.15},
    "LEAPS": {Timeframe.MN1: 0.35, Timeframe.W1: 0.35, Timeframe.D1: 0.30},
    "HODL": {Timeframe.MN1: 0.50, Timeframe.W1: 0.30, Timeframe.D1: 0.20},
}

WEIGHTS = {"ema_stack": 0.30, "price_vs_ema9": 0.15, "structure": 0.25,
           "rsi_regime": 0.15, "adx_dmi": 0.15}


class TrendEngine:
    """One per symbol×timeframe; consumes bars, keeps its own indicator + structure state."""

    def __init__(self, symbol: str, timeframe: Timeframe) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.indicators = IndicatorEngine(symbol, timeframe)
        self.structure = MarketStructure(k=2)
        self._prev_ema9: float | None = None
        self.snapshot: TrendSnapshot | None = None
        self.last_indicators: IndicatorSnapshot | None = None
        self.last_bar: Bar | None = None  # the actual completed bar for THIS timeframe —
                                          # e.g. the true H1 bar (full-hour OHLC), never the
                                          # 5m bar that triggered its rollup (parity fix)

    def update(self, bar: Bar, session_id: str) -> TrendSnapshot:
        self.last_bar = bar
        snap = self.indicators.update(bar, session_id)
        self.last_indicators = snap
        self.structure.update(bar)
        components: dict[str, float] = {}

        # EMA stack: 9>21>50 rising = +100; inverted falling = −100; partial = graded
        if snap.ema9 is not None and snap.ema21 is not None and snap.ema50 is not None:
            stack = 0.0
            stack += 50.0 if snap.ema9 > snap.ema21 else -50.0
            stack += 50.0 if snap.ema21 > snap.ema50 else -50.0
            if self._prev_ema9 is not None and snap.ema9 != self._prev_ema9:
                rising = snap.ema9 > self._prev_ema9
                stack = stack * 1.0 if (rising and stack > 0) or (not rising and stack < 0) else stack * 0.5
            components["ema_stack"] = stack
        if snap.ema9 is not None:
            self._prev_ema9 = snap.ema9

        # Price vs 9 EMA (RULE-T5)
        if snap.ema9 is not None:
            components["price_vs_ema9"] = 100.0 if snap.close > snap.ema9 else -100.0

        # Market structure (RULE-T1)
        sstate = self.structure.state
        components["structure"] = {
            StructureState.UPTREND: 100.0, StructureState.DOWNTREND: -100.0,
            StructureState.RANGE: 0.0, StructureState.UNKNOWN: 0.0,
        }[sstate]

        # RSI regime (RULE-T7): ≥60 strong / 50–60 lean / 40–50 lean-bear / ≤40 weak
        if snap.rsi14 is not None:
            r = snap.rsi14
            components["rsi_regime"] = (
                100.0 if r >= 60 else 40.0 if r >= 50 else -40.0 if r >= 40 else -100.0
            )

        # ADX/DMI: trending (ADX≥20) in DI direction; weak ADX = neutral drag
        if snap.adx14 is not None and snap.di_plus is not None and snap.di_minus is not None:
            direction = 1.0 if snap.di_plus > snap.di_minus else -1.0
            strength = min(snap.adx14 / 40.0, 1.0)  # ADX 40+ = full conviction
            components["adx_dmi"] = 100.0 * direction * strength if snap.adx14 >= 20 else 0.0

        # Weighted score over available components; renormalize during warmup
        avail = {k: WEIGHTS[k] for k in components}
        total_w = sum(avail.values())
        score = (
            sum(components[k] * avail[k] for k in components) / total_w if total_w else 0.0
        )
        self.snapshot = TrendSnapshot(
            symbol=self.symbol, timeframe=self.timeframe, score=round(score, 2),
            state=state_from_score(score), components=components,
        )
        return self.snapshot


def alignment_score(matrix: dict[Timeframe, float], module: str, direction: int) -> float:
    """0–100 agreement of the module's TFs with `direction` (+1 long / −1 short).

    Missing TFs are skipped with weight renormalization (warmup honesty).
    """
    weights = MODULE_TF_WEIGHTS[module]
    avail = {tf: w for tf, w in weights.items() if tf in matrix}
    total = sum(avail.values())
    if total == 0:
        return 0.0
    agree = sum(max(matrix[tf] * direction, 0.0) / 100.0 * w for tf, w in avail.items())
    return round(100.0 * agree / total, 2)
