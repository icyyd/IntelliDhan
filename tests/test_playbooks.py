"""The educational catalog cannot become an execution or profitability signal."""

import json

from intellidhan_gateway.playbooks import CATALOG_VERSION, build_playbook_catalog
from intellidhan_schemas.option_policy import OPTION_HORIZONS
from intellidhan_schemas.signals import Module


def test_catalog_is_json_ready_and_display_only_at_every_level():
    catalog = json.loads(json.dumps(build_playbook_catalog(), allow_nan=False))
    assert catalog["version"] == CATALOG_VERSION
    assert catalog["status"] == "RESEARCH_ONLY"
    assert catalog["execution_authorized"] is False
    assert len(catalog["horizons"]) == 3
    assert len(catalog["playbooks"]) == 5
    for item in catalog["horizons"] + catalog["playbooks"]:
        assert item["execution_authorized"] is False
        assert not {"confidence", "win_rate", "probability", "entry_price", "order"} & item.keys()


def test_holding_sessions_are_not_option_calendar_dte():
    horizons = {item["id"]: item for item in build_playbook_catalog()["horizons"]}
    for module, policy in OPTION_HORIZONS.items():
        horizon = horizons[module.value]
        assert horizon["holding_period"]["unit"] == "TRADING_SESSIONS"
        assert horizon["option_expiry"] == {
            "min_dte": policy.min_dte,
            "max_dte": policy.max_dte,
            "preferred_min_dte": policy.preferred_min_dte,
            "preferred_max_dte": policy.preferred_max_dte,
            "unit": "CALENDAR_DAYS",
            "status": "RESEARCH_SELECTION_WINDOW",
        }
    swing = horizons[Module.SWING.value]
    assert swing["holding_period"]["min_sessions"] == 2
    assert swing["holding_period"]["max_sessions"] == 5
    assert swing["holding_period"]["status"] == "REQUESTED_NOT_ENFORCED_OR_VALIDATED"
    assert swing["option_expiry"]["min_dte"] == 21
    assert swing["option_expiry"]["max_dte"] == 90
    assert "not a maximum holding period" in swing["current_behavior"]
    leaps = horizons[Module.LEAPS.value]
    assert leaps["holding_period"]["max_sessions"] is None
    assert leaps["option_expiry"]["min_dte"] == 365
    assert "not a one-year forecast" in leaps["current_behavior"]


def test_all_playbooks_expose_limits_and_current_implementation_truth():
    catalog = build_playbook_catalog()
    horizons = {row["id"] for row in catalog["horizons"]}
    playbooks = {row["id"]: row for row in catalog["playbooks"]}
    assert len(playbooks) == len(catalog["playbooks"])
    for row in playbooks.values():
        assert row["horizon_id"] in horizons
        for field in ("name", "summary", "setup", "why_it_may_help", "when_to_avoid",
                      "exit_review", "implementation_status", "validation_status",
                      "evidence_note", "gaps"):
            assert row[field]
    assert playbooks["ema9-mtf-scalp"]["validation_status"] == "NOT_LIVE_ELIGIBLE"
    assert "simple EMA crossover" in playbooks["ema9-mtf-scalp"]["evidence_note"]
    orr = playbooks["opening-range-reversal"]
    assert orr["implementation_status"] == "RESEARCH_BACKTEST_ONLY"
    assert orr["validation_status"] == "LATER_SAMPLE_DID_NOT_CONFIRM_EDGE"
    assert "negative after" in orr["evidence_note"]
    assert playbooks["swing-trend-pullback"]["validation_status"] == (
        "NOT_VALIDATED_FOR_REQUESTED_HOLD"
    )
    assert playbooks["leaps-quality-trend"]["implementation_status"] == "PROPOSED_NOT_IMPLEMENTED"


def test_course_spreads_are_unsupported_and_do_not_invent_entry_rules():
    spread = next(row for row in build_playbook_catalog()["playbooks"]
                  if row["id"] == "defined-risk-credit-spread-concept")
    assert spread["implementation_status"] == "UNSUPPORTED"
    assert spread["validation_status"] == "UNVALIDATED"
    assert "not transcribed" in spread["setup"]
    assert "no exact entry rules" in spread["setup"]
    assert "without averaging down or rolling" in spread["exit_review"]
    assert "not a guaranteed fill" in spread["exit_review"]
    assert "single long calls or puts only" in spread["evidence_note"]


def test_plain_actions_do_not_imply_a_position_or_execution_authority():
    catalog = build_playbook_catalog()
    assert "not an order" in catalog["action_language"]["BUY"]
    assert "not permission to open a short" in catalog["action_language"]["SELL"]
    assert "no clear new opportunity" in catalog["action_language"]["HOLD"]
    assert "holdings have not been assessed" in catalog["action_language"]["HOLD"]
    assert any("earlier exit" in note for note in catalog["guidance"])


def test_mutating_a_response_cannot_modify_future_catalogs():
    response = build_playbook_catalog()
    response["execution_authorized"] = True
    response["horizons"][0]["option_expiry"]["min_dte"] = -100
    response["playbooks"][0]["gaps"].clear()
    fresh = build_playbook_catalog()
    assert fresh["execution_authorized"] is False
    assert fresh["horizons"][0]["option_expiry"]["min_dte"] == 0
    assert fresh["playbooks"][0]["gaps"]
