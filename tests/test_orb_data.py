from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from intellidhan_schemas import Bar, Timeframe
from intellidhan_learning.orb_data import (
    ET,
    _get_xnys_calendar,
    _schedule_days,
    load_orb_sessions,
)


def _calendar(start: date, end: date, *, early: set[date] = set(), closed: set[date] = set()):
    rows = []
    day = start
    while day < end:
        if day.weekday() < 5 and day not in closed:
            close = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(
                hour=13 if day in early else 16, minute=0
            )
            opened = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(
                hour=9, minute=30
            )
            rows.append((day, opened.astimezone(timezone.utc), close.astimezone(timezone.utc)))
        day += timedelta(days=1)
    frame = pd.DataFrame(rows, columns=["day", "open", "close"]).set_index("day")
    return SimpleNamespace(schedule=frame)


def _bar(symbol: str, stamp: datetime, price: float = 100.0, volume: float = 1_000.0):
    return Bar(
        symbol=symbol,
        timeframe=Timeframe.M5,
        ts_close=stamp.astimezone(timezone.utc),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price + 0.5,
        volume=volume,
        source="test",
    )


def _session_bars(symbol: str, day: date, close_hour: int = 16, *, premarket: bool = False):
    rows = []
    if premarket:
        stamp = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=4, minute=5)
        for _ in range(66):
            rows.append(_bar(symbol, stamp))
            stamp += timedelta(minutes=5)
    stamp = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=9, minute=35)
    end = datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=close_hour, minute=0)
    while stamp <= end:
        rows.append(_bar(symbol, stamp))
        stamp += timedelta(minutes=5)
    return rows


def _history(symbol: str, start: date, end: date, *, early: set[date] = set(), closed: set[date] = set(), premarket: bool = False):
    rows = []
    day = start
    while day < end:
        if day.weekday() < 5 and day not in closed:
            rows.extend(_session_bars(symbol, day, 13 if day in early else 16, premarket=premarket))
        day += timedelta(days=1)
    return rows


def test_dst_and_early_close_grid_is_exchange_local():
    target = date(2024, 11, 29)
    bars = _history("SPY", date(2024, 10, 1), date(2024, 12, 2), early={target}, premarket=True)
    sessions, audit = load_orb_sessions(
        bars, date(2024, 11, 4), date(2024, 12, 2),
        symbol="SPY", calendar=_calendar(date(2024, 8, 1), date(2024, 12, 3), early={target}),
    )
    row = next(item for item in sessions if item.day == target)
    assert len(row.bars) == 42
    assert row.bars[0].ts_close.astimezone(ET).strftime("%H:%M") == "09:35"
    assert row.bars[-1].ts_close.astimezone(ET).strftime("%H:%M") == "13:00"
    assert row.premarket is not None and len(row.premarket) == 66
    assert audit.complete == len(sessions)


def test_holiday_is_not_an_expected_session():
    holiday = date(2024, 11, 28)
    bars = _history("SPY", date(2024, 11, 1), date(2024, 12, 2), closed={holiday})
    sessions, audit = load_orb_sessions(
        bars, date(2024, 11, 25), date(2024, 12, 2), symbol="SPY",
        calendar=_calendar(date(2024, 10, 1), date(2024, 12, 3), closed={holiday}),
    )
    assert holiday not in audit.days
    assert all(item.day != holiday for item in sessions)


def test_missing_rth_session_is_not_filled():
    target = date(2024, 10, 15)
    bars = _history("SPY", date(2024, 9, 1), date(2024, 10, 20))
    bars = [row for row in bars if row.ts_close.astimezone(ET).date() != target]
    _sessions, audit = load_orb_sessions(
        bars, date(2024, 10, 1), date(2024, 10, 20), symbol="SPY",
        calendar=_calendar(date(2024, 7, 1), date(2024, 10, 21)),
    )
    assert target in audit.missing_days
    assert audit.missing >= 1


def test_duplicate_rth_row_invalidates_session():
    target = date(2024, 10, 15)
    bars = _history("SPY", date(2024, 9, 1), date(2024, 10, 20))
    duplicate = next(row for row in bars if row.ts_close.astimezone(ET).date() == target)
    bars.append(duplicate)
    sessions, audit = load_orb_sessions(
        bars, date(2024, 10, 1), date(2024, 10, 20), symbol="SPY",
        calendar=_calendar(date(2024, 7, 1), date(2024, 10, 21)),
    )
    assert target in audit.invalid_days
    assert all(item.day != target for item in sessions)


def test_real_xnys_calendar_holidays_early_closes_and_dst():
    calendar = _get_xnys_calendar(date(2024, 3, 7), date(2025, 7, 7))
    schedule = {row.day: row for row in _schedule_days(calendar, date(2024, 3, 7), date(2025, 7, 7))}
    assert date(2024, 11, 28) not in schedule
    assert date(2025, 1, 9) not in schedule  # Carter funeral closure
    assert len(schedule[date(2024, 11, 29)].rth_grid) == 42
    assert len(schedule[date(2025, 7, 3)].rth_grid) == 42
    assert schedule[date(2024, 3, 8)].rth_grid[0].isoformat() == "2024-03-08T14:35:00+00:00"
    assert schedule[date(2024, 3, 11)].rth_grid[0].isoformat() == "2024-03-11T13:35:00+00:00"


def test_spx_never_uses_premarket_and_groups_symbols_separately():
    start, end = date(2024, 10, 1), date(2024, 10, 10)
    bars = _history("SPY", date(2024, 8, 1), end, premarket=True)
    bars += _history("SPX", date(2024, 8, 1), end, premarket=True)
    result = load_orb_sessions(
        bars, start, end,
        calendar=_calendar(date(2024, 6, 1), end),
    )
    assert set(result) == {"SPY", "SPX"}
    assert all(row.premarket is not None for row in result["SPY"][0])
    assert all(row.premarket is None for row in result["SPX"][0])
    assert result["SPX"][1].pm_available == 0


def test_premarket_off_grid_or_duplicate_suppresses_only_premarket():
    target = date(2024, 10, 8)
    bars = _history("SPY", date(2024, 9, 1), date(2024, 10, 15), premarket=True)
    pm = next(row for row in bars if row.ts_close.astimezone(ET).date() == target and row.ts_close.astimezone(ET).time().strftime("%H:%M") == "08:00")
    bars.append(pm.model_copy(update={"ts_close": pm.ts_close + timedelta(minutes=1)}))
    duplicate = next(row for row in bars if row.ts_close.astimezone(ET).date() == target and row.ts_close.astimezone(ET).time().strftime("%H:%M") == "08:05")
    bars.append(duplicate)
    sessions, audit = load_orb_sessions(
        bars, date(2024, 10, 1), date(2024, 10, 15), symbol="SPY",
        calendar=_calendar(date(2024, 7, 1), date(2024, 10, 16)),
    )
    session = next(row for row in sessions if row.day == target)
    assert session.premarket is None
    assert target not in audit.invalid_days
    assert audit.reason_counts["off_grid_premarket"] == 1
    assert audit.reason_counts["duplicate_premarket"] == 1


def test_ex_dividend_session_does_not_reduce_raw_premarket_coverage():
    event = date(2024, 10, 1)
    start, end = date(2024, 9, 20), date(2024, 10, 10)
    calendar = _calendar(date(2024, 7, 1), end)
    bars = _history("SPY", date(2024, 7, 1), end, premarket=True)
    sessions, audit = load_orb_sessions(
        bars, start, end, symbol="SPY", ex_div_dates={event}, calendar=calendar
    )
    assert audit.pm_available == audit.total_expected
    assert event not in {row.day for row in sessions}


def test_atr_uses_only_prior_sessions_and_future_bars_cannot_change_it():
    start, end = date(2024, 10, 1), date(2024, 10, 10)
    calendar = _calendar(date(2024, 7, 1), end)
    bars = _history("SPY", date(2024, 7, 1), end)
    sessions_a, _ = load_orb_sessions(bars, start, end, symbol="SPY", calendar=calendar)
    current = next(row for row in sessions_a if row.day == date(2024, 10, 2))
    future = _bar("SPY", datetime(2024, 10, 9, 15, 55, tzinfo=ET), price=10_000)
    sessions_b, _ = load_orb_sessions(bars + [future], start, end, symbol="SPY", calendar=calendar)
    current_b = next(row for row in sessions_b if row.day == date(2024, 10, 2))
    assert current.atr == current_b.atr


def test_ex_dividend_event_is_excluded_and_blocks_atr_context():
    event = date(2024, 10, 1)
    start, end = date(2024, 9, 20), date(2024, 10, 10)
    calendar = _calendar(date(2024, 7, 1), end)
    bars = _history("SPY", date(2024, 7, 1), end)
    sessions, audit = load_orb_sessions(
        bars, start, end, symbol="SPY", ex_div_dates={event}, calendar=calendar
    )
    assert event not in {row.day for row in sessions}
    after = next(row for row in sessions if row.day == date(2024, 10, 2))
    assert after.atr is None
    assert audit.reason_counts["ex_dividend_excluded"] == 1
    assert audit.reason_counts["atr_blocked_ex_dividend"] >= 1


def test_out_of_supported_range_fails_closed():
    with pytest.raises(ValueError):
        load_orb_sessions([], date(2023, 10, 5), date(2023, 10, 10), calendar=_calendar(date(2023, 8, 1), date(2023, 10, 11)))
    with pytest.raises(ValueError):
        load_orb_sessions([], date(2026, 10, 5), date(2026, 10, 7), calendar=_calendar(date(2026, 8, 1), date(2026, 10, 8)))


def test_injected_calendar_truncation_fails_closed():
    with pytest.raises(ValueError, match="calendar does not cover"):
        load_orb_sessions(
            [], date(2024, 10, 1), date(2024, 10, 10), symbol="SPY",
            calendar=_calendar(date(2024, 10, 1), date(2024, 10, 10)),
        )


def test_unsupported_symbol_fails_closed():
    with pytest.raises(ValueError, match="only SPY and SPX"):
        load_orb_sessions([], date(2024, 10, 1), date(2024, 10, 3), symbol="QQQ")
