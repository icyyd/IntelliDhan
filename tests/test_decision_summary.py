"""The beginner read-model must simplify the truth, not create trade authority."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from intellidhan_gateway.decision_summary import (
    DecisionSummary,
    summarize_dossier,
    summarize_signal,
)

NOW = datetime(2026, 9, 24, 15, 2, tzinfo=timezone.utc)


def alert(**overrides):
    result = {
        "alert_id": "test-spy-call", "symbol": "SPY", "module": "0DTE",
        "strategy": "TEST", "vehicle": "OPTION", "action": "BTO",
        "created_at": (NOW - timedelta(minutes=2)).isoformat(),
        "valid_until": (NOW + timedelta(minutes=10)).isoformat(),
        "status": "ACTIVE", "research_only": False,
        "entry_zone": [1.4, 1.5], "entry_limit": 1.45,
        "stop_underlying": 599, "stop_est_vehicle": 1.1,
        "underlying_price": 600,
        "take_profits": [{"underlying": 603}, {"underlying": 604}],
        "legs": [{"side": "BUY", "option_type": "CALL", "research_only": False}],
        "trend_matrix": {"5m": "UP", "15m": "STRONG_UP", "1H": "UP"},
        "evidence": {"evidence_status": "FORWARD_PAPER", "point_estimate": 0.8,
                     "sample_size": 100, "expected_net_r": 0.1},
        "factors": {"F1_trend": 80, "F2_setup": 80},
        "confidence": 0.8,
    }
    result.update(overrides)
    return result


def health(**overrides):
    result = {
        "ok": True,
        "symbols": {"SPY": {
            "actionable": True, "status": "OK",
            "last_good_bar_at": (NOW - timedelta(minutes=2)).isoformat(),
        }},
    }
    result.update(overrides)
    return result


def calibration():
    return {"TEST": {
        "strategy": "TEST",
        "meta": {"live_eligible": True, "evidence_status": "FORWARD_PAPER",
                 "avg_r_pre_cost": 0.15, "cost_stress_r": 0.05},
        "buckets": {"0-100": {"n": 100, "sufficient": True, "wr": 0.8}},
    }}


def signal(item=None, *, data_health=None, evidence=None, now=NOW):
    return summarize_signal(
        item if item is not None else alert(), health=data_health or health(),
        calibration=evidence if evidence is not None else calibration(), now=now,
    )


def dossier():
    return {
        "security": {"symbol": "SPY"},
        "analysis": {
            "symbol": "SPY", "as_of": "2026-09-23T20:00:00Z",
            "consensus": {"label": "UPTREND"},
            "forecast": {
                "strategy_context_status": "VALIDATED_CONTEXT",
                "horizons": {"one_month": {
                    "trading_days": 21, "label": "FAVORABLE", "confidence": "MODERATE",
                    "matched_state_samples": 30,
                    "walk_forward_validation": {"status": "OUTPERFORMS_BASE", "brier_skill_pct": 4},
                }},
            },
            "key_levels": {"last_close": 600, "breakout_confirmation_55d": 610,
                           "invalidation_reference_20d": 580, "sma_200": 550},
        },
        "intelligence": {
            "generated_at": (NOW - timedelta(minutes=5)).isoformat(),
            "fundamentals": {"score": 75, "coverage": 3, "metrics": [
                {"key": "revenue_growth", "value": 10},
                {"key": "gross_margin", "value": 50},
                {"key": "net_margin", "value": 20},
            ]},
            "multi_brain": {"status": "RESEARCH_ONLY", "posture": "BUY", "critical_blockers": []},
        },
    }


def test_validated_setup_can_be_reviewed_but_never_authorizes_execution():
    result = signal()
    assert result["research_view"] == "BUY"
    assert result["next_step"] == "REVIEW_SETUP"
    assert result["status"] == "CURRENT"
    assert result["execution_authorized"] is False
    assert result["freshness"] == "CURRENT"
    assert result["age_seconds"] == 120
    assert not result["blockers"]
    DecisionSummary.model_validate(result)


def test_single_strategy_calibration_record_is_supported_without_guessing_identity():
    assert signal(evidence=calibration()["TEST"])["next_step"] == "REVIEW_SETUP"
    changed = calibration()["TEST"]
    changed["strategy"] = "OTHER"
    assert signal(evidence=changed)["next_step"] == "WAIT"


@pytest.mark.parametrize("changes", [
    {"research_only": True}, {"status": "SHADOW"}, {"status": "CANCELLED"},
    {"status": "CLOSED"}, {"valid_until": NOW.isoformat()}, {"valid_until": None},
    {"created_at": (NOW + timedelta(minutes=1)).isoformat()},
    {"created_at": "2026-09-24T14:00:00"}, {"evidence": None},
    {"trend_matrix": {"5m": "UP"}},
    {"entry_zone": [2.0, 1.0]}, {"stop_underlying": float("nan")},
])
def test_unvalidated_inactive_or_incomplete_setup_is_wait(changes):
    result = signal(alert(**changes))
    assert result["next_step"] == "WAIT"
    assert result["research_view"] == "WAIT"
    assert result["blockers"]
    assert result["execution_authorized"] is False


def test_missing_or_small_sample_calibration_is_wait():
    assert signal(evidence={})["next_step"] == "WAIT"
    limited = calibration()
    limited["TEST"]["buckets"]["0-100"]["n"] = 14
    assert signal(evidence=limited)["next_step"] == "WAIT"
    limited = calibration()
    limited["TEST"]["meta"]["live_eligible"] = False
    assert signal(evidence=limited)["next_step"] == "WAIT"


@pytest.mark.parametrize("changes", [
    {"confidence": 0.74}, {"confidence": 0.9}, {"confidence": float("nan")},
    {"factors": {}}, {"factors": {"F1_trend": 101}},
    {"factors": {"F1_trend": float("nan")}},
])
def test_signal_confidence_must_be_sufficient_and_supported_by_matching_bucket(changes):
    assert signal(alert(**changes))["next_step"] == "WAIT"


@pytest.mark.parametrize("changes", [
    {"expected_net_r": -0.1}, {"expected_net_r": 0}, {"expected_net_r": None},
    {"sample_size": 5}, {"point_estimate": 0.9}, {"point_estimate": None},
])
def test_evidence_requires_positive_expectancy_and_consistent_sample(changes):
    source = alert()
    source["evidence"].update(changes)
    assert signal(source)["next_step"] == "WAIT"


def test_unrelated_sufficient_bucket_cannot_qualify_a_thin_alert_bucket():
    table = calibration()
    table["TEST"]["buckets"] = {
        "0-75": {"n": 100, "sufficient": True, "wr": 0.8},
        "75-100": {"n": 5, "sufficient": False, "wr": 0.8},
    }
    assert signal(evidence=table)["next_step"] == "WAIT"
    table["TEST"]["buckets"].pop("75-100")
    assert signal(evidence=table)["next_step"] == "WAIT"


def test_ambiguous_overlapping_buckets_do_not_qualify():
    table = calibration()
    table["TEST"]["buckets"]["75-100"] = {"n": 100, "sufficient": True, "wr": 0.8}
    assert signal(evidence=table)["next_step"] == "WAIT"


def test_positive_alert_expectancy_cannot_override_missing_or_negative_calibration():
    table = calibration()
    table["TEST"]["meta"]["avg_r_pre_cost"] = -0.1
    assert signal(evidence=table)["next_step"] == "WAIT"
    table["TEST"]["meta"].pop("avg_r_pre_cost")
    assert signal(evidence=table)["next_step"] == "WAIT"


def test_bought_put_is_bearish_not_an_instruction_to_sell_shares():
    result = signal(alert(
        legs=[{"side": "BUY", "option_type": "PUT"}],
        trend_matrix={"5m": "DOWN", "15m": "DOWN", "1H": "STRONG_DOWN"},
        stop_underlying=601, take_profits=[{"underlying": 598}],
    ))
    assert result["trend"] == result["thesis_direction"] == "DOWN"
    assert result["research_view"] == "SELL"
    assert result["headline"] == "Review a falling-price setup"
    assert any("not an instruction to sell shares" in text for text in result["reasons"])


def test_mixed_trends_are_not_simplified_into_a_buy():
    result = signal(alert(trend_matrix={"5m": "UP", "15m": "DOWN", "1H": "UP"}))
    assert result["trend"] == "SIDEWAYS"
    assert result["research_view"] == "WAIT"


@pytest.mark.parametrize("kind", ["stale", "quarantined", "missing", "future", "calendar"])
def test_bad_market_data_fails_closed(kind):
    data = health()
    now = NOW
    if kind == "stale":
        data["symbols"]["SPY"]["last_good_bar_at"] = (NOW - timedelta(hours=1)).isoformat()
    elif kind == "quarantined":
        data["symbols"]["SPY"].update({"actionable": False, "status": "QUARANTINED"})
    elif kind == "missing":
        data["symbols"]["SPY"].pop("last_good_bar_at")
    elif kind == "future":
        data["symbols"]["SPY"]["last_good_bar_at"] = (NOW + timedelta(minutes=2)).isoformat()
    else:
        now = NOW.replace(year=2028)
    result = signal(data_health=data, now=now)
    assert result["next_step"] == "WAIT"
    assert result["execution_authorized"] is False


def test_contradictory_actionable_flag_cannot_override_quarantine_status():
    data = health()
    data["symbols"]["SPY"]["status"] = "QUARANTINED"
    result = signal(data_health=data)
    assert result["status"] == "UNAVAILABLE"
    assert result["next_step"] == "WAIT"


def test_option_premium_and_stock_levels_have_different_units():
    levels = {row["key"]: row for row in signal()["levels"]}
    assert levels["entry"]["unit"] == "OPTION_USD_PER_SHARE"
    assert levels["entry"]["low"] == 1.4
    assert levels["stop"]["unit"] == "UNDERLYING_USD_PER_SHARE"
    assert levels["stop"]["value"] == 599
    assert levels["option_stop"]["unit"] == "OPTION_USD_PER_SHARE"
    assert levels["target_1"]["unit"] == "UNDERLYING_USD_PER_SHARE"


def test_0dte_equity_fallback_is_not_an_option_signal():
    result = signal(alert(vehicle="EQUITY", action="EQUITY_BUY", legs=[]))
    assert result["next_step"] == "WAIT"
    assert any("contract has not been verified" in text for text in result["blockers"])
    assert result["levels"][0]["unit"] == "UNDERLYING_USD_PER_SHARE"


@pytest.mark.parametrize("module", ["SWING", "LEAPS"])
def test_requested_swing_and_leaps_coverage_is_not_fabricated(module):
    result = signal(alert(module=module))
    assert result["next_step"] == "WAIT"
    assert result["horizon_coverage"] != "VALIDATED_SETUP"
    if module == "SWING":
        assert "2–5-day" in result["horizon_note"]
    else:
        assert result["horizon_coverage"] == "UNAVAILABLE"


def test_complete_dossier_is_research_only_even_when_buy_view_is_supported():
    result = summarize_dossier(dossier(), now=NOW)
    assert result["research_view"] == "BUY"
    assert result["next_step"] == "TRACK_ONLY"
    assert result["execution_authorized"] is False
    assert result["status"] == "RESEARCH_ONLY"
    assert result["horizon"] == "DAILY_CONTEXT"
    assert [item["horizon"] for item in result["horizons"]] == ["0DTE", "SWING", "LEAPS"]
    assert all(item["coverage"] != "VALIDATED_SETUP" for item in result["horizons"])
    assert all(level["kind"] == "REFERENCE" for level in result["levels"])


@pytest.mark.parametrize("change", [
    {"confidence": "LOW"}, {"matched_state_samples": 11}, {"label": "UNCONFIRMED"},
    {"walk_forward_validation": {"status": "OUTPERFORMS_BASE", "brier_skill_pct": 0}},
    {"walk_forward_validation": {"status": "NO_BENCHMARK_EDGE", "brier_skill_pct": 5}},
    {"trading_days": 5},
])
def test_top_level_context_flag_cannot_replace_actual_forecast_validation(change):
    data = dossier()
    data["analysis"]["forecast"]["horizons"]["one_month"].update(change)
    result = summarize_dossier(data, now=NOW)
    assert result["research_view"] == "WAIT"
    assert any("validation checks" in text for text in result["blockers"])


def test_missing_fundamentals_or_social_enthusiasm_cannot_create_buy():
    data = dossier()
    data["intelligence"]["fundamentals"] = {}
    data["intelligence"]["social"] = {"score": 100}
    result = summarize_dossier(data, now=NOW)
    assert result["research_view"] == "WAIT"


def test_hold_does_not_assume_the_user_owns_the_stock():
    data = dossier()
    data["intelligence"]["multi_brain"]["posture"] = "HOLD"
    result = summarize_dossier(data, now=NOW)
    assert result["headline"] == "Hold · no clear new opportunity"
    assert any("not assessed your existing holdings" in text for text in result["reasons"])


def test_daily_freshness_handles_weekends_and_stale_sessions():
    data = dossier()
    saturday = datetime(2026, 9, 26, 15, tzinfo=timezone.utc)
    data["analysis"]["as_of"] = "2026-09-25T04:00:00Z"  # Yahoo's session date.
    data["intelligence"]["generated_at"] = saturday.isoformat()
    assert summarize_dossier(data, now=saturday)["freshness"] == "CURRENT"
    data["analysis"]["as_of"] = "2026-09-24T20:00:00Z"
    result = summarize_dossier(data, now=saturday)
    assert result["freshness"] == result["status"] == "STALE"
    assert result["research_view"] == "WAIT"


@pytest.mark.parametrize("stamp", [None, "2026-09-23", "2026-09-24T16:00:00Z"])
def test_unknown_or_future_daily_timestamp_is_wait(stamp):
    data = dossier()
    data["analysis"]["as_of"] = stamp
    result = summarize_dossier(data, now=NOW)
    assert result["freshness"] == "UNKNOWN"
    assert result["research_view"] == "WAIT"


def test_packet_assembly_time_is_not_claimed_as_source_freshness():
    data = dossier()
    data["intelligence"]["generated_at"] = (NOW - timedelta(hours=2)).isoformat()
    data["intelligence"]["sources"] = [
        {"name": "SEC EDGAR", "status": "AVAILABLE", "as_of": (NOW - timedelta(hours=6)).isoformat()},
        {"name": "Alpha Vantage", "status": "NOT_CONFIGURED", "as_of": None},
    ]
    result = summarize_dossier(data, now=NOW)
    assert result["next_step"] == "TRACK_ONLY"
    assert result["research_generated_at"] == (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    sources = {source["name"]: source for source in result["source_observations"]}
    assert sources["SEC EDGAR"]["age_seconds"] == 6 * 3600
    assert sources["SEC EDGAR"]["freshness"] == "NOT_ASSESSED"
    assert sources["SEC EDGAR"]["timestamp_kind"] == "PROVIDER_OBSERVATION"
    assert "not the filing date" in sources["SEC EDGAR"]["note"]
    assert sources["Alpha Vantage"]["freshness"] == "UNKNOWN"
    assert "does not confirm" in sources["Alpha Vantage"]["note"]
    assert "verified in the past hour" not in str(result)


def test_newly_assembled_packet_does_not_refresh_stale_news_content():
    data = dossier()
    data["intelligence"]["generated_at"] = NOW.isoformat()
    data["intelligence"]["sources"] = [{
        "name": "Alpha Vantage", "status": "AVAILABLE",
        "as_of": (NOW - timedelta(days=5)).isoformat(),
    }]
    sources = summarize_dossier(data, now=NOW)["source_observations"]
    news = next(row for row in sources if row["name"] == "Alpha Vantage")
    assert news["age_seconds"] == 5 * 24 * 3600
    assert news["freshness"] == "NOT_ASSESSED"
    assert news["timestamp_kind"] == "CONTENT"


def test_summaries_do_not_mutate_inputs_or_leak_unrelated_fields():
    data = dossier()
    data["intelligence"]["private_token"] = "do-not-echo"
    before = deepcopy(data)
    result = summarize_dossier(data, now=NOW)
    assert data == before
    assert "do-not-echo" not in str(result)
    source = alert()
    before = deepcopy(source)
    signal(source)
    assert source == before


def test_execution_authority_is_structurally_impossible():
    result = signal()
    result["execution_authorized"] = True
    with pytest.raises(ValidationError):
        DecisionSummary.model_validate(result)
    with pytest.raises(ValueError, match="timezone-aware"):
        signal(now=NOW.replace(tzinfo=None))
