"""Synthetic pattern-label tests, never evidence of a trading edge."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from intellidhan_learning.orb_patterns import (
    CONTAINMENT_LEVELS,
    PATTERN_IDS,
    PREMARKET_PATTERNS,
    ResearchSession,
    pattern_observations,
    pattern_universe,
)
from intellidhan_schemas import Bar, Timeframe


ET = ZoneInfo("America/New_York")
DAY = date(2024, 11, 27)


def stamp(clock: str, day: date = DAY) -> datetime:
    hour, minute = map(int, clock.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=ET).astimezone(timezone.utc)


def make_bar(symbol, closed, *, open=100.0, high=100.5, low=99.5, close=100.0):
    return Bar(
        symbol=symbol, timeframe=Timeframe.M5, ts_close=closed,
        open=open, high=high, low=low, close=close,
        volume=1000, source="synthetic-not-market-evidence",
    )


def session(*, symbol="SPY", atr=10.0, half_day=False, premarket=True):
    count = 42 if half_day else 78
    bars = tuple(
        make_bar(symbol, stamp("09:35") + timedelta(minutes=5 * index))
        for index in range(count)
    )
    pm = tuple(
        make_bar(symbol, stamp("04:05") + timedelta(minutes=5 * index), high=102, low=98)
        for index in range(66)
    ) if premarket else None
    return ResearchSession(symbol, DAY, bars, pm, 103.0, 97.0, 99.0, atr)


def change(source, clock, **ohlc):
    bars = list(source.bars)
    index = next(index for index, bar in enumerate(bars) if bar.ts_close == stamp(clock))
    values = {name: getattr(bars[index], name) for name in ("open", "high", "low", "close")}
    values.update(ohlc)
    bars[index] = make_bar(source.symbol, stamp(clock), **values)
    return replace(source, bars=tuple(bars))


def breakout(source=None, *, down=False):
    source = source or session()
    return change(source, "09:50", **(
        {"open": 100, "high": 100.1, "low": 99.2, "close": 99.3} if down else
        {"open": 100, "high": 100.8, "low": 99.9, "close": 100.7}
    ))


def observed(source, key="orb_first_close_break", window=15):
    return next(row for row in pattern_observations(source, window) if row["pattern_id"] == key)


def test_all_declared_patterns_emit_even_without_events_and_are_json_ready():
    rows = pattern_observations(session())
    assert tuple(row["pattern_id"] for row in rows) == PATTERN_IDS
    assert len(rows) == 12
    json.dumps(rows, allow_nan=False)
    for row in rows:
        assert row["research_only"] is True
        assert row["execution_authorized"] is False and row["live_eligible"] is False
        assert row["label_scope"] == "UNDERLYING_PATTERN_NOT_OPTION_RETURN"
        assert not {"pnl", "win_rate", "order", "entry_price", "confidence"} & row.keys()
    assert observed(session())["status"] == "NO_EVENT"
    assert observed(session())["reason"] == "NO_QUALIFYING_EVENT"
    assert all(row["status"] == "OBSERVED" for row in rows if row["kind"] == "CONTAINMENT")
    definitions = pattern_universe()
    assert [row["pattern_id"] for row in definitions] == list(PATTERN_IDS)
    definitions[0]["symbols"].clear()
    assert pattern_universe()[0]["symbols"] == ["SPY", "SPX"]


@pytest.mark.parametrize("window,clock", [(5, "09:40"), (15, "09:50"), (30, "10:05")])
def test_opening_range_variants_freeze_completed_candles(window, clock):
    source = change(session(), clock, high=100.8, close=100.7)
    row = observed(source, window=window)
    assert row["event_ts"] == stamp(clock).isoformat()
    assert row["features"]["opening_range_as_of"] == (
        stamp("09:30") + timedelta(minutes=window)
    ).isoformat()
    assert row["features"]["opening_high"] == 100.5
    assert row["anchor"] == 100.7


@pytest.mark.parametrize("value", [True, 15.0, 10, 0, 60])
def test_undeclared_opening_windows_are_rejected(value):
    with pytest.raises(ValueError, match="one of"):
        pattern_observations(session(), value)


def test_future_prices_can_change_label_but_not_frozen_features_or_first_trigger():
    source = breakout()
    favorable = change(source, "09:55", open=100.7, high=103.3, low=100.6, close=103.2)
    adverse = change(source, "09:55", open=100.7, high=100.8, low=98.0, close=98.1)
    a, b = observed(favorable), observed(adverse)
    assert a["outcome"] == "FAVORABLE_FIRST"
    assert b["outcome"] == "ADVERSE_FIRST"
    for field in ("features", "event_ts", "feature_as_of", "anchor", "direction",
                  "favorable_level", "adverse_level"):
        assert a[field] == b[field]
    assert a["event_ts"] == stamp("09:50").isoformat()


def test_trigger_candle_extremes_are_never_used_as_future_label():
    source = change(breakout(), "09:50", high=105, low=95)
    assert observed(source)["outcome"] == "NEITHER"


def test_barrier_double_touch_is_ambiguous_and_never_selected_from_later_prices():
    source = change(breakout(), "09:55", open=100.7, high=104, low=97, close=100.7)
    row = observed(source)
    assert row["outcome"] == "AMBIGUOUS"
    assert row["reason"] == "BOTH_BARRIERS_IN_SAME_BAR"
    assert row["outcome_bar_close"] == stamp("09:55").isoformat()
    assert row["touch_timing"] == "UNORDERED_INTRABAR"


def test_open_beyond_barrier_establishes_order_even_when_both_extremes_cross():
    source = change(breakout(), "09:55", open=104, high=105, low=97, close=100)
    row = observed(source)
    assert row["outcome"] == "FAVORABLE_FIRST"
    assert row["touch_timing"] == "BAR_OPEN"


@pytest.mark.parametrize("down", [False, True])
def test_symmetric_long_short_barriers_use_exact_touch_and_next_sixty_minutes(down):
    source = breakout(down=down)
    expected = observed(source)["favorable_level"]
    values = {"low": expected} if down else {"high": expected}
    source = change(source, "10:50", **values)
    row = observed(source)
    assert row["outcome"] == "FAVORABLE_FIRST"
    assert row["horizon_ts"] == stamp("10:50").isoformat()
    assert row["outcome_bar_close"] == stamp("10:50").isoformat()
    beyond = change(breakout(down=down), "10:55", **values)
    assert observed(beyond)["outcome"] == "NEITHER"


def test_ten_thirty_close_is_excluded_but_ten_twenty_five_is_eligible():
    excluded = change(session(), "10:30", high=101, close=100.8)
    assert observed(excluded)["status"] == "NO_EVENT"
    included = change(session(), "10:25", high=101, close=100.8)
    assert observed(included)["event_ts"] == stamp("10:25").isoformat()


def test_clearance_filters_same_first_break_not_later_more_attractive_event():
    source = change(breakout(), "10:00", open=100, high=104, low=99.5, close=103.5)
    for key in ("orb_cleared_prior_boundary", "orb_cleared_premarket_boundary"):
        row = observed(source, key)
        assert row["status"] == "NO_EVENT"
        assert row["reason"] == "FIRST_BREAKOUT_HAS_NOT_CLEARED_REFERENCE"
        assert row["event_ts"] is None
    assert observed(source)["event_ts"] == stamp("09:50").isoformat()


@pytest.mark.parametrize("down", [False, True])
def test_cleared_reference_boundary_uses_directional_side(down):
    source = change(session(), "09:50", **(
        {"open": 100, "high": 100.5, "low": 96, "close": 96.5} if down else
        {"open": 100, "high": 104, "low": 99.5, "close": 103.5}
    ))
    for key in ("orb_cleared_prior_boundary", "orb_cleared_premarket_boundary"):
        assert observed(source, key)["status"] == "OBSERVED"


@pytest.mark.parametrize(("atr", "key", "expected"), [
    (1 / 0.15, "orb_narrow_break", "OBSERVED"),
    (1 / 0.151, "orb_narrow_break", "NO_EVENT"),
    (1 / 0.30, "orb_wide_break", "OBSERVED"),
    (1 / 0.299, "orb_wide_break", "NO_EVENT"),
])
def test_frozen_range_thresholds_are_inclusive(atr, key, expected):
    assert observed(breakout(session(atr=atr)), key)["status"] == expected


def test_failed_break_requires_previous_close_outside_not_wick_only():
    source = breakout()
    row = observed(source, "orb_failed_break")
    assert row["event_ts"] == stamp("09:55").isoformat()
    assert row["direction"] == "SHORT"
    wick_only = change(session(), "09:50", high=103, close=100)
    assert observed(wick_only, "orb_failed_break")["status"] == "NO_EVENT"


@pytest.mark.parametrize("key", ["prior_boundary_sweep_rejection", "premarket_boundary_sweep_rejection"])
def test_two_sided_reference_sweep_is_ambiguous_not_a_chosen_direction(key):
    source = change(session(), "09:50", high=104, low=96, close=100)
    row = observed(source, key)
    assert row["status"] == "OBSERVED" and row["outcome"] == "AMBIGUOUS"
    assert row["reason"] == "BOTH_REFERENCE_BOUNDARIES_SWEPT"
    assert row["direction"] is None
    assert row["favorable_level"] is None and row["adverse_level"] is None
    assert row["event_ts"] == stamp("09:50").isoformat()


def test_reference_sweep_is_strict_and_first_rejection_is_frozen():
    touched = change(session(), "09:50", high=103, close=100)
    assert observed(touched, "prior_boundary_sweep_rejection")["status"] == "NO_EVENT"
    swept = change(touched, "09:55", high=103.01, close=100)
    row = observed(swept, "prior_boundary_sweep_rejection")
    assert row["direction"] == "SHORT"
    assert row["event_ts"] == stamp("09:55").isoformat()


def test_containment_uses_all_future_extremes_and_boundary_touch_is_breach():
    source = change(session(), "10:45", high=102, close=100)
    row = observed(source, "contain_pml_pmh")
    assert row["outcome"] == "BREACHED"
    assert row["event_ts"] == stamp("09:45").isoformat()
    assert row["horizon_ts"] == stamp("10:45").isoformat()
    assert row["direction"] is None
    beyond = change(session(), "10:50", high=103, close=100)
    assert observed(beyond, "contain_pml_pmh")["outcome"] == "CONTAINED"


def test_containment_is_not_triggered_on_equal_or_reversed_boundaries():
    equal = replace(session(), previous_low=100)
    assert observed(equal, "contain_pdl_pmh")["status"] == "NO_EVENT"
    assert observed(equal, "contain_pdl_pmh")["reason"] == "FREEZE_CLOSE_NOT_STRICTLY_INSIDE"
    reversed_levels = replace(session(), previous_low=104)
    row = observed(reversed_levels, "contain_pdl_pmh")
    assert row["status"] == "INELIGIBLE" and row["reason"] == "INVALID_REFERENCE_INTERVAL"


@pytest.mark.parametrize("atr", [None, 0, float("nan"), float("inf")])
def test_missing_atr_blocks_directional_labels_without_suppressing_containment(atr):
    rows = pattern_observations(breakout(session(atr=atr)))
    for row in rows:
        if row["pattern_id"] not in CONTAINMENT_LEVELS:
            assert row["status"] == "INELIGIBLE"
            assert row["reason"] == "MISSING_OR_INVALID_PRIOR_ATR"
        else:
            assert row["status"] == "OBSERVED"
    json.dumps(rows, allow_nan=False)


def test_missing_previous_context_does_not_use_current_day_or_premarket_substitute():
    source = replace(breakout(), previous_high=None, previous_low=None, previous_close=None)
    assert observed(source)["status"] == "OBSERVED"
    for key in ("orb_cleared_prior_boundary", "prior_boundary_sweep_rejection", "contain_pdl_pdh"):
        assert observed(source, key)["status"] == "INELIGIBLE"
    assert observed(source)["features"]["opening_gap_atr"] is None


@pytest.mark.parametrize("missing", ["none", "gap", "duplicate", "other_symbol"])
def test_invalid_premarket_blocks_only_premarket_families(missing):
    source = session()
    pm = source.premarket
    if missing == "none":
        pm = None
    elif missing == "gap":
        pm = pm[:-1]
    elif missing == "duplicate":
        pm = (*pm[:-1], pm[-2])
    else:
        pm = (pm[0].model_copy(update={"symbol": "SPX"}), *pm[1:])
    source = replace(source, premarket=pm)
    for row in pattern_observations(source):
        if row["pattern_id"] in PREMARKET_PATTERNS:
            assert row["status"] == "INELIGIBLE"
        else:
            assert row["status"] != "INELIGIBLE"


def test_spx_never_gets_premarket_or_volume_features_even_when_supplied():
    source = breakout(session(symbol="SPX"))
    for row in pattern_observations(source):
        assert row["features"]["premarket_high"] is None
        assert row["features"]["premarket_low"] is None
        assert not any("volume" in key or "rvol" in key for key in row["features"])
        if row["pattern_id"] in PREMARKET_PATTERNS:
            assert row["reason"] == "SPX_PREMARKET_NOT_APPLICABLE"
    assert observed(source)["status"] == "OBSERVED"


def test_research_sessions_are_symbol_isolated_and_do_not_share_mutable_features():
    spy = observed(breakout())
    spx = observed(session(symbol="SPX"))
    assert spy["status"] == "OBSERVED" and spx["status"] == "NO_EVENT"
    spy["features"]["previous_high"] = 1
    assert observed(breakout())["features"]["previous_high"] == 103
    source = session()
    mixed = replace(source, bars=(source.bars[0].model_copy(update={"symbol": "SPX"}), *source.bars[1:]))
    assert all(row["status"] == "INELIGIBLE" for row in pattern_observations(mixed))


@pytest.mark.parametrize("mutation", ["gap", "duplicate", "unsorted", "tail", "timeframe"])
def test_structurally_incomplete_rth_is_never_scored(mutation):
    source = session()
    bars = list(source.bars)
    if mutation == "gap":
        bars.pop(12)
    elif mutation == "duplicate":
        bars[12] = bars[11]
    elif mutation == "unsorted":
        bars[11], bars[12] = bars[12], bars[11]
    elif mutation == "tail":
        bars.pop()
    else:
        bars[0] = bars[0].model_copy(update={"timeframe": Timeframe.M1})
    rows = pattern_observations(replace(source, bars=tuple(bars)))
    assert all(row["status"] == "INELIGIBLE" and row["outcome"] is None for row in rows)


def test_validated_half_day_still_uses_full_sixty_minute_morning_horizon():
    row = observed(breakout(session(half_day=True)))
    assert row["status"] == "OBSERVED"
    assert row["horizon_ts"] == stamp("10:50").isoformat()
    assert row["outcome"] == "NEITHER"


def test_unknown_instrument_cannot_be_pooled_with_spy_or_spx():
    rows = pattern_observations(session(symbol="ES"))
    assert all(row["reason"] == "UNSUPPORTED_SYMBOL" for row in rows)
