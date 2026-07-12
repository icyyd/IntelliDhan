"""Engine runner — drives bars through state → strategies → scoring → veto wall.

Used identically by live ingestion, replay, and backtests (doc 01 §7).
"""

from __future__ import annotations

from intellidhan_engine.calibration import CalibrationMap
from intellidhan_engine.macro import MacroContext
from intellidhan_engine.scoring import composite, score_factors
from intellidhan_engine.state import SymbolState
from intellidhan_engine.strategies import REGISTRY, RawSignal
from intellidhan_engine.veto import EngineControls, Verdict, reward_risk, run_gates
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Setup, SuppressedSetup


class EngineRunner:
    def __init__(self, symbols: list[str], strategies=None, shadow: bool = False) -> None:
        self.states = {s: SymbolState(s) for s in symbols}
        self.strategies = strategies if strategies is not None else REGISTRY
        self.controls = EngineControls()
        self.shadow = shadow          # SHADOW mode (doc 08 §4): bypass ONLY the
                                      # confidence gate to harvest calibration samples
        self.calibration = {st.key: CalibrationMap.load(st.key) for st in self.strategies}
        self.macro_by_day: dict[str, MacroContext] = {}
        self.setups: list[Setup] = []
        self.suppressed: list[SuppressedSetup] = []
        self._seq = 0

    def seed_daily(self, symbol: str, daily_bars: list[Bar]) -> None:
        self.states[symbol].seed_daily(daily_bars)

    def set_macro_series(self, macro_by_day: dict[str, MacroContext]) -> None:
        self.macro_by_day = macro_by_day

    def on_bar_5m(self, bar: Bar) -> list[Setup]:
        state = self.states.get(bar.symbol)
        if state is None:
            return []
        prev_daily = state.trend[Timeframe.D1].last_indicators
        prev_h1 = state.trend[Timeframe.H1].last_indicators
        state.on_bar_5m(bar)
        emitted: list[Setup] = []
        daily_closed = state.trend[Timeframe.D1].last_indicators is not prev_daily
        h1_closed = state.trend[Timeframe.H1].last_indicators is not prev_h1
        for strat in self.strategies:
            if strat.trigger_tf == Timeframe.D1 and not daily_closed:
                continue
            if strat.trigger_tf == Timeframe.H1 and not h1_closed:
                continue
            sig = strat.evaluate(state)
            if sig is not None:
                result = self._score_and_gate(state, sig)
                if result is not None:
                    emitted.append(result)
        self.setups.extend(emitted)
        return emitted

    def _score_and_gate(self, state: SymbolState, sig: RawSignal) -> Setup | None:
        self._seq += 1
        setup_id = (f"stp_{state.ts().strftime('%Y%m%d_%H%M%S')}_"
                    f"{sig.strategy.lower()}_{state.symbol.lower()}_{self._seq}")
        macro = self.macro_by_day.get(state.session_id or "")
        factors = score_factors(state, sig, macro)
        comp = composite(factors)
        cal = self.calibration[sig.strategy]
        conf = cal.confidence(comp)
        gate_conf = 1.0 if self.shadow else conf
        verdict: Verdict = run_gates(state, sig, gate_conf, self.controls)
        if not verdict.passed:
            self.suppressed.append(
                SuppressedSetup(
                    setup_id=setup_id, strategy=sig.strategy, symbol=state.symbol,
                    ts=state.ts(), gate=verdict.gate, detail=verdict.detail,
                    composite=comp, confidence=conf,
                )
            )
            return None
        matrix = {tf.value: score for tf, score in state.mtf_matrix().items()}
        return Setup(
            setup_id=setup_id, module=sig.module, strategy=sig.strategy,
            symbol=state.symbol, direction=sig.direction, trigger_tf=sig.trigger_tf,
            ts=state.ts(), mtf_matrix=matrix, factors=factors, composite=comp,
            confidence=conf, entry_underlying=round(sig.entry, 4),
            stop_underlying=round(sig.stop, 4),
            targets_underlying=[round(t, 4) for t in sig.targets],
            reward_risk=reward_risk(sig),
            explain=sig.explain + (
                " [SHADOW mode — calibration harvesting]" if self.shadow
                else (f" [calibrated: {len(cal.buckets)} buckets]" if cal.buckets
                      else " [uncalibrated-v0 — conservative map]")),
            invalidation=sig.invalidation,
        )
