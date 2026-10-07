"""Frozen, research-only opening-range and reference-level observations.

The caller supplies independently validated, complete historical sessions and
point-in-time prior-session context. This module has no provider, broker, model,
calibration, strategy-registry, or P&L integration. The two assets are never
pooled: SPX has no premarket/volume feature, even if a caller supplies one.

Directional labels start *after* a completed trigger candle. They describe
which symmetric underlying-price barrier is observed first within 60 minutes,
not a fill, recommendation, option return, or executable strategy. Events must
close strictly before 10:30 ET. Breakout subgroups filter the same first close
break rather than select a more attractive subsequent breakout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

from intellidhan_schemas import Bar, Timeframe


ET = ZoneInfo("America/New_York")
UNIVERSE = ("SPY", "SPX")
OPENING_WINDOWS = (5, 15, 30)
PRIMARY_OPENING_MINUTES = 15
LABEL_MINUTES = 60
BARRIER_ATR = 0.25
NARROW_OR_ATR_MAX = 0.15
WIDE_OR_ATR_MIN = 0.30
STEP = timedelta(minutes=5)
EVENT_CUTOFF = time(10, 30)

PATTERN_IDS = (
    "orb_first_close_break",
    "orb_cleared_prior_boundary",
    "orb_cleared_premarket_boundary",
    "orb_narrow_break",
    "orb_wide_break",
    "orb_failed_break",
    "prior_boundary_sweep_rejection",
    "premarket_boundary_sweep_rejection",
    "contain_pdl_pmh",
    "contain_pml_pdh",
    "contain_pdl_pdh",
    "contain_pml_pmh",
)
PREMARKET_PATTERNS = frozenset({
    "orb_cleared_premarket_boundary", "premarket_boundary_sweep_rejection",
    "contain_pdl_pmh", "contain_pml_pdh", "contain_pml_pmh",
})
CONTAINMENT_LEVELS = {
    "contain_pdl_pmh": ("previous_low", "premarket_high"),
    "contain_pml_pdh": ("premarket_low", "previous_high"),
    "contain_pdl_pdh": ("previous_low", "previous_high"),
    "contain_pml_pmh": ("premarket_low", "premarket_high"),
}


@dataclass(frozen=True)
class ResearchSession:
    """One symbol/day; timestamps are bar closes, not bar opens.

    ``bars`` contains every RTH 5m bar. ``premarket`` is either unavailable or
    every SPY 5m bar closing 04:05 through 09:30 ET. Previous levels and ATR
    must be known from the immediately preceding completed exchange session.
    Calendar/licensing/availability validation is the caller's responsibility;
    structural checks here prevent cross-symbol or malformed-window reuse.
    """

    symbol: str
    day: date
    bars: tuple[Bar, ...]
    premarket: tuple[Bar, ...] | None
    previous_high: float | None
    previous_low: float | None
    previous_close: float | None
    atr: float | None


def pattern_universe() -> list[dict]:
    """Fresh JSON-ready definitions; there are 12 observations per OR window."""
    return [
        {
            "pattern_id": pattern,
            "kind": "CONTAINMENT" if pattern in CONTAINMENT_LEVELS else "DIRECTIONAL",
            "symbols": ["SPY"] if pattern in PREMARKET_PATTERNS else list(UNIVERSE),
            "opening_minutes": list(OPENING_WINDOWS),
            "primary_opening_minutes": PRIMARY_OPENING_MINUTES,
            "event_cutoff_et": "10:30 exclusive",
            "horizon_minutes": LABEL_MINUTES,
            "outcomes": (
                ["CONTAINED", "BREACHED"] if pattern in CONTAINMENT_LEVELS else
                ["FAVORABLE_FIRST", "ADVERSE_FIRST", "NEITHER", "AMBIGUOUS"]
            ),
            "execution_authorized": False,
        }
        for pattern in PATTERN_IDS
    ]


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _at(day: date, value: time) -> datetime:
    return datetime.combine(day, value, tzinfo=ET)


def _price(value: float | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _series_valid(
    session: ResearchSession, bars: tuple[Bar, ...], first: datetime, last: datetime,
) -> bool:
    count = int((last - first) / STEP) + 1
    if len(bars) != count:
        return False
    for index, bar in enumerate(bars):
        if (
            bar.symbol != session.symbol or bar.timeframe != Timeframe.M5
            or bar.ts_close != first + index * STEP
            or not all(_price(value) is not None for value in (
                bar.open, bar.high, bar.low, bar.close,
            ))
            or not bar.low <= bar.open <= bar.high
            or not bar.low <= bar.close <= bar.high
        ):
            return False
    return True


def _rth_valid(session: ResearchSession) -> bool:
    if not session.bars:
        return False
    last = session.bars[-1].ts_close.astimezone(ET)
    # Historical holiday/early-close verification belongs to the supplied
    # exchange calendar, not an independently maintained calendar in this file.
    if last.date() != session.day or last.time() not in {time(13), time(16)}:
        return False
    return _series_valid(session, session.bars, _at(session.day, time(9, 35)), last)


def _row(session: ResearchSession, opening_minutes: int, pattern: str) -> dict:
    return {
        "symbol": session.symbol,
        "session_date": session.day.isoformat(),
        "opening_minutes": opening_minutes,
        "pattern_id": pattern,
        "kind": "CONTAINMENT" if pattern in CONTAINMENT_LEVELS else "DIRECTIONAL",
        "status": "NO_EVENT",
        "reason": "NO_QUALIFYING_EVENT",
        "event_ts": None,
        "horizon_ts": None,
        "direction": None,
        "anchor": None,
        "favorable_level": None,
        "adverse_level": None,
        "containment_low": None,
        "containment_high": None,
        "feature_as_of": None,
        "features": {},
        "outcome": None,
        "outcome_bar_close": None,
        "touch_timing": None,
        "research_only": True,
        "live_eligible": False,
        "execution_authorized": False,
        "label_scope": "UNDERLYING_PATTERN_NOT_OPTION_RETURN",
    }


def _unavailable(row: dict, reason: str) -> dict:
    row.update(status="INELIGIBLE", reason=reason)
    return row


def _window(session: ResearchSession, event: Bar) -> tuple[Bar, ...]:
    end = event.ts_close + timedelta(minutes=LABEL_MINUTES)
    rows = tuple(bar for bar in session.bars if event.ts_close < bar.ts_close <= end)
    # Do not substitute an incomplete tail or forward-fill a missing outcome.
    return rows if _series_valid(session, rows, event.ts_close + STEP, end) else ()


def _observed(row: dict, event: Bar) -> None:
    row.update(
        status="OBSERVED", reason=None, event_ts=_stamp(event.ts_close),
        horizon_ts=_stamp(event.ts_close + timedelta(minutes=LABEL_MINUTES)),
        anchor=event.close, feature_as_of=_stamp(event.ts_close),
    )


def _directional(session: ResearchSession, row: dict, event: Bar, direction: int) -> dict:
    _observed(row, event)
    row["direction"] = "LONG" if direction > 0 else "SHORT"
    distance = BARRIER_ATR * session.atr
    upper, lower = event.close + distance, event.close - distance
    row["favorable_level"] = upper if direction > 0 else lower
    row["adverse_level"] = lower if direction > 0 else upper
    future = _window(session, event)
    if not future:
        return _unavailable(row, "INCOMPLETE_OUTCOME_WINDOW")
    for bar in future:
        side = None
        timing = "INTRABAR"
        # An open beyond a barrier establishes its precedence over subsequent
        # extremes. Otherwise a double-touch candle has unknowable ordering.
        if bar.open >= upper:
            side, timing = 1, "BAR_OPEN"
        elif bar.open <= lower:
            side, timing = -1, "BAR_OPEN"
        elif bar.high >= upper and bar.low <= lower:
            row.update(
                outcome="AMBIGUOUS", reason="BOTH_BARRIERS_IN_SAME_BAR",
                outcome_bar_close=_stamp(bar.ts_close), touch_timing="UNORDERED_INTRABAR",
            )
            return row
        elif bar.high >= upper:
            side = 1
        elif bar.low <= lower:
            side = -1
        if side is not None:
            row.update(
                outcome="FAVORABLE_FIRST" if side == direction else "ADVERSE_FIRST",
                outcome_bar_close=_stamp(bar.ts_close), touch_timing=timing,
            )
            return row
    row.update(outcome="NEITHER", outcome_bar_close=row["horizon_ts"])
    return row


def _containment(session: ResearchSession, row: dict, event: Bar) -> dict:
    low_key, high_key = CONTAINMENT_LEVELS[row["pattern_id"]]
    low, high = row["features"][low_key], row["features"][high_key]
    if low is None or high is None:
        return _unavailable(row, "MISSING_REQUIRED_REFERENCE_LEVEL")
    if low >= high:
        return _unavailable(row, "INVALID_REFERENCE_INTERVAL")
    row.update(containment_low=low, containment_high=high)
    if not low < event.close < high:
        row["reason"] = "FREEZE_CLOSE_NOT_STRICTLY_INSIDE"
        return row
    _observed(row, event)
    future = _window(session, event)
    if not future:
        return _unavailable(row, "INCOMPLETE_OUTCOME_WINDOW")
    for bar in future:
        # Strict containment ends on a touch, not only on a close outside.
        if bar.low <= low or bar.high >= high:
            row.update(outcome="BREACHED", outcome_bar_close=_stamp(bar.ts_close))
            return row
    row.update(outcome="CONTAINED", outcome_bar_close=row["horizon_ts"])
    return row


def _first_sweep(bars: tuple[Bar, ...], low: float, high: float) -> tuple[Bar, int] | None:
    for bar in bars:
        if not low < bar.close < high:
            continue
        swept_high, swept_low = bar.high > high, bar.low < low
        if swept_high or swept_low:
            # Zero marks an ambiguous trigger, not a direction inferred later.
            direction = 0 if swept_high and swept_low else (-1 if swept_high else 1)
            return bar, direction
    return None


def pattern_observations(session: ResearchSession, opening_minutes: int = 15) -> list[dict]:
    """Return all 12 declared rows, retaining ineligible/no-event denominators.

    The 15m specification is primary; 5m/30m are explicit exploratory variants.
    Price breaks/sweeps are strict; a failed break closes back into the inclusive
    OR interval. Containment eligibility and its entire future path are strict.
    All labels exclude the trigger candle. No statistic here is trade P&L.
    """
    if (
        isinstance(opening_minutes, bool) or not isinstance(opening_minutes, int)
        or opening_minutes not in OPENING_WINDOWS
    ):
        raise ValueError("opening_minutes must be one of 5, 15, 30")
    rows = [_row(session, opening_minutes, pattern) for pattern in PATTERN_IDS]
    if session.symbol not in UNIVERSE:
        return [_unavailable(row, "UNSUPPORTED_SYMBOL") for row in rows]
    if not _rth_valid(session):
        return [_unavailable(row, "INVALID_OR_INCOMPLETE_RTH_BARS") for row in rows]

    opening = session.bars[:opening_minutes // 5]
    freeze = opening[-1]
    opening_high, opening_low = max(b.high for b in opening), min(b.low for b in opening)
    width = opening_high - opening_low
    atr = _price(session.atr)
    pm = session.premarket
    pm_valid = session.symbol == "SPY" and pm is not None and _series_valid(
        session, pm, _at(session.day, time(4, 5)), _at(session.day, time(9, 30)),
    )
    previous_close = _price(session.previous_close)
    features = {
        "opening_range_as_of": _stamp(freeze.ts_close),
        "opening_high": opening_high, "opening_low": opening_low,
        "opening_width": width, "opening_width_atr": width / atr if atr else None,
        "freeze_close": freeze.close, "prior_atr": atr,
        "previous_high": _price(session.previous_high),
        "previous_low": _price(session.previous_low), "previous_close": previous_close,
        "premarket_high": max(b.high for b in pm) if pm_valid else None,
        "premarket_low": min(b.low for b in pm) if pm_valid else None,
        "opening_gap_atr": (
            (opening[0].open - previous_close) / atr if atr and previous_close else None
        ),
    }
    eligible_bars = tuple(
        bar for bar in session.bars if
        freeze.ts_close < bar.ts_close < _at(session.day, EVENT_CUTOFF)
    )
    breakout = next((
        (bar, 1 if bar.close > opening_high else -1) for bar in eligible_bars
        if bar.close > opening_high or bar.close < opening_low
    ), None)
    failed = None
    previous = freeze
    for bar in eligible_bars:
        if opening_low <= bar.close <= opening_high:
            if previous.close > opening_high or previous.close < opening_low:
                failed = (bar, -1 if previous.close > opening_high else 1)
                break
        previous = bar

    for row in rows:
        pattern = row["pattern_id"]
        row.update(features=dict(features), feature_as_of=_stamp(freeze.ts_close))
        if pattern in PREMARKET_PATTERNS and session.symbol != "SPY":
            _unavailable(row, "SPX_PREMARKET_NOT_APPLICABLE")
            continue
        if pattern in PREMARKET_PATTERNS and not pm_valid:
            _unavailable(row, "MISSING_OR_INCOMPLETE_SPY_PREMARKET")
            continue
        if pattern in CONTAINMENT_LEVELS:
            _containment(session, row, freeze)
            continue
        if atr is None:
            _unavailable(row, "MISSING_OR_INVALID_PRIOR_ATR")
            continue
        if width <= 0:
            _unavailable(row, "NONPOSITIVE_OPENING_RANGE")
            continue
        event = breakout
        if pattern == "orb_failed_break":
            event = failed
        elif pattern in {"prior_boundary_sweep_rejection", "premarket_boundary_sweep_rejection"}:
            prefix = "previous" if pattern.startswith("prior") else "premarket"
            low, high = features[f"{prefix}_low"], features[f"{prefix}_high"]
            if low is None or high is None or low >= high:
                _unavailable(row, "MISSING_OR_INVALID_REFERENCE_RANGE")
                continue
            event = _first_sweep(eligible_bars, low, high)
        elif pattern == "orb_narrow_break" and width / atr > NARROW_OR_ATR_MAX:
            row["reason"] = "OPENING_RANGE_NOT_NARROW"
            continue
        elif pattern == "orb_wide_break" and width / atr < WIDE_OR_ATR_MIN:
            row["reason"] = "OPENING_RANGE_NOT_WIDE"
            continue
        if pattern in {"orb_cleared_prior_boundary", "orb_cleared_premarket_boundary"}:
            prefix = "previous" if pattern == "orb_cleared_prior_boundary" else "premarket"
            low, high = features[f"{prefix}_low"], features[f"{prefix}_high"]
            if low is None or high is None or low >= high:
                _unavailable(row, "MISSING_OR_INVALID_REFERENCE_RANGE")
                continue
            if event and not (event[0].close > high if event[1] > 0 else event[0].close < low):
                row["reason"] = "FIRST_BREAKOUT_HAS_NOT_CLEARED_REFERENCE"
                continue
        if event is None:
            continue
        event_bar, direction = event
        if direction == 0:
            _observed(row, event_bar)
            row.update(
                outcome="AMBIGUOUS", reason="BOTH_REFERENCE_BOUNDARIES_SWEPT",
                outcome_bar_close=_stamp(event_bar.ts_close), touch_timing="AMBIGUOUS_TRIGGER",
            )
        else:
            _directional(session, row, event_bar, direction)
    return rows
