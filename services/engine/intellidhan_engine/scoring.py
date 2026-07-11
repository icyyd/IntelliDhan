"""Factor scoring + confidence (doc 03 §3-4).

F1 trend alignment, F2 setup quality (strategy-owned), F3 level confluence,
F4 momentum & volume are computed from real state. F5-F8 (volatility fit,
options flow, macro, statistical POP) are NEUTRAL_STUB=60 until their data
feeds land — every stub is named in the factor dict so the UI can label it,
and the calibration version is stamped 'uncalibrated-v0': alerts run in
SHADOW grading until real calibration tables exist (doc 08 §4 governance).
"""

from __future__ import annotations

from intellidhan_analytics.trend import alignment_score
from intellidhan_engine.state import SymbolState
from intellidhan_engine.strategies import RawSignal
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

    return {
        "F1_trend": round(f1, 1),
        "F2_setup": sig.f2_quality,
        "F3_levels": round(f3, 1),
        "F4_momentum": round(f4, 1),
        "F5_volatility": NEUTRAL_STUB,
        "F6_flow": NEUTRAL_STUB,
        "F7_macro": NEUTRAL_STUB,
        "F8_pop": NEUTRAL_STUB,
    }


def composite(factors: dict[str, float]) -> float:
    return round(sum(factors[k] * WEIGHTS[k] for k in WEIGHTS), 2)


def calibrated_confidence(composite_score: float) -> float:
    """v0 conservative map until isotonic tables exist: a composite of 100
    claims only 0.90, 80 claims 0.72, 75 claims 0.675 — deliberately shy of
    the raw score so uncalibrated optimism can't inflate claims (G8)."""
    return round(min(composite_score / 100.0 * 0.90, 0.90), 4)
