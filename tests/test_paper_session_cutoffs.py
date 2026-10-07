"""Intraday research exits must use the actual session without invented fills."""

from datetime import datetime, timedelta

import pytest

from intellidhan_engine.composer import Budgets, Composer
from intellidhan_ingestor.market_clock import ET
from intellidhan_learning.paper import Outcome, PaperExecutor, PaperTrade, performance_report
from intellidhan_learning.research import stats as research_stats
from intellidhan_learning.research_0dte import stats as research_0dte_stats
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Direction, Module, Setup


def stamp(day, hour, minute=0):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=ET)


def trade(day="2026-07-10", *, pending=False, module=Module.ZDTE):
    created = stamp(day, 10)
    return PaperTrade(
        alert_id="test_intraday", symbol="SPY", module=module, strategy="TEST",
        direction=Direction.LONG, confidence=0.74, created_at=created,
        entry=100, initial_stop=98, targets=[105, 110, 115],
        valid_until=created + timedelta(minutes=10),
        filled_at=None if pending else created + timedelta(minutes=5),
        outcome=Outcome.PENDING if pending else Outcome.OPEN,
    )


def bar(at, *, low=100.2, high=101.2, close=101):
    return Bar(
        symbol="SPY", timeframe=Timeframe.M5, ts_close=at,
        open=close, low=low, high=high, close=close, volume=1000, source="fixture",
    )


@pytest.mark.parametrize(("day", "hour"), [("2026-07-10", 15), ("2026-11-27", 12)])
def test_open_scalp_flattens_five_minutes_before_actual_close(day, hour):
    executor = PaperExecutor()
    position = trade(day)
    executor.track(position)
    assert executor.on_bar(bar(stamp(day, hour, 50))) == []
    result = executor.on_bar(bar(stamp(day, hour, 55)))
    assert result == [position]
    assert position.outcome == Outcome.FLATTENED_TIME
    assert position.exit_ts == stamp(day, hour, 55)
    assert position.realized_r == 0.5
    assert executor.active_trades() == []


def test_cutoff_stop_still_precedes_targets_and_timed_flatten():
    executor = PaperExecutor()
    position = trade("2026-11-27")
    executor.track(position)
    executor.on_bar(bar(stamp("2026-11-27", 12, 55), low=97, high=116))
    assert position.outcome == Outcome.STOPPED
    assert position.realized_r == -1


def test_cutoff_preserves_already_realized_tranches():
    executor = PaperExecutor()
    position = trade("2026-11-27")
    position.tranches_exited = 1
    executor.track(position)
    executor.on_bar(bar(stamp("2026-11-27", 12, 55), low=102.5, high=103.5, close=103))
    assert position.outcome == Outcome.FLATTENED_TIME
    assert position.realized_r == pytest.approx(0.33 * 2.5 + 0.67 * 1.5)


@pytest.mark.parametrize("minute", [55, 59])
@pytest.mark.parametrize(("day", "hour"), [("2026-07-10", 15), ("2026-11-27", 12)])
def test_pending_scalp_cannot_fill_at_or_after_exit_cutoff(day, hour, minute):
    executor = PaperExecutor()
    position = trade(day, pending=True)
    position.created_at = stamp(day, hour, 50)
    position.valid_until = stamp(day, hour, 59) + timedelta(minutes=30)
    executor.track(position)
    executor.on_bar(bar(stamp(day, hour, minute), low=99.5))
    assert position.outcome == Outcome.EXPIRED_UNFILLED
    assert position.filled_at is None
    assert position.realized_r is None
    assert "Entry window ended" in position.resolution_note


def test_pending_scalp_can_fill_before_cutoff_then_flatten_on_half_day():
    executor = PaperExecutor()
    position = trade("2026-11-27", pending=True)
    position.created_at = stamp("2026-11-27", 12, 40)
    position.valid_until = stamp("2026-11-27", 12, 55)
    executor.track(position)
    assert executor.on_bar(bar(stamp("2026-11-27", 12, 50), low=99.5)) == []
    assert position.outcome == Outcome.OPEN
    assert position.filled_at == stamp("2026-11-27", 12, 50)
    executor.on_bar(bar(stamp("2026-11-27", 12, 55)))
    assert position.outcome == Outcome.FLATTENED_TIME
    assert position.realized_r == 0.5


def test_bar_ending_at_market_open_cannot_fill_from_premarket_prices():
    executor = PaperExecutor()
    position = trade(pending=True)
    position.created_at = stamp("2026-07-10", 9, 20)
    position.valid_until = stamp("2026-07-10", 9, 40)
    executor.track(position)
    executor.on_bar(bar(stamp("2026-07-10", 9, 30), low=99.5))
    assert position.outcome == Outcome.PENDING
    executor.on_bar(bar(stamp("2026-07-10", 9, 35), low=99.5))
    assert position.outcome == Outcome.OPEN
    assert position.filled_at == stamp("2026-07-10", 9, 35)


@pytest.mark.parametrize("observed", [
    stamp("2026-11-27", 13), stamp("2026-11-27", 14), stamp("2026-11-30", 9, 35),
])
def test_missing_cutoff_does_not_invent_exit_using_late_or_next_session_price(observed):
    executor = PaperExecutor()
    position = trade("2026-11-27")
    executor.track(position)
    # Even a later bar touching every target must not manufacture a win.
    result = executor.on_bar(bar(observed, low=110, high=125, close=120))
    assert result == [position]
    assert position.outcome == Outcome.UNRESOLVED_DATA
    assert position.exit_ts is None
    assert position.realized_r is None
    assert position.tranches_exited == 0
    assert "Missing exit observation" in position.resolution_note
    assert executor.active_trades() == []


@pytest.mark.parametrize("day", ["2026-11-26", "2028-01-03"])
@pytest.mark.parametrize("pending", [False, True])
def test_holiday_or_unknown_calendar_cannot_generate_simulated_returns(day, pending):
    executor = PaperExecutor()
    position = trade(day, pending=pending)
    executor.track(position)
    executor.on_bar(bar(stamp(day, 10, 10), low=99.5, high=125, close=120))
    assert position.outcome == (Outcome.EXPIRED_UNFILLED if pending else Outcome.UNRESOLVED_DATA)
    assert position.realized_r is None
    assert "calendar is unavailable" in position.resolution_note


@pytest.mark.parametrize("day", ["2026-11-27", "2028-01-03"])
def test_swing_is_unaffected_by_intraday_cutoff_and_calendar_horizon(day):
    executor = PaperExecutor()
    position = trade(day, module=Module.SWING)
    executor.track(position)
    assert executor.on_bar(bar(stamp(day, 13))) == []
    assert position.outcome == Outcome.OPEN
    assert position.realized_r is None


def test_unresolved_research_is_counted_but_excluded_from_performance_statistics():
    executor = PaperExecutor()
    missing = trade("2026-11-27")
    executor.track(missing)
    executor.on_bar(bar(stamp("2026-11-27", 13)))
    assert performance_report([missing]) == {"decided": 0, "unresolved_data": 1}
    winner = trade()
    winner.outcome = Outcome.TP_FULL
    winner.realized_r = 2
    winner.tranches_exited = 3
    loser = trade()
    loser.outcome = Outcome.STOPPED
    loser.realized_r = -1
    report = performance_report([winner, loser, missing])
    assert report["decided"] == 2
    assert report["unresolved_data"] == 1
    assert report["win_rate"] == 0.5
    assert report["profit_factor"] == 2
    assert report["avg_r"] == 0.5
    assert research_stats([winner, loser, missing])["n"] == 2
    assert research_0dte_stats([winner, loser, missing])["n"] == 2


def test_missing_cutoff_with_partial_profit_is_not_counted_as_a_completed_win():
    executor = PaperExecutor()
    position = trade("2026-11-27")
    position.tranches_exited = 1
    executor.track(position)
    executor.on_bar(bar(stamp("2026-11-27", 13)))
    assert position.outcome == Outcome.UNRESOLVED_DATA
    assert position.tranches_exited == 1
    assert position.realized_r is None
    assert research_stats([position]) == {"n": 0}
    assert research_0dte_stats([position]) == {"n": 0}
    restored = PaperExecutor()
    restored.restore([PaperTrade.model_validate(position.model_dump(mode="json"))])
    assert restored.active_trades() == []
    assert restored.on_bar(bar(stamp("2026-11-30", 10))) == []


def setup(at, module=Module.ZDTE):
    return Setup(
        setup_id="test_cutoff", symbol="SPY", strategy="TEST", module=module,
        direction=Direction.LONG, trigger_tf=Timeframe.M5, ts=at,
        mtf_matrix={"5m": 70}, factors={"F1_trend": 70}, composite=80, confidence=0.74,
        entry_underlying=100, stop_underlying=98, targets_underlying=[105, 110, 115],
        reward_risk=2, explain="Confirmed breakout.", invalidation="Close beyond the stop.",
    )


@pytest.mark.parametrize(("day", "hour"), [("2026-07-10", 15), ("2026-11-27", 12)])
def test_printed_flatten_time_and_entry_validity_match_executor_cutoff(day, hour):
    composer = Composer(Budgets())
    alert = composer.compose(setup(stamp(day, hour, 50)))
    assert alert.valid_until == stamp(day, hour, 55)
    assert f"Hard flatten by {hour}:55 ET" in alert.management
    assert composer.compose(setup(stamp(day, hour, 55))) is None
    assert composer.compose(setup(stamp(day, hour + 1))) is None


@pytest.mark.parametrize("day", ["2026-11-26", "2028-01-03"])
def test_composer_suppresses_intraday_plan_when_session_cannot_be_validated(day):
    composer = Composer(Budgets())
    assert composer.compose(setup(stamp(day, 10))) is None
    swing = composer.compose(setup(stamp(day, 10), module=Module.SWING))
    assert swing is not None
    assert not any("Hard flatten" in line for line in swing.management)
