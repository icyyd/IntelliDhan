"""Market clock — the single authority for session state (doc 01 §2①).

Every scheduler and analytics node keys off this service; no other code may
reason about holidays, half-days, or DST. Deterministic: callers inject `now`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from intellidhan_schemas import SessionState, Timeframe

ET = ZoneInfo("America/New_York")

# NYSE full-closure holidays. Extend annually; test_market_clock guards the horizon.
_HOLIDAYS: frozenset[date] = frozenset(
    [
        # 2026
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
        # 2027
        date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
        date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
        date(2027, 11, 25), date(2027, 12, 24),
    ]
)

# 13:00 ET early closes.
_HALF_DAYS: frozenset[date] = frozenset(
    [
        date(2026, 11, 27), date(2026, 12, 24),
        date(2027, 11, 26),
    ]
)

_CLOCK_HORIZON = date(2027, 12, 31)

PRE_OPEN = time(4, 0)
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
HALF_DAY_CLOSE = time(13, 0)
POST_CLOSE = time(20, 0)


class MarketClock:
    """Session-state oracle for US equities/ETF/index-options trading days."""

    def is_trading_day(self, d: date) -> bool:
        if d > _CLOCK_HORIZON:
            raise ValueError(f"market calendar not populated beyond {_CLOCK_HORIZON}: {d}")
        return d.weekday() < 5 and d not in _HOLIDAYS

    def is_half_day(self, d: date) -> bool:
        return d in _HALF_DAYS

    def rth_close(self, d: date) -> time:
        return HALF_DAY_CLOSE if self.is_half_day(d) else RTH_CLOSE

    def option_expiry_close(self, expiry: str) -> datetime | None:
        """Conservative regular-session option cutoff; unknown calendars fail closed."""
        try:
            day = date.fromisoformat(expiry)
            if not self.is_trading_day(day):
                return None
        except (TypeError, ValueError):
            return None
        return datetime.combine(day, self.rth_close(day), tzinfo=ET)

    def session_state(self, now: datetime) -> SessionState:
        if now.tzinfo is None:
            raise ValueError("naive datetime rejected")
        local = now.astimezone(ET)
        d, t = local.date(), local.time()
        if not self.is_trading_day(d):
            return SessionState.CLOSED
        close = self.rth_close(d)
        if PRE_OPEN <= t < RTH_OPEN:
            return SessionState.PRE
        if RTH_OPEN <= t < close:
            return SessionState.RTH
        # No post-market session after an early close.
        if not self.is_half_day(d) and close <= t < POST_CLOSE:
            return SessionState.POST
        return SessionState.CLOSED

    def next_rth_open(self, now: datetime) -> datetime:
        """Next RTH open strictly after `now` (used by schedulers and the briefing job)."""
        local = now.astimezone(ET)
        d = local.date()
        if self.is_trading_day(d) and local.time() < RTH_OPEN:
            return datetime.combine(d, RTH_OPEN, tzinfo=ET)
        d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return datetime.combine(d, RTH_OPEN, tzinfo=ET)

    def session_id(self, now: datetime) -> str:
        """Stable per-trading-day key; VWAP and profile builders reset on change."""
        return now.astimezone(ET).date().isoformat()

    def latest_completed_bar_close(
        self, now: datetime, timeframe: Timeframe
    ) -> datetime:
        """Latest regular-session bar close at or before ``now``.

        The prior session close remains the boundary through the overnight,
        weekend, and first minutes after the next open. Feed callers apply
        their own publication grace before calling so a just-closed candle is
        not treated as late while the provider is still finalizing it.
        """
        if now.tzinfo is None:
            raise ValueError("naive datetime rejected")
        if timeframe.seconds >= Timeframe.D1.seconds:
            raise ValueError("completed intraday boundary requires an intraday timeframe")
        local = now.astimezone(ET)
        if self.is_trading_day(local.date()):
            opened = datetime.combine(local.date(), RTH_OPEN, tzinfo=ET)
            closed = datetime.combine(
                local.date(), self.rth_close(local.date()), tzinfo=ET
            )
            if opened <= local < closed:
                completed = int((local - opened).total_seconds() // timeframe.seconds)
                if completed >= 1:
                    return opened + timedelta(seconds=completed * timeframe.seconds)
        return self.latest_completed_session_close(local)

    def latest_completed_session_close(self, now: datetime) -> datetime:
        """Most recent full regular-session close at or before ``now``."""
        if now.tzinfo is None:
            raise ValueError("naive datetime rejected")
        local = now.astimezone(ET)
        day = local.date()
        if self.is_trading_day(day):
            closed = datetime.combine(day, self.rth_close(day), tzinfo=ET)
            if local >= closed:
                return closed
        day -= timedelta(days=1)
        while not self.is_trading_day(day):
            day -= timedelta(days=1)
        return datetime.combine(day, self.rth_close(day), tzinfo=ET)
