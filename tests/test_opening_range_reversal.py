import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import intellidhan_learning.opening_range_reversal as orr
from intellidhan_learning.opening_range_reversal import (
    OpeningRangeReversalConfig,
    ORR_PROFILES,
    _daily_context,
    _rth_5m,
    _split_by_session,
    backtest_opening_range_reversal,
    performance_report,
)
from intellidhan_schemas import Bar, Timeframe


ET = ZoneInfo("America/New_York")


def daily_bars(symbol: str = "SPY"):
    rows = []
    start = datetime(2025, 1, 2, 16, 0, tzinfo=ET)
    for index in range(45):
        close = 100.0 + (index % 2) * 0.25
        rows.append(
            Bar(
                symbol=symbol,
                timeframe=Timeframe.D1,
                ts_close=(start + timedelta(days=index)).astimezone(timezone.utc),
                open=close,
                high=close + 2.0,
                low=close - 2.0,
                close=close,
                volume=1_000_000,
                source="test",
            )
        )
    return rows


def session_bars(*, narrow_range: bool = False, symbol: str = "SPY"):
    day = datetime(2025, 2, 3, 9, 30, tzinfo=ET)
    rows = []
    for index in range(78):
        opened = day + timedelta(minutes=5 * index)
        close_ts = (opened + timedelta(minutes=5)).astimezone(timezone.utc)
        price = 100.0
        opened_price = price
        high = price + 0.2
        low = price - 0.2
        close = price
        if index == 0:
            opened_price, high, low, close = 100.0, 102.0, 99.0, 101.5
        elif index == 1:
            opened_price, high, low, close = 101.5, 103.0, 99.5, 102.5
        elif index == 2:
            opened_price, high, low, close = 102.5, 104.0, 100.0, 103.0
        elif not narrow_range and index == 3:
            opened_price, high, low, close = 103.0, 103.2, 99.5, 100.0
        elif not narrow_range and index == 4:
            opened_price, high, low, close = 100.0, 100.1, 98.5, 98.8
        rows.append(
            Bar(
                symbol=symbol,
                timeframe=Timeframe.M5,
                ts_close=close_ts,
                open=opened_price,
                high=high,
                low=low,
                close=close,
                volume=100_000,
                source="test",
            )
        )
    return rows


def test_daily_context_uses_prior_completed_bar_only():
    context = _daily_context(daily_bars(), 14)
    current = context["2025-01-21"]
    assert current[0] is not None
    assert current[1] == 102.0
    assert current[2] == 98.0


def test_opening_range_reversal_requires_manipulation_gate_and_next_bar_break():
    trades = backtest_opening_range_reversal(session_bars(), daily_bars())
    assert len(trades) == 1
    trade = trades[0]
    assert trade.direction == "SHORT"
    assert trade.initial_push == "UP"
    assert trade.signal_ts.astimezone(ET).time().hour == 9
    assert trade.signal_ts.astimezone(ET).time().minute == 50
    assert trade.entry_ts.astimezone(ET).time().minute == 50
    assert trade.opening_range_atr_ratio >= 0.20


def test_narrow_opening_range_is_suppressed():
    config = OpeningRangeReversalConfig(manipulation_fraction=0.20)
    assert (
        backtest_opening_range_reversal(session_bars(narrow_range=True), daily_bars(), config) == []
    )


def test_missing_or_duplicate_five_minute_rows_reject_the_session():
    rows = session_bars()
    rows.pop(5)
    assert backtest_opening_range_reversal(rows, daily_bars()) == []

    rows = session_bars()
    rows.insert(5, rows[5])
    assert backtest_opening_range_reversal(rows, daily_bars()) == []


def test_target_two_ledger_exit_includes_adverse_slippage():
    rows = session_bars()
    for index in range(5, len(rows)):
        rows[index] = rows[index].model_copy(
            update={"open": 95.0, "high": 96.0, "low": 94.0, "close": 94.5}
        )
    trade = backtest_opening_range_reversal(
        rows, daily_bars(), OpeningRangeReversalConfig(runner_target_r=1.0)
    )[0]
    assert trade.exit_reason == "TARGET_TWO"
    assert trade.exit > trade.target_two


def test_cli_daily_context_uses_raw_basis_aligned_with_intraday(monkeypatch):
    calls = []

    class FakeProvider:
        async def get_bars(self, symbol, timeframe, start, end, *, adjusted=False):
            calls.append((symbol, timeframe, adjusted))
            return []

    monkeypatch.setattr(orr, "YahooProvider", FakeProvider)
    asyncio.run(orr._fetch(["SPY"], 2))
    assert calls == [("SPY", Timeframe.M5, False), ("SPY", Timeframe.D1, False)]


def test_report_is_cost_aware_and_tracks_partial_target():
    trades = backtest_opening_range_reversal(session_bars(), daily_bars())
    report = performance_report(trades)
    assert report["trades"] == 1
    assert report["target_one_rate"] == 1.0
    assert report["by_symbol"]["SPY"]["trades"] == 1
    trade = trades[0]
    assert trade.partial_exit is not None
    assert trade.partial_exit_ts is not None


def _bar_at(local: datetime) -> Bar:
    return Bar(
        symbol="SPY",
        timeframe=Timeframe.M5,
        ts_close=local.astimezone(timezone.utc),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=100_000,
        source="test",
    )


def test_market_clock_excludes_holidays_and_respects_half_day_close():
    assert not _rth_5m(_bar_at(datetime(2026, 7, 3, 10, 0, tzinfo=ET)))
    assert _rth_5m(_bar_at(datetime(2026, 11, 27, 13, 0, tzinfo=ET)))
    assert not _rth_5m(_bar_at(datetime(2026, 11, 27, 13, 5, tzinfo=ET)))


def test_walk_forward_split_uses_fixed_sessions_including_no_trade_days():
    dates = [f"2026-07-{day:02d}" for day in range(1, 11)]
    trades = [SimpleNamespace(session_date=dates[index]) for index in (0, 6, 8)]
    splits = _split_by_session(trades, dates)
    assert [trade.session_date for trade in splits["train"]] == [dates[0]]
    assert [trade.session_date for trade in splits["validation"]] == [dates[6]]
    assert [trade.session_date for trade in splits["test"]] == [dates[8]]


def test_shadow_candidate_profile_is_explicit_and_not_the_control():
    control = ORR_PROFILES["control"]
    candidate = ORR_PROFILES["shadow_candidate"]
    assert control != candidate
    assert candidate.manipulation_fraction == 0.25
    assert candidate.min_reversal_body_fraction == 0.25
    assert candidate.entry_cutoff_et == "10:30"
    assert candidate.prior_level_filter is False
