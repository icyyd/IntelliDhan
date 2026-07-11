"""Veto & Suppressor Wall — the single choke point (doc 01 §2③, doc 03 §5).

Gates run in order; the first failure suppresses with a named gate so the
'why we're quiet' feed can render it. Every rule cites its spec ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from intellidhan_engine.state import SymbolState
from intellidhan_engine.strategies import RawSignal
from intellidhan_schemas import Timeframe
from intellidhan_schemas.signals import Module

CONFIDENCE_THRESHOLD = 0.75
COUNTER_TREND_THRESHOLD = 0.85
MIN_RR = 2.0

MODULE_CONCURRENCY = {Module.ZDTE: 2, Module.SWING: 5, Module.LEAPS: 6, Module.HODL: 10}

ZDTE_NO_ENTRY_BEFORE = time(9, 35)   # RULE-T12
ZDTE_NO_ENTRY_AFTER = time(15, 50)


@dataclass
class EngineControls:
    """Mutable discipline state the behavior plane will publish (doc 17 §5)."""

    open_by_module: dict[Module, int] = field(default_factory=dict)
    cooldown_until: dict[Module, object] = field(default_factory=dict)  # ts by module
    consecutive_stops_today: dict[Module, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    gate: str = ""
    detail: str = ""


def run_gates(
    state: SymbolState, sig: RawSignal, confidence: float, controls: EngineControls
) -> Verdict:
    snap = state.indicators(sig.trigger_tf)

    # G7 data/warmup: never score on half-warmed indicators
    if snap is None or snap.atr14 is None or snap.ema21 is None:
        return Verdict(False, "warmup", "indicators not fully warmed")

    # RULE-T12 session lockouts (0DTE)
    if sig.module == Module.ZDTE:
        t = state.et_time()
        if t is None or t < ZDTE_NO_ENTRY_BEFORE:
            return Verdict(False, "lockout", "no 0DTE entries before 9:35 ET")
        if t > ZDTE_NO_ENTRY_AFTER:
            return Verdict(False, "lockout", "no new 0DTE entries after 15:50 ET")

    # RULE-M2 extension: momentum entries not > 2 ATR from the 21EMA
    extension = abs(sig.entry - snap.ema21) / snap.atr14
    if extension > 2.0:
        return Verdict(False, "extension", f"entry {extension:.1f} ATR from 21EMA (max 2.0)")

    # R:R ≥ 2:1 to first meaningful target (directional, doc 03 §3)
    risk = abs(sig.entry - sig.stop)
    if risk <= 0:
        return Verdict(False, "risk_geometry", "entry equals stop")
    reward = abs(sig.targets[1] - sig.entry) if len(sig.targets) > 1 else abs(
        sig.targets[0] - sig.entry)
    rr = reward / risk
    if rr < MIN_RR:
        return Verdict(False, "reward_risk", f"R:R {rr:.2f} < {MIN_RR}")

    # Cooldown (discipline layer, doc 00 §4)
    until = controls.cooldown_until.get(sig.module)
    if until is not None and state.ts() < until:
        return Verdict(False, "cooldown", f"module cooling down until {until}")

    # Concurrency cap (RULE-A1)
    open_count = controls.open_by_module.get(sig.module, 0)
    if open_count >= MODULE_CONCURRENCY[sig.module]:
        return Verdict(False, "concurrency", f"{open_count} open ≥ cap")

    # Confidence gate — counter-trend needs 85 (RULE-T1)
    threshold = COUNTER_TREND_THRESHOLD if sig.counter_trend else CONFIDENCE_THRESHOLD
    if confidence < threshold:
        return Verdict(False, "confidence", f"{confidence:.2f} < {threshold}")

    return Verdict(True)


def reward_risk(sig: RawSignal) -> float:
    risk = abs(sig.entry - sig.stop)
    if risk <= 0:
        return 0.0
    reward = abs(sig.targets[1] - sig.entry) if len(sig.targets) > 1 else abs(
        sig.targets[0] - sig.entry)
    return round(reward / risk, 2)


_ = Timeframe  # spec cross-ref convenience
