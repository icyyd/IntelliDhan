"""Signal-plane tests: state, strategies, scoring, veto wall, full-fixture run."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from intellidhan_engine.runner import EngineRunner
from intellidhan_engine.scoring import calibrated_confidence, composite
from intellidhan_engine.state import SymbolState
from intellidhan_engine.veto import MODULE_CONCURRENCY
from intellidhan_ingestor.backfill import read_recording
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Module

ET = ZoneInfo("America/New_York")
FIXTURE = Path(__file__).parent.parent / "fixtures/golden-sessions/qqq-complex-5m.jsonl"


def mk_bar(ts, o, h, lo, c, v=100_000.0, sym="T") -> Bar:
    return Bar(symbol=sym, timeframe=Timeframe.M5, ts_close=ts,
               open=o, high=h, low=lo, close=c, volume=v, source="fx")


def test_opening_range_tracks_first_30_minutes():
    st = SymbolState("T")
    t0 = datetime(2026, 7, 10, 9, 35, tzinfo=ET)
    prices = [(100, 102, 99), (101, 104, 100), (103, 105, 102),
              (104, 104.5, 103), (104, 106, 103.5), (105, 105.5, 104)]
    for i, (o, h, lo) in enumerate(prices):
        st.on_bar_5m(mk_bar(t0 + timedelta(minutes=5 * i), o, h, lo, (h + lo) / 2))
    assert not st.opening_range.complete  # 10:05 bar not seen yet
    st.on_bar_5m(mk_bar(t0 + timedelta(minutes=30), 105, 107, 104, 106))
    assert st.opening_range.complete
    assert st.opening_range.high == 106.0  # max through 10:00 bar
    assert st.opening_range.low == 99.0


def test_composite_and_confidence_math():
    factors = {"F1_trend": 100.0, "F2_setup": 100.0, "F3_levels": 100.0,
               "F4_momentum": 100.0, "F5_volatility": 100.0, "F6_flow": 100.0,
               "F7_macro": 100.0, "F8_pop": 100.0}
    assert composite(factors) == pytest.approx(100.0)
    assert calibrated_confidence(100.0) == pytest.approx(0.90)  # never claims certainty
    assert calibrated_confidence(80.0) == pytest.approx(0.72)   # conservative v0 map
    assert calibrated_confidence(0.0) == 0.0


def test_full_fixture_run_emits_and_suppresses():
    bars = read_recording(FIXTURE)
    runner = EngineRunner(["QQQ", "SPY", "SMH", "TQQQ"])
    for bar in bars:
        for setup in runner.on_bar_5m(bar):
            # every emitted setup is complete and inside its own gates
            assert setup.confidence >= 0.75 or setup.reward_risk >= 2.0
            assert setup.stop_underlying != setup.entry_underlying
            assert len(setup.targets_underlying) == 3
            assert setup.explain and setup.invalidation
            assert "SHADOW" in setup.explain  # uncalibrated honesty stamp
    # the wall must be doing real work: evaluations happened, most were suppressed
    assert len(runner.suppressed) > 0
    gates = {s.gate for s in runner.suppressed}
    assert gates <= {"warmup", "lockout", "extension", "reward_risk",
                     "cooldown", "concurrency", "confidence", "risk_geometry",
                     "one_timeframing", "profile_shape"}
    # sub-threshold setups carry their scores for calibration learning (doc 03 §1)
    conf_suppressed = [s for s in runner.suppressed if s.gate == "confidence"]
    assert all(s.composite is not None for s in conf_suppressed)


def test_concurrency_caps_match_rule_a1():
    assert MODULE_CONCURRENCY[Module.ZDTE] == 2
    assert MODULE_CONCURRENCY[Module.SWING] == 5
    assert MODULE_CONCURRENCY[Module.LEAPS] == 6
    assert MODULE_CONCURRENCY[Module.HODL] == 10
