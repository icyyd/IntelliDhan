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
    # every suppressed record carries its module so the UI can filter by view
    assert all(s.module in (Module.ZDTE, Module.SWING) for s in runner.suppressed)
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


def test_pop_based_signal_skips_rr_gate():
    from pathlib import Path

    from intellidhan_engine.strategies import RawSignal
    from intellidhan_engine.veto import EngineControls, run_gates
    from intellidhan_ingestor.backfill import read_recording
    from intellidhan_schemas.signals import Direction as Dir

    state = SymbolState("QQQ")
    fixture = Path(__file__).parent.parent / "fixtures/golden-sessions/qqq-complex-5m.jsonl"
    for b in read_recording(fixture):
        if b.symbol == "QQQ":
            state.on_bar_5m(b)
    snap = state.indicators(Timeframe.M5)
    entry = snap.ema21  # zero extension
    lowrr = dict(module=Module.SWING, direction=Dir.LONG, trigger_tf=Timeframe.M5,
                 entry=entry, stop=entry - 1.0,
                 targets=[entry + 0.8, entry + 1.6, entry + 2.8],  # RR to T2 = 1.6 < 2
                 f2_quality=70.0, explain="x", invalidation="y")
    hi_conf = 0.80
    normal = run_gates(state, RawSignal(strategy="X", **lowrr), hi_conf, EngineControls())
    assert not normal.passed and normal.gate == "reward_risk"
    popb = run_gates(state, RawSignal(strategy="X", pop_based=True, **lowrr),
                     hi_conf, EngineControls())
    assert popb.gate != "reward_risk"


def test_pullback_continuation_calibration_loads():
    from intellidhan_engine.calibration import CalibrationMap

    cal = CalibrationMap.load("PULLBACK_CONTINUATION")
    assert cal.buckets, "held-out calibration table must exist"
    assert cal.confidence(60.0) == 0.765  # claimed = validation WR, not train


def test_unvalidated_calibration_cannot_claim_live_probability():
    from intellidhan_engine.calibration import CalibrationMap

    unvalidated = CalibrationMap(
        "TEST",
        {"0-100": {"n": 1000, "wr": 0.99, "sufficient": True}},
        {"evidence_status": "UNVALIDATED"},
    )
    validated = CalibrationMap(
        "TEST",
        {"0-100": {"n": 1000, "wr": 0.80, "sufficient": True}},
        {"evidence_status": "FORWARD_PAPER"},
    )
    assert unvalidated.confidence(100.0) == 0.74
    assert not unvalidated.has_validated_evidence
    assert validated.confidence(100.0) == 0.80
    assert validated.has_validated_evidence


def test_recent_daily_buffer_seeds_bounded_and_grows_on_live_close():
    st = SymbolState("T")
    t0 = datetime(2025, 1, 1, 16, 0, tzinfo=ET)
    daily = []
    d = t0
    while len(daily) < 260:
        if d.weekday() < 5:
            px = 100.0 + len(daily) * 0.1
            daily.append(Bar(symbol="T", timeframe=Timeframe.D1, ts_close=d,
                             open=px, high=px + 1, low=px - 1, close=px + 0.5,
                             volume=1e6, source="fx"))
        d += timedelta(days=1)
    st.seed_daily(daily)
    # bounded at 250, keeping the most recent bars
    assert len(st.recent_daily) == 250
    assert st.recent_daily[-1].ts_close == daily[-1].ts_close
    assert st.recent_daily[0].ts_close == daily[10].ts_close

    # a full live session then the next day's first bar closes one more D1 bar
    day1 = daily[-1].ts_close + timedelta(days=3)  # skip past weekend safely
    while day1.weekday() >= 5:
        day1 += timedelta(days=1)
    start = day1.replace(hour=9, minute=35)
    n_before = len(st.recent_daily)
    for i in range(78):  # 9:35 -> 16:00
        ts = start + timedelta(minutes=5 * i)
        st.on_bar_5m(mk_bar(ts, 200 + i * 0.01, 201 + i * 0.01, 199 + i * 0.01,
                            200.5 + i * 0.01))
    nxt = start + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    st.on_bar_5m(mk_bar(nxt, 210, 211, 209, 210.5))
    # buffer was full: the new D1 close pushes one out, cap holds at 250
    assert len(st.recent_daily) == n_before == 250
    assert st.recent_daily[-1].timeframe == Timeframe.D1
    assert st.recent_daily[-1].ts_close.date() == day1.date()  # the live close
