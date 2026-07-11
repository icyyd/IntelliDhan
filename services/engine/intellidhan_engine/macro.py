"""MacroContext — volatility-regime layer feeding F7 (docs 03 §3, 12 §1).

v1 input: VIX daily closes. Regime per session date uses the trailing-252d
percentile of PRIOR closes only (no lookahead — the context a trader had at
that open). Richer inputs (econ calendar, rate path, headlines) extend this
object in later phases without touching consumers.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_schemas import Bar


class Regime(str, Enum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"


class MacroContext(BaseModel):
    date: str
    vix: float | None = None
    vix_pctl_1y: float | None = None
    regime: Regime = Regime.NEUTRAL


def build_macro_series(vix_daily: list[Bar]) -> dict[str, MacroContext]:
    """date -> context; each date sees only closes strictly before it."""
    clock = MarketClock()
    ordered = sorted(vix_daily, key=lambda b: b.ts_close)
    out: dict[str, MacroContext] = {}
    closes: list[float] = []
    for bar in ordered:
        day = clock.session_id(bar.ts_close)
        if closes:
            window = closes[-252:]
            prior = closes[-1]
            pctl = sum(1 for c in window if c <= prior) / len(window)
            regime = (Regime.RISK_OFF if pctl >= 0.75 else
                      Regime.RISK_ON if pctl <= 0.40 else Regime.NEUTRAL)
            out[day] = MacroContext(date=day, vix=round(prior, 2),
                                    vix_pctl_1y=round(pctl, 3), regime=regime)
        else:
            out[day] = MacroContext(date=day)
        closes.append(bar.close)
    return out


def f7_score(ctx: MacroContext | None, direction: int) -> float:
    """F7 macro factor: regime agreement with trade direction (0–100)."""
    if ctx is None or ctx.vix_pctl_1y is None:
        return 60.0  # neutral stub value when macro data is absent
    if ctx.regime == Regime.RISK_ON:
        base = 75.0 if direction > 0 else 45.0
    elif ctx.regime == Regime.RISK_OFF:
        base = 35.0 if direction > 0 else 70.0
    else:
        base = 60.0
    # deep-fear contrarian tilt (RULE-B2): extreme VIX percentile softens the
    # long penalty — panic is where longer-horizon longs get built
    if ctx.regime == Regime.RISK_OFF and ctx.vix_pctl_1y >= 0.95 and direction > 0:
        base = 50.0
    return base
