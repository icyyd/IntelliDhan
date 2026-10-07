"""Research-only opening-range data preparation.

This loader is intentionally independent from the production ``MarketClock``.
It uses the lazily imported XNYS exchange calendar to validate completed
five-minute sessions and to build prior-session context without look-ahead.
It does not download data, fill gaps, or make a trading decision.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import math
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from intellidhan_schemas import Bar, Timeframe
from intellidhan_learning.orb_patterns import ResearchSession


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SUPPORTED_START = date(2023, 10, 6)
# End is exclusive.  The supported inclusive final session is 2026-10-05.
SUPPORTED_END_EXCLUSIVE = date(2026, 10, 6)
WARMUP_CALENDAR_DAYS = 42
ATR_PERIOD = 14

@dataclass(frozen=True)
class ORBAudit:
    """Session-level and input-quality audit for one symbol."""

    total_expected: int
    complete: int
    missing: int
    invalid: int
    pm_available: int
    reason_counts: Mapping[str, int]
    days: tuple[date, ...]
    complete_days: tuple[date, ...] = ()
    missing_days: tuple[date, ...] = ()
    invalid_days: tuple[date, ...] = ()
    schedule_version: str = "XNYS"
    schedule_sha256: str = ""
    symbol: str = ""
    expected_rth_bars: int = 0
    observed_rth_bars: int = 0

    @property
    def total_expected_bars(self) -> int:
        """Return the expected RTH row count, useful for coverage reports."""
        return self.expected_rth_bars

    @property
    def session_calendar_hash(self) -> str:
        """Stable alias used by research manifests."""
        return self.schedule_sha256

    @property
    def data_coverage(self) -> float:
        """Observed/expected RTH rows, with zero expected rows returning zero."""
        expected = self.total_expected_bars
        return self.observed_rth_bars / expected if expected else 0.0

@dataclass(frozen=True)
class _ScheduleDay:
    day: date
    open_et: datetime
    close_et: datetime
    rth_grid: tuple[datetime, ...]
    pre_grid: tuple[datetime, ...]


def _get_xnys_calendar(start: date | None = None, end: date | None = None):
    """Load the optional calendar dependency only when research is invoked."""
    try:
        import exchange_calendars as xcals
    except ImportError as exc:  # fail closed; do not substitute weekdays
        raise RuntimeError(
            "ORB research requires the optional exchange-calendars XNYS dependency"
        ) from exc
    return xcals.get_calendar("XNYS", start=start, end=end)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive timestamp rejected")
    return value.astimezone(UTC)


def _index_date(value: object) -> date:
    if isinstance(value, datetime):
        # Exchange-calendar session labels are dates.  Do not convert a
        # midnight UTC label through ET, which would move it to the prior day.
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    return date.fromisoformat(text[:10])


def _calendar_schedule(calendar, start: date, end_exclusive: date) -> list[tuple[date, datetime, datetime]]:
    """Read an XNYS schedule across calendar versions without guessing dates."""
    last = end_exclusive - timedelta(days=1)
    schedule = calendar.schedule
    if callable(schedule):
        schedule = schedule(start_date=start.isoformat(), end_date=last.isoformat())
    available_days = [_index_date(index) for index, _row in schedule.iterrows()]
    if not available_days:
        raise ValueError(f"XNYS calendar has no coverage for {start} through {end_exclusive}")
    # A requested edge may fall on a weekend or exchange holiday.  Allow the
    # normal long-weekend gap, but fail closed when an injected calendar is
    # plainly truncated before the requested warmup or study window.
    if min(available_days) - start > timedelta(days=4) or (
        end_exclusive - timedelta(days=1) - max(available_days) > timedelta(days=4)
    ):
        raise ValueError(f"XNYS calendar does not cover {start} through {end_exclusive}")
    all_rows: list[tuple[date, datetime, datetime]] = []
    for index, row in schedule.iterrows():
        day = _index_date(index)
        if not start <= day < end_exclusive:
            continue
        all_rows.append((day, _as_utc(row["open"]), _as_utc(row["close"])))
    if not all_rows:
        raise ValueError(f"XNYS calendar has no sessions for {start} through {end_exclusive}")
    return sorted(all_rows, key=lambda item: item[0])


def _grid(start: datetime, end: datetime) -> tuple[datetime, ...]:
    """Generate close timestamps, retaining only complete five-minute bars."""
    result: list[datetime] = []
    current = start + timedelta(minutes=5)
    while current <= end:
        result.append(current.astimezone(UTC))
        current += timedelta(minutes=5)
    return tuple(result)


def _schedule_days(calendar, start: date, end_exclusive: date) -> tuple[_ScheduleDay, ...]:
    rows = _calendar_schedule(calendar, start, end_exclusive)
    result: list[_ScheduleDay] = []
    for day, opened, closed in rows:
        open_et = opened.astimezone(ET)
        close_et = closed.astimezone(ET)
        if open_et.time() != time(9, 30) or close_et.minute % 5:
            raise ValueError(f"unsupported XNYS session boundary on {day}")
        pre_start = datetime.combine(day, time(4, 0), tzinfo=ET)
        rth_open = datetime.combine(day, time(9, 30), tzinfo=ET)
        result.append(
            _ScheduleDay(
                day=day,
                open_et=rth_open,
                close_et=close_et,
                rth_grid=_grid(rth_open, close_et),
                pre_grid=_grid(pre_start, rth_open),
            )
        )
    return tuple(result)


def _schedule_digest(days: Sequence[_ScheduleDay]) -> str:
    payload = "\n".join(
        f"{item.day.isoformat()}|{item.open_et.isoformat()}|{item.close_et.isoformat()}"
        for item in days
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _schedule_source_version() -> str:
    try:
        from importlib.metadata import version

        return f"exchange-calendars=={version('exchange-calendars')}:XNYS"
    except Exception:  # pragma: no cover - only the optional-dependency failure path
        return "XNYS"


def _valid_bar(bar: Bar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
    if not all(math.isfinite(float(value)) for value in values):
        return False
    if any(float(value) <= 0 for value in (bar.open, bar.high, bar.low, bar.close)):
        return False
    if bar.volume < 0:
        return False
    return bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high


def _true_range(current: tuple[float, float, float], previous_close: float) -> float:
    high, low, _close = current
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def _load_one_symbol(
    symbol: str,
    bars: Sequence[Bar],
    schedule: tuple[_ScheduleDay, ...],
    target_start: date,
    target_end: date,
    ex_div_dates: set[date],
) -> tuple[tuple[ResearchSession, ...], ORBAudit]:
    expected_by_day = {item.day: item for item in schedule}
    target_days = tuple(item.day for item in schedule if target_start <= item.day < target_end)
    rth_by_day: dict[date, dict[datetime, Bar]] = defaultdict(dict)
    pm_by_day: dict[date, dict[datetime, Bar]] = defaultdict(dict)
    invalid_days: set[date] = set()
    pm_invalid_days: set[date] = set()
    post_ignored = 0
    reason_counts: Counter[str] = Counter()

    for bar in bars:
        if bar.symbol.upper() != symbol.upper():
            continue
        try:
            local = _as_utc(bar.ts_close).astimezone(ET)
        except (AttributeError, TypeError, ValueError):
            reason_counts["invalid_timestamp"] += 1
            continue
        session = expected_by_day.get(local.date())
        if session is None:
            continue
        close_utc = _as_utc(bar.ts_close)
        if close_utc in session.rth_grid:
            if bar.timeframe != Timeframe.M5 or not _valid_bar(bar):
                invalid_days.add(local.date())
                reason_counts["invalid_bar"] += 1
                continue
            if close_utc in rth_by_day[local.date()]:
                invalid_days.add(local.date())
                reason_counts["duplicate_rth"] += 1
            else:
                rth_by_day[local.date()][close_utc] = (
                    bar if bar.symbol == symbol else bar.model_copy(update={"symbol": symbol})
                )
            continue
        if close_utc in session.pre_grid:
            if bar.timeframe != Timeframe.M5 or not _valid_bar(bar):
                pm_invalid_days.add(local.date())
                reason_counts["invalid_premarket"] += 1
                continue
            if symbol.upper() == "SPX":
                reason_counts["spx_premarket_ignored"] += 1
            elif close_utc in pm_by_day[local.date()]:
                pm_invalid_days.add(local.date())
                reason_counts["duplicate_premarket"] += 1
            else:
                pm_by_day[local.date()][close_utc] = (
                    bar if bar.symbol == symbol else bar.model_copy(update={"symbol": symbol})
                )
            continue
        # Extended hours after the regular close are intentionally ignored but
        # counted so a manifest cannot hide post-market contamination.
        if local.time() > session.close_et.time() and local.time() <= time(20, 0):
            post_ignored += 1
            continue
        if time(4, 0) <= local.time() < time(9, 30):
            if symbol.upper() == "SPY":
                pm_invalid_days.add(local.date())
                reason_counts["off_grid_premarket"] += 1
            else:
                reason_counts["spx_premarket_ignored"] += 1
            continue
        if local.time() >= time(9, 30) and local.time() <= session.close_et.time():
            invalid_days.add(local.date())
            reason_counts["off_grid_rth"] += 1

    if post_ignored:
        reason_counts["post_ignored"] += post_ignored

    complete_days: list[date] = []
    missing_days: list[date] = []
    invalid_target_days: list[date] = []
    complete_stats: dict[date, tuple[float, float, float]] = {}
    pm_available_count = 0
    for day, session in expected_by_day.items():
        expected = set(session.rth_grid)
        observed = set(rth_by_day.get(day, {}))
        if day in invalid_days:
            reason_counts["invalid_rth_session"] += 1
            if target_start <= day < target_end:
                invalid_target_days.append(day)
            continue
        if observed != expected:
            reason_counts["missing_rth_session"] += 1
            if target_start <= day < target_end:
                missing_days.append(day)
            continue
        rows = [rth_by_day[day][stamp] for stamp in session.rth_grid]
        complete_stats[day] = (
            max(row.high for row in rows),
            min(row.low for row in rows),
            rows[-1].close,
        )
        if target_start <= day < target_end:
            complete_days.append(day)

    def context_for(
        day: date,
    ) -> tuple[float | None, float | None, float | None, float | None, str | None]:
        prior = [item.day for item in schedule if item.day < day]
        if not prior or prior[-1] not in complete_stats:
            return None, None, None, None, "previous_session_incomplete"
        previous_day = prior[-1]
        previous = complete_stats[previous_day]
        prior15 = prior[-15:]
        if len(prior15) < 15 or any(item not in complete_stats for item in prior15):
            return previous[0], previous[1], previous[2], None, "atr_warmup_unavailable"
        if symbol.upper() == "SPY" and any(item in ex_div_dates for item in prior15):
            return previous[0], previous[1], previous[2], None, "atr_blocked_ex_dividend"
        trs = [
            _true_range(complete_stats[item], complete_stats[prior15[index - 1]][2])
            for index, item in enumerate(prior15[1:], start=1)
        ]
        # The first of the 14 ATR samples uses the fifteenth prior session's
        # close.  This is exactly 14 completed prior TRs, never the current day.
        if len(trs) != ATR_PERIOD:
            return previous[0], previous[1], previous[2], None, "atr_warmup_unavailable"
        return previous[0], previous[1], previous[2], sum(trs) / ATR_PERIOD, None

    sessions: list[ResearchSession] = []
    for day in target_days:
        if day in invalid_days:
            continue
        session = expected_by_day[day]
        if day not in complete_stats:
            continue
        pm_rows = pm_by_day.get(day, {})
        pm_complete = (
            symbol.upper() == "SPY"
            and day not in pm_invalid_days
            and set(pm_rows) == set(session.pre_grid)
        )
        if pm_complete:
            pm_available_count += 1
        if symbol.upper() == "SPY" and day in ex_div_dates:
            reason_counts["ex_dividend_excluded"] += 1
            continue
        previous_high, previous_low, previous_close, atr, context_reason = context_for(day)
        if context_reason:
            reason_counts[context_reason] += 1
        premarket: tuple[Bar, ...] | None
        if symbol.upper() == "SPX":
            premarket = None
            reason_counts["premarket_not_applicable"] += 1
        elif day not in pm_invalid_days and set(pm_rows) == set(session.pre_grid):
            premarket = tuple(pm_rows[stamp] for stamp in session.pre_grid)
            reason_counts["premarket_complete"] += 1
        else:
            premarket = None
            reason_counts["premarket_incomplete" if pm_rows else "premarket_missing"] += 1
        reason_counts["complete"] += 1
        sessions.append(
            ResearchSession(
                symbol=symbol.upper(),
                day=day,
                bars=tuple(rth_by_day[day][stamp] for stamp in session.rth_grid),
                premarket=premarket,
                previous_high=previous_high,
                previous_low=previous_low,
                previous_close=previous_close,
                atr=atr,
            )
        )

    expected_bars = sum(len(expected_by_day[day].rth_grid) for day in target_days)
    audit = ORBAudit(
        total_expected=len(target_days),
        complete=len(complete_days),
        missing=len(missing_days),
        invalid=len(invalid_target_days),
        pm_available=pm_available_count,
        reason_counts=dict(sorted(reason_counts.items())),
        days=target_days,
        complete_days=tuple(complete_days),
        missing_days=tuple(missing_days),
        invalid_days=tuple(invalid_target_days),
        schedule_version=_schedule_source_version(),
        schedule_sha256=_schedule_digest(schedule),
        symbol=symbol.upper(),
        expected_rth_bars=expected_bars,
        observed_rth_bars=sum(len(rth_by_day.get(day, {})) for day in target_days),
    )
    return tuple(sessions), audit


def load_orb_sessions(
    bars: Sequence[Bar] | Iterable[Bar],
    start: date,
    end: date,
    *,
    symbol: str | None = None,
    ex_div_dates: set[date] | None = None,
    calendar=None,
) -> tuple[tuple[ResearchSession, ...], ORBAudit] | dict[str, tuple[tuple[ResearchSession, ...], ORBAudit]]:
    """Validate and prepare ORB sessions for ``[start, end)``.

    ``bars`` may contain multiple symbols.  Passing ``symbol`` returns one
    ``(sessions, audit)`` pair; omitting it returns one pair per symbol, keyed
    by uppercase symbol.  A 42-calendar-day warmup is fetched from the XNYS
    schedule for prior-session levels and ATR14.  Premarket is strict for SPY
    (04:05 through 09:30 ET); it is always unavailable for SPX.  Post-market
    rows are ignored and counted.  No rows are fabricated or forward-filled.
    """
    if start >= end:
        raise ValueError("start must be before end (end is exclusive)")
    if start < SUPPORTED_START or end > SUPPORTED_END_EXCLUSIVE:
        raise ValueError(
            f"ORB research range must remain within {SUPPORTED_START} <= start < end <= "
            f"{SUPPORTED_END_EXCLUSIVE}"
        )
    ex_div_dates = set(ex_div_dates or ())
    if any(not isinstance(day, date) for day in ex_div_dates):
        raise ValueError("ex_div_dates must contain date values")
    all_bars = tuple(bars)
    groups: dict[str, list[Bar]] = defaultdict(list)
    for row in all_bars:
        groups[row.symbol.upper()].append(row)
    unsupported = sorted(set(groups) - {"SPY", "SPX"})
    if symbol is not None and symbol.upper() not in {"SPY", "SPX"}:
        raise ValueError("ORB research supports only SPY and SPX")
    if unsupported:
        raise ValueError(f"ORB research received unsupported symbols: {unsupported}")
    selected = [symbol.upper()] if symbol else sorted(groups)
    if not selected:
        return {} if symbol is None else ((), _empty_audit())
    warmup_start = start - timedelta(days=WARMUP_CALENDAR_DAYS)
    calendar = calendar or _get_xnys_calendar(warmup_start, end)
    schedule = _schedule_days(calendar, warmup_start, end)
    results = {
        key: _load_one_symbol(
            key, groups.get(key, ()), schedule, start, end,
            ex_div_dates if key == "SPY" else set(),
        )
        for key in selected
    }
    return results[selected[0]] if symbol else results


def _empty_audit() -> ORBAudit:
    return ORBAudit(0, 0, 0, 0, 0, {}, (), schedule_version="XNYS")


# Explicit alias for callers that prefer the research terminology.
load_research_orb_sessions = load_orb_sessions
