from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from intellidhan_ingestor.market_clock import ET, MarketClock
from intellidhan_schemas import SessionState

clock = MarketClock()


def et(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_session_states_regular_day():
    # Friday 2026-07-10 is a regular trading day
    assert clock.session_state(et(2026, 7, 10, 3, 59)) == SessionState.CLOSED
    assert clock.session_state(et(2026, 7, 10, 4, 0)) == SessionState.PRE
    assert clock.session_state(et(2026, 7, 10, 9, 29)) == SessionState.PRE
    assert clock.session_state(et(2026, 7, 10, 9, 30)) == SessionState.RTH
    assert clock.session_state(et(2026, 7, 10, 15, 59)) == SessionState.RTH
    assert clock.session_state(et(2026, 7, 10, 16, 0)) == SessionState.POST
    assert clock.session_state(et(2026, 7, 10, 20, 0)) == SessionState.CLOSED


def test_weekend_and_holiday_closed():
    assert clock.session_state(et(2026, 7, 11, 12, 0)) == SessionState.CLOSED  # Saturday
    assert clock.session_state(et(2026, 7, 3, 12, 0)) == SessionState.CLOSED   # July 4th observed
    assert not clock.is_trading_day(date(2026, 11, 26))                         # Thanksgiving


def test_half_day_closes_at_1pm_with_no_post():
    d = et(2026, 11, 27, 12, 59)  # day after Thanksgiving
    assert clock.session_state(d) == SessionState.RTH
    assert clock.session_state(et(2026, 11, 27, 13, 0)) == SessionState.CLOSED
    assert clock.session_state(et(2026, 11, 27, 14, 0)) == SessionState.CLOSED


def test_dst_handling_via_utc():
    # 14:30 UTC is 9:30 ET during DST but 10:30 ET after fall-back (Nov 2 2026)
    utc = ZoneInfo("UTC")
    assert clock.session_state(datetime(2026, 7, 10, 13, 30, tzinfo=utc)) == SessionState.RTH
    assert clock.session_state(datetime(2026, 12, 11, 13, 30, tzinfo=utc)) == SessionState.PRE


def test_next_rth_open_skips_weekend():
    nxt = clock.next_rth_open(et(2026, 7, 10, 17, 0))  # Friday evening
    assert nxt == et(2026, 7, 13, 9, 30)               # Monday


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        clock.session_state(datetime(2026, 7, 10, 12, 0))


def test_horizon_guard():
    with pytest.raises(ValueError):
        clock.is_trading_day(date(2028, 1, 3))
