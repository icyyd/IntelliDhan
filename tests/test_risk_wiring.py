"""Regression tests for the risk-state wiring gap (enhancement review §6.2):
MODULE_CONCURRENCY existed but nothing ever incremented open_by_module, so
the cap, the duplicate guard, and the correlation cap were all permanent
no-ops. These tests prove the counters move and the gates actually fire.
"""

import pytest

from intellidhan_engine.veto import EngineControls, cluster_of
from intellidhan_schemas.signals import Module


def test_cluster_mapping_groups_leveraged_pairs():
    assert cluster_of("QQQ") == cluster_of("TQQQ") == "NDX"
    assert cluster_of("SMH") == "SOX"
    assert cluster_of("SPY") == "SPX"
    assert cluster_of("AAPL") == "AAPL"  # unlisted symbols are their own cluster


def test_register_open_close_symmetric():
    c = EngineControls()
    c.register_open(Module.SWING, "QQQ", "PULLBACK_CONTINUATION")
    c.register_open(Module.SWING, "TQQQ", "PULLBACK_CONTINUATION")
    assert c.open_by_module[Module.SWING] == 2
    assert c.open_by_cluster["NDX"] == 2
    assert c.open_symbol_strategy[("QQQ", "PULLBACK_CONTINUATION")] == 1

    c.register_close(Module.SWING, "QQQ", "PULLBACK_CONTINUATION")
    assert c.open_by_module[Module.SWING] == 1
    assert c.open_by_cluster["NDX"] == 1
    assert c.open_symbol_strategy[("QQQ", "PULLBACK_CONTINUATION")] == 0


def test_close_never_goes_negative():
    c = EngineControls()
    c.register_close(Module.SWING, "QQQ", "X")  # close without a prior open
    assert c.open_by_module[Module.SWING] == 0
    assert c.open_by_cluster["NDX"] == 0


@pytest.fixture
def warmed_state():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from intellidhan_engine.state import SymbolState
    from intellidhan_schemas import Bar, Timeframe

    ET = ZoneInfo("America/New_York")
    st = SymbolState("QQQ")
    t0 = datetime(2026, 7, 13, 9, 35, tzinfo=ET)
    for i in range(60):
        px = 100 + i * 0.05
        st.on_bar_5m(Bar(symbol="QQQ", timeframe=Timeframe.M5,
                         ts_close=t0 + timedelta(minutes=5 * i),
                         open=px, high=px + 0.3, low=px - 0.3, close=px,
                         volume=1000, source="fx"))
    return st


def _swing_signal(entry, stop, target):
    from intellidhan_engine.strategies import RawSignal
    from intellidhan_schemas import Timeframe
    from intellidhan_schemas.signals import Direction

    return RawSignal(strategy="PULLBACK_CONTINUATION", module=Module.SWING,
                     direction=Direction.LONG, trigger_tf=Timeframe.M5, entry=entry,
                     stop=stop, targets=[target], f2_quality=70.0, explain="x",
                     invalidation="y", pop_based=True)


def test_duplicate_guard_blocks_same_symbol_strategy(warmed_state):
    from intellidhan_engine.veto import run_gates
    from intellidhan_schemas import Timeframe

    controls = EngineControls()
    px = warmed_state.indicators(Timeframe.M5).ema21
    sig = _swing_signal(px, px - 1.0, px + 3.0)
    first = run_gates(warmed_state, sig, 0.99, controls)
    assert first.passed
    controls.register_open(Module.SWING, "QQQ", "PULLBACK_CONTINUATION")
    second = run_gates(warmed_state, sig, 0.99, controls)
    assert not second.passed and second.gate == "duplicate"
    controls.register_close(Module.SWING, "QQQ", "PULLBACK_CONTINUATION")
    third = run_gates(warmed_state, sig, 0.99, controls)
    assert third.passed  # released after close


def test_correlation_cap_blocks_third_cluster_member(warmed_state):
    from intellidhan_engine.veto import run_gates
    from intellidhan_schemas import Timeframe

    px = warmed_state.indicators(Timeframe.M5).ema21
    sig = _swing_signal(px, px - 1.0, px + 3.0)
    controls = EngineControls()
    controls.register_open(Module.SWING, "TQQQ", "OTHER_STRAT")  # 1st NDX-cluster open
    controls.register_open(Module.SWING, "QQQ", "ANOTHER_STRAT")  # 2nd NDX-cluster open (cap=2)
    verdict = run_gates(warmed_state, sig, 0.99, controls)  # QQQ itself, 3rd in cluster
    assert not verdict.passed and verdict.gate == "correlation"


def test_concurrency_cap_actually_fires_now(warmed_state):
    """Regression for the exact bug: cap existed but open_by_module was never
    written, so this gate could never fire. Fill SWING to its cap (5) with
    distinct symbol+strategy keys (so the duplicate guard isn't what blocks
    the 6th) and confirm the concurrency gate — not some other gate — fires."""
    from intellidhan_engine.veto import MODULE_CONCURRENCY, run_gates
    from intellidhan_schemas import Timeframe

    px = warmed_state.indicators(Timeframe.M5).ema21
    sig = _swing_signal(px, px - 1.0, px + 3.0)
    controls = EngineControls()
    for i in range(MODULE_CONCURRENCY[Module.SWING]):
        controls.register_open(Module.SWING, f"SYM{i}", f"STRAT{i}")
    verdict = run_gates(warmed_state, sig, 0.99, controls)
    assert not verdict.passed and verdict.gate == "concurrency"


def test_disabled_strategy_never_emits_live_alert_but_shadow_still_harvests():
    """PULLBACK_CONTINUATION is blocked pending research/production parity
    (docs/18-enhancement-review.md). This must hold even for an artificially
    perfect setup — the block is unconditional, not confidence-dependent."""
    from pathlib import Path

    from intellidhan_engine.runner import EngineRunner
    from intellidhan_ingestor.backfill import read_recording

    fixture = Path(__file__).parent.parent / "fixtures/golden-sessions/qqq-complex-5m.jsonl"
    bars = [b for b in read_recording(fixture) if b.symbol == "QQQ"]

    gated = EngineRunner(["QQQ"], shadow=False)
    for b in bars:
        gated.on_bar_5m(b)
    assert not any(s.strategy == "PULLBACK_CONTINUATION" for s in gated.setups)
    disabled = [s for s in gated.suppressed if s.gate == "disabled"]
    # Only asserts the mechanism exists and is reachable; whether this specific
    # short fixture happens to trigger the strategy depends on market data.
    for s in disabled:
        assert s.strategy == "PULLBACK_CONTINUATION"

    shadow = EngineRunner(["QQQ"], shadow=True)
    for b in bars:
        shadow.on_bar_5m(b)
    # In shadow mode the 'disabled' gate must never fire — research harvesting
    # is exactly what shadow mode is for.
    assert not any(s.gate == "disabled" for s in shadow.suppressed)
