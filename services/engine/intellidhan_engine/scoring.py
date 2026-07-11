"""Factor scoring + confidence (doc 03 §3-4).

Real factors: F1 trend alignment, F2 setup quality (strategy-owned), F3 level
confluence, F4 momentum & volume, F5 volatility fit (target geometry vs ATR +
auction context), F8 statistical POP (random-walk barrier baseline adjusted by
trend). Stubs awaiting data feeds: F6 options flow, F7 macro/catalyst —
named in the factor dict so the UI can label them.
"""

from __future__ import annotations

from intellidhan_analytics.trend import alignment_score
from intellidhan_engine.state import SymbolState
from intellidhan_engine.strategies import RawSignal
from intellidhan_schemas import Timeframe
from intellidhan_schemas.signals import Direction

NEUTRAL_STUB = 60.0
CALIBRATION_VERSION = "uncalibrated-v0"

WEIGHTS = {  # doc 03 §3 defaults
    "F1_trend": 0.22, "F2_setup": 0.18, "F3_levels": 0.14, "F4_momentum": 0.12,
    "F5_volatility": 0.12, "F6_flow": 0.08, "F7_macro": 0.08, "F8_pop": 0.06,
}


def score_factors(state: SymbolState, sig: RawSignal) -> dict[str, float]:
    direction = 1 if sig.direction == Direction.LONG else -1
    matrix = state.mtf_matrix()
    f1 = alignment_score(matrix, sig.module.value, direction)

    f3 = state.levels.confluence_score(sig.entry)

    f4 = 50.0
    snap = state.indicators(sig.trigger_tf)
    if snap is not None:
        if snap.rel_volume is not None:
            f4 += min((snap.rel_volume - 1.0) * 20.0, 30.0)
        if snap.macd_histogram is not None:
            agrees = (snap.macd_histogram > 0) == (direction > 0)
            f4 += 15.0 if agrees else -15.0
        f4 = max(0.0, min(f4, 100.0))

    # F5 — volatility fit (doc 03 §3): are the targets reachable within the
    # session's realistic range, and does the auction context support movement?
    f5 = NEUTRAL_STUB
    daily = state.indicators(Timeframe.D1)
    if daily is not None and daily.atr14 and sig.targets:
        t2 = sig.targets[1] if len(sig.targets) > 1 else sig.targets[0]
        target_atr = abs(t2 - sig.entry) / daily.atr14
        # sweet spot: T2 within 0.3–1.0 daily ATR (reachable, non-trivial)
        if target_atr <= 1.0:
            f5 = 80.0 - max(0.0, (0.3 - target_atr)) * 100.0
        else:
            f5 = max(20.0, 80.0 - (target_atr - 1.0) * 40.0)
        prof = state.profile_state
        if prof is not None:
            agrees = ((direction > 0 and prof.range_ext_up)
                      or (direction < 0 and prof.range_ext_down))
            f5 += 10.0 * prof.trend_day_probability * (1 if agrees else -1)
        f5 = max(0.0, min(f5, 100.0))

    # F8 — statistical POP baseline: random-walk odds of hitting T1 before the
    # stop = risk / (risk + reward_T1), shifted by trend agreement (drift term).
    f8 = NEUTRAL_STUB
    risk = abs(sig.entry - sig.stop)
    if risk > 0 and sig.targets:
        reward1 = abs(sig.targets[0] - sig.entry)
        pop_rw = risk / (risk + reward1)
        drift = (f1 - 50.0) / 100.0 * 0.20  # ±0.10 max from alignment
        f8 = max(0.0, min((pop_rw + drift) * 100.0, 100.0))

    return {
        "F1_trend": round(f1, 1),
        "F2_setup": sig.f2_quality,
        "F3_levels": round(f3, 1),
        "F4_momentum": round(f4, 1),
        "F5_volatility": round(f5, 1),
        "F6_flow": NEUTRAL_STUB,
        "F7_macro": NEUTRAL_STUB,
        "F8_pop": round(f8, 1),
    }


def composite(factors: dict[str, float]) -> float:
    return round(sum(factors[k] * WEIGHTS[k] for k in WEIGHTS), 2)


def calibrated_confidence(composite_score: float) -> float:
    """v0 conservative map until isotonic tables exist: a composite of 100
    claims only 0.90, 80 claims 0.72, 75 claims 0.675 — deliberately shy of
    the raw score so uncalibrated optimism can't inflate claims (G8)."""
    return round(min(composite_score / 100.0 * 0.90, 0.90), 4)
