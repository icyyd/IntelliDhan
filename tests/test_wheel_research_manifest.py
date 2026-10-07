"""Research handoff integrity; these tests do not validate strategy performance."""

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from intellidhan_engine.strategies import REGISTRY


RESEARCH = Path(__file__).resolve().parents[1] / "docs" / "research"
MANIFEST = RESEARCH / "wheel-deal-candidates.v1.json"


def load_manifest():
    return json.loads(MANIFEST.read_text())


def test_wheel_manifest_is_explicitly_unvalidated_and_disabled():
    data = load_manifest()
    assert data["schema_version"] == "wheel-research-v1"
    assert data["status"] == "RESEARCH_ONLY"
    assert data["backtest_runs"] == []
    assert data["performance"] is None
    assert data["protocol_frozen"] is False
    assert data["runtime_loaded"] is False
    date.fromisoformat(data["reviewed_on"])
    assert (RESEARCH / data["research_note"]).is_file()
    registry_keys = {strategy.key for strategy in REGISTRY}
    for row in [data, *data["candidates"]]:
        for flag in ("execution_authorized", "engine_registered", "live_eligible"):
            assert row[flag] is False
        assert row["performance"] is None
    for candidate in data["candidates"]:
        assert candidate["id"] not in registry_keys
        assert candidate["validation_status"] == "NOT_BACKTESTED"
        assert candidate["unresolved"]
        assert candidate["rule_origin"] == (
            "INTELLIDHAN_PROPOSAL_INSPIRED_BY_SOURCE_NOT_AUTHOR_RULESET"
        )


def test_source_and_claim_references_are_complete_and_unique():
    data = load_manifest()
    source_ids = {source["id"] for source in data["sources"]}
    assert len(source_ids) == len(data["sources"])
    claim_ids = {claim["id"] for claim in data["claims"]}
    assert len(claim_ids) == len(data["claims"])
    candidate_ids = {candidate["id"] for candidate in data["candidates"]}
    assert len(candidate_ids) == len(data["candidates"])
    for source in data["sources"]:
        assert source["title"] and source["review_status"]
        url = urlparse(source["url"])
        assert url.scheme == "https" and url.hostname
        assert not url.username and not url.password and not url.query
    for claim in data["claims"]:
        assert claim["source_ids"]
        assert set(claim["source_ids"]) <= source_ids
        assert claim["classification"] and claim["summary"]
        assert set(claim.get("source_locators", {})) <= set(claim["source_ids"])
    for candidate in data["candidates"]:
        assert candidate["claim_ids"]
        assert set(candidate["claim_ids"]) <= claim_ids
        assert set(candidate.get("depends_on", [])) <= candidate_ids - {candidate["id"]}
    prose = (RESEARCH / data["research_note"]).read_text()
    prose_urls = set(re.findall(r"https://[^\s)]+", prose))
    assert prose_urls <= {source["url"] for source in data["sources"]}


def test_review_coverage_does_not_claim_unreviewed_videos_or_account_verification():
    data = load_manifest()
    decks = [source for source in data["sources"] if source["kind"] == "LESSON_DECK"]
    coverage = data["coverage"]
    assert len(decks) == coverage["public_lesson_decks_reviewed"] == 12
    assert sum(source["slides_reviewed"] for source in decks) == (
        coverage["public_slides_reviewed"]
    ) == 151
    pdf = next(source for source in data["sources"] if source["kind"] == "POSITION_PDF")
    assert pdf["pages_reviewed"] == coverage["position_pdf_pages_reviewed"] == 4
    assert re.fullmatch(r"[a-f0-9]{64}", pdf["sha256"])
    assert coverage["youtube_broadcasts_reviewed"] == 0
    assert coverage["youtube_broadcasts_inventoried"] == 24
    assert coverage["book_reviewed"] is False
    assert coverage["broker_records_verified"] is False


def test_research_does_not_reuse_long_option_sizing_or_pretend_assignment_is_supported():
    data = load_manifest()
    constraints = data["shared_constraints"]
    for flag in (
        "short_opening_supported_by_current_executor", "historical_option_replay_implemented",
        "assignment_and_share_ledger_implemented", "adjusted_contracts_allowed",
        "margin_borrowing_allowed", "automatic_rolling_allowed", "long_option_sizing_reusable",
    ):
        assert constraints[flag] is False
    assert "SPX" in constraints["exclude_underlyings_from_equity_wheel"]
    assert all(value is None for value in constraints["unresolved_protocol"].values())
    assert "all expirations" in constraints["call_share_reservation"]
    assert "Full strike" in constraints["put_cash_reservation"]
    strategies = [row for row in data["candidates"] if row["kind"] == "STRATEGY_CANDIDATE"]
    assert len(strategies) == 3
    analyst = next(row for row in data["candidates"] if row["kind"] == "ANALYST_RESEARCH_CHECK")
    assert "NOT_AN_ENTRY_STRATEGY" in analyst["horizon"]


def test_cost_lifecycle_and_benchmark_gaps_cannot_be_mistaken_for_test_results():
    data = load_manifest()
    assert data["cost_policy"]["result_basis"] == "PRETAX_NET_OF_DECLARED_COSTS"
    assert data["cost_policy"]["taxes"] == "EXCLUDED_NO_AFTER_TAX_CLAIM"
    for field in (
        "fee_schedule", "slippage_and_spread_stress_schedule",
        "cash_yield_series_and_reinvestment_method",
    ):
        assert data["cost_policy"][field] is None
    lifecycle = data["lifecycle_scenarios"]
    assert lifecycle["status"] == "NOT_SPECIFIED_NOT_IMPLEMENTED"
    assert {case["id"] for case in lifecycle["cases"]} == {
        "EARLY_PUT_ASSIGNMENT", "EX_DIVIDEND_CALL_ASSIGNMENT", "ITM_EXPIRATION",
        "AFTER_HOURS_EXERCISE_AND_GAP", "SETTLEMENT_AND_AVAILABLE_CASH_SHARES",
        "FAILED_OR_PARTIAL_CLOSE", "HALT_OR_MISSING_QUOTE",
    }
    assert all(case["specification"] is None for case in lifecycle["cases"])
    source_ids = {source["id"] for source in data["sources"]}
    for benchmark in data["benchmarks"]:
        assert benchmark["performance"] is None
        assert set(benchmark.get("source_ids", [])) <= source_ids
        if benchmark.get("source_ids"):
            assert benchmark["kind"] == "BENCHMARK_DESIGN_NOT_STRATEGY_RESULT"
