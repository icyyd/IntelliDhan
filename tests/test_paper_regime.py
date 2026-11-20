from datetime import datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trading.regime import regime_context


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 20, 9, 45, tzinfo=ET)
PREVIOUS = ("2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19")


def opening(day, volume=100):
    return [{"symbol": "SPY", "started_at": f"{day}T09:{minute}:00-05:00", "volume": volume}
            for minute in (30, 35, 40)]


def vix(**changes):
    return dict({"close": 20, "session_date": "2026-11-19", "source": "TEST_VIX",
                 "available_at": "2026-11-19T17:00:00-05:00"}, **changes)


def chain(**changes):
    return dict({
        "symbol": "SPY", "spot": 100, "expiration_date": "2026-11-20",
        "scope": "ALL_STANDARD_0DTE", "complete": True, "expected_contract_count": 2,
        "source": "TEST_CHAIN", "observed_at": "2026-11-20T09:44:00-05:00",
        "oi_session_date": "2026-11-19", "oi_available_at": "2026-11-20T08:00:00-05:00",
        "contracts": [{"contract_id": identity, "option_type": side, "open_interest": 50,
                       "gamma": .05, "multiplier": 100} for identity, side in (("one", "put"), ("two", "call"))],
    }, **changes)


def inputs():
    return [row for day in PREVIOUS + ("2026-11-20",) for row in opening(day)]


def run(bars=None, *, now=NOW, **kwargs):
    return regime_context("SPY", inputs() if bars is None else bars, now,
                          volume_source="TEST_SHARES", **kwargs)


def test_frozen_thresholds_median_and_no_direction_or_live_authority():
    bars = inputs()
    bars[0]["volume"] = 1000000  # outlier: mean would distort this baseline
    out = run(bars, vix=vix())
    assert out["rvol"]["value"] == 1
    assert out["rvol"]["prior_sessions"] == 5
    assert out["rvol"]["status"] == out["vix"]["status"] == out["combined"] == "PASS"
    assert out["research_only"] and not out["live_eligible"]
    assert out["gex"]["status"] == "UNKNOWN"
    bars[-1]["volume"] -= 1
    assert run(bars, vix=vix())["combined"] == "FAIL"


def test_censored_bars_exclude_future_belated_and_uncertain_observations():
    bars = inputs()
    assert run(bars, now=NOW - timedelta(microseconds=1))["rvol"]["value"] is None
    assert run(bars + opening("2026-11-23", 99999))["rvol"]["value"] == 1
    assert run(bars + [bars[-1]])["rvol"]["status"] == "UNKNOWN"
    assert run(bars + [bars[0]])["rvol"]["prior_sessions"] == 4
    bars[-1]["available_at"] = (NOW + timedelta(seconds=1)).isoformat()
    assert run(bars)["rvol"]["value"] is None


def test_old_missing_sessions_cannot_supply_a_distant_reference():
    bars = opening("2026-10-01") + inputs()[3:]
    out = run(bars)
    assert out["rvol"]["prior_sessions"] == 4
    assert out["rvol"]["value"] is None
    wrong_symbol = inputs()
    wrong_symbol[-1]["symbol"] = "QQQ"
    assert run(wrong_symbol)["rvol"]["status"] == "UNKNOWN"


@pytest.mark.parametrize(("value", "status"), [(11.99, "FAIL"), (12, "PASS"), (29.99, "PASS"), (30, "FAIL")])
def test_prior_close_boundaries(value, status):
    assert run(vix=vix(close=value))["vix"]["status"] == status


@pytest.mark.parametrize("changes", [
    {"available_at": "2026-11-20T10:00:00-05:00"}, {"available_at": "2026-11-19T12:00:00-05:00"},
    {"session_date": "2026-11-20"}, {"session_date": "2026-11-18"}, {"close": float("nan")},
    {"symbol": "VIX1D"}, {"source": ""}, {"available_at": "2026-11-19T18:00:00"},
])
def test_wrong_or_mistimed_vix_is_unavailable(changes):
    out = run(vix=vix(**changes))
    assert out["vix"]["value"] is None and out["combined"] == "UNKNOWN"


def test_previous_actual_session_skips_thanksgiving():
    now = "2023-11-24T09:45:00-05:00"
    out = run([], now=now, vix=vix(session_date="2023-11-22", available_at="2023-11-22T18:00:00-05:00"))
    assert out["vix"]["status"] == "PASS"
    assert out["vix"]["required_session"] == "2023-11-22"


def test_gamma_gross_units_one_count_per_contract_not_call_minus_put():
    out = run(vix=vix(), gamma=chain())
    assert out["gex"]["value"] == 50_000
    assert out["gex"]["dealer_sign"] == "UNKNOWN" and not out["gex"]["influences_decision"]
    assert out["combined"] == run(vix=vix())["combined"]


@pytest.mark.parametrize("changes", [
    {"complete": False}, {"expected_contract_count": 3}, {"oi_session_date": "2026-11-20"},
    {"oi_available_at": "2026-11-20T09:45:00-05:00"}, {"observed_at": "2026-11-20T09:39:59-05:00"},
    {"observed_at": "2026-11-20T09:46:00-05:00"}, {"symbol": "SPX"}, {"expiration_date": "2026-11-23"},
])
def test_unusable_gamma_never_defaults_to_zero_or_changes_required_factors(changes):
    out = run(gamma=chain(**changes), vix=vix())
    assert out["gex"]["value"] is None
    assert out["combined"] == "PASS"


def test_gamma_duplicates_missing_and_negative_gamma_are_unknown():
    g = chain()
    g["contracts"][1]["contract_id"] = g["contracts"][0]["contract_id"]
    assert run(gamma=g)["gex"]["value"] is None
    for key, value in (("gamma", None), ("gamma", -.05), ("multiplier", 10), ("open_interest", 1.5)):
        g = chain()
        g["contracts"][1][key] = value
        assert run(gamma=g)["gex"]["value"] is None
