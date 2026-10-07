from copy import deepcopy
from pathlib import Path
import json

import pytest

from intellidhan_learning.orb_report import build_report, split_for_day, summarize
from intellidhan_learning.orb_patterns import (
    BARRIER_ATR, EVENT_CUTOFF, LABEL_MINUTES, OPENING_WINDOWS, PATTERN_IDS,
    PRIMARY_OPENING_MINUTES,
)


def protocol():
    return json.loads((Path(__file__).parents[1] /
                       "docs/research/orb-pattern-protocol.v1.json").read_text())


def row(day="2026-09-01", outcome="FAVORABLE_FIRST", status="OBSERVED"):
    return dict(session_date=day, symbol="SPY", opening_minutes=15,
                pattern_id="orb_first_close_break", status=status, outcome=outcome)


def test_fixed_splits_and_no_test_selection():
    p = protocol()
    assert split_for_day("2025-04-05", p) == "development"
    assert split_for_day("2025-04-06", p) == "validation"
    assert split_for_day("2025-10-06", p) == "retrospective_test"
    with pytest.raises(ValueError):
        split_for_day("2026-10-06", p)
    result = build_report([row()], {}, p, {})
    assert not result["three_year_test_complete"]
    assert not result["execution_authorized"]
    assert not result["live_eligible"]
    assert result["selected_strategy"] is None
    assert len(result["cells"]) == 2 * 3 * 12 * 4
    assert all(c["trading_win_rate"] is None for c in result["cells"])


def test_protocol_matches_executable_constants():
    p = protocol()
    assert tuple(p["patterns"]) == PATTERN_IDS
    assert sorted([p["primary_opening_minutes"], *p["exploratory_opening_minutes"]]) == list(
        OPENING_WINDOWS
    )
    assert p["primary_opening_minutes"] == PRIMARY_OPENING_MINUTES
    assert p["barrier_prior_atr_fraction"] == BARRIER_ATR
    assert p["horizon_minutes"] == LABEL_MINUTES
    assert p["event_cutoff_exclusive_et"] == EVENT_CUTOFF.strftime("%H:%M")


def test_ambiguous_and_no_event_are_not_fabricated_losses():
    rows = [row(), row("2026-09-02", "AMBIGUOUS"),
            row("2026-09-03", None, "NO_EVENT"),
            row("2026-09-04", "NEITHER")]
    result = summarize(rows)
    assert result["observed_events"] == 3
    assert result["known_outcomes"] == 2
    assert result["success_fraction_known"] == .5
    assert result["success_fraction_ambiguity_bounds"] == [1 / 3, 2 / 3]
    assert result["pointwise_block_bootstrap_95pct"] is None


def test_duplicate_observations_are_rejected():
    with pytest.raises(ValueError):
        build_report([row(), deepcopy(row())], {}, protocol(), {})
    with pytest.raises(ValueError):
        summarize([row(), row()])


def test_bootstrap_is_reproducible_and_empty_is_unknown():
    rows = [row(f"2026-09-{d:02}", "FAVORABLE_FIRST" if d % 3 else "ADVERSE_FIRST")
            for d in range(1, 26)]
    assert summarize(rows) == summarize(rows)
    interval = summarize(rows)["pointwise_block_bootstrap_95pct"]
    assert 0 <= interval[0] <= interval[1] <= 1
    assert summarize([])["success_fraction_known"] is None
