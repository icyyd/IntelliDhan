"""Strategy evidence object, expectancy helpers, and MACD pullback shadow."""

from intellidhan_analytics.strategy_context import trend_strategy_suitability
from intellidhan_engine.calibration import (
    CalibrationMap,
    expected_net_r,
)
from intellidhan_engine.strategies import (
    PullbackContinuation,
    PullbackContinuationMacd,
    REGISTRY,
)
from intellidhan_schemas.signals import EvidenceStatus, StrategyEvidence


def test_expected_net_r_math():
    # 76.5% hit, avg win 0.3R, 1R loss, 0.05 cost → thin/negative after cost
    net = expected_net_r(0.765, 0.30, 1.0, 0.05)
    assert net < 0
    # Stronger win size clears the gate
    assert expected_net_r(0.765, 0.50, 1.0, 0.05) > 0


def test_build_evidence_from_pullback_calibration():
    cal = CalibrationMap.load("PULLBACK_CONTINUATION")
    evidence = cal.build_evidence(60.0)
    assert isinstance(evidence, StrategyEvidence)
    assert evidence.evidence_status == EvidenceStatus.HISTORICAL_OOS
    assert evidence.point_estimate == 0.765
    assert evidence.sample_size is not None and evidence.sample_size > 0
    assert evidence.interval_95 is not None
    assert evidence.note is not None


def test_expectancy_gate_fails_when_declared_net_negative():
    cal = CalibrationMap(
        "TOY",
        {"0-100": {"n": 50, "wr": 0.70, "sufficient": True}},
        {
            "evidence_status": "HISTORICAL_OOS",
            "avg_r_pre_cost": -0.10,
            "cost_stress_r": 0.05,
            "live_eligible": True,
        },
    )
    ok, detail = cal.passes_expectancy_gate(research_only=False)
    assert not ok
    assert "expected_net_r" in detail
    # Research path still allowed so shadow can accumulate samples
    ok_research, _ = cal.passes_expectancy_gate(research_only=True)
    assert ok_research


def test_macd_variant_registered_and_shadow_only():
    keys = [s.key for s in REGISTRY]
    assert "PULLBACK_CONTINUATION_MACD" in keys
    macd = next(s for s in REGISTRY if s.key == "PULLBACK_CONTINUATION_MACD")
    assert isinstance(macd, PullbackContinuationMacd)
    assert issubclass(type(macd), PullbackContinuation)


def test_macd_calibration_is_not_live():
    cal = CalibrationMap.load("PULLBACK_CONTINUATION_MACD")
    assert cal.meta.get("live_eligible") is False
    assert cal.evidence_status == "IN_SAMPLE_ONLY"
    assert cal.confidence(90.0) <= 0.74


def test_trend_strategy_suitability_notes():
    up = trend_strategy_suitability("STRONG_UPTREND", "BREAKOUT", forecast_status="UNCONFIRMED")
    assert up["swing"]["stance"] == "FAVORABLE"
    assert "UNCONFIRMED" in up["swing"]["note"]
    down = trend_strategy_suitability("DOWNTREND", "INSIDE_CHANNEL")
    assert down["swing"]["stance"] == "DEFENSIVE"
    assert "never create alerts" in up["guardrail"]
