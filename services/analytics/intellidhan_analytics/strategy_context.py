"""Map daily trend consensus to strategy-module suitability notes.

Complements trend_analysis.py without changing the fixed four-method votes.
Used by on-demand analysis and optional strategy context panels.
"""

from __future__ import annotations


def trend_strategy_suitability(
    consensus_label: str,
    breakout_state: str,
    *,
    forecast_status: str | None = None,
) -> dict:
    """Return transparent suitability notes for 0DTE / Swing context.

    This is *context*, not a signal and never raises confidence scores.
    """
    label = consensus_label.upper()
    bullish = label in {"STRONG_UPTREND", "UPTREND"}
    bearish = label in {"STRONG_DOWNTREND", "DOWNTREND"}
    mixed = not bullish and not bearish

    swing = {
        "stance": "FAVORABLE" if bullish else "DEFENSIVE" if bearish else "NEUTRAL",
        "note": (
            "Daily trend agrees with long pullback/continuation class."
            if bullish
            else "Daily trend is defensive; long-only swing pullbacks should stay silent."
            if bearish
            else "Mixed daily votes — prefer no new swing continuation entries."
        ),
    }
    zdte = {
        "stance": (
            "CONTEXT_ONLY"
            if breakout_state == "BREAKOUT" and bullish
            else "DEFENSIVE"
            if bearish
            else "NEUTRAL"
        ),
        "note": (
            "Daily uptrend + channel breakout is supportive context for long 0DTE momentum; "
            "intraday gates still control entries."
            if breakout_state == "BREAKOUT" and bullish
            else "Daily downtrend raises the bar for long 0DTE; prefer short-side or silence."
            if bearish
            else "Daily regime is not decisive for intraday 0DTE triggers."
        ),
    }
    if forecast_status == "UNCONFIRMED":
        swing["note"] += " Forward forecast remains UNCONFIRMED — do not treat as probability."
        zdte["note"] += " Forward forecast remains UNCONFIRMED."

    return {
        "consensus": consensus_label,
        "breakout_state": breakout_state,
        "swing": swing,
        "zerodte": zdte,
        "guardrail": (
            "Suitability notes are regime context only. They never create alerts, "
            "change calibration, or override live_eligible / research_only flags."
        ),
    }
