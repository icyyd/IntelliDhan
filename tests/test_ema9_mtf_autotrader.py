"""SPY/QQQ multi-timeframe 9EMA SHADOW strategy tests."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from intellidhan_engine.runner import EngineRunner
from intellidhan_engine.strategies import Ema9MtfZeroDte, RawSignal
from intellidhan_engine.veto import Verdict
from intellidhan_gateway.live import LiveLoop
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Direction, Module


ET = ZoneInfo("America/New_York")


def bar(ts, *, open_, high, low, close, symbol="SPY"):
    return Bar(
        symbol=symbol,
        timeframe=Timeframe.M5,
        ts_close=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000_000,
        source="test",
    )


class StrategyState:
    def __init__(self, symbol="SPY", *, opposing_h1=False):
        now = datetime(2026, 7, 20, 10, 35, tzinfo=ET)
        self.symbol = symbol
        self.recent_5m = [
            bar(now - timedelta(minutes=5), open_=100.2, high=100.4, low=99.8, close=100.0,
                symbol=symbol),
            bar(now, open_=100.0, high=101.2, low=99.9, close=101.0, symbol=symbol),
        ]
        self.last_bar = self.recent_5m[-1]
        self._snaps = {
            Timeframe.M5: SimpleNamespace(
                ema9=100.2, ema21=99.7, atr14=0.8, rsi14=62.0,
                vwap=100.1, rel_volume=1.2,
            ),
            Timeframe.M15: SimpleNamespace(ema9=100.0, ema21=99.4),
            Timeframe.H1: SimpleNamespace(ema9=99.5, ema21=98.8),
            Timeframe.D1: SimpleNamespace(ema9=98.0, ema21=96.0),
        }
        self._trends = {
            Timeframe.M5: SimpleNamespace(score=65.0),
            Timeframe.M15: SimpleNamespace(score=50.0),
            Timeframe.H1: SimpleNamespace(score=-30.0 if opposing_h1 else 45.0),
            Timeframe.D1: SimpleNamespace(score=35.0),
        }

    def et_time(self):
        return self.last_bar.ts_close.astimezone(ET).time()

    def indicators(self, timeframe):
        return self._snaps[timeframe]

    def trend_snap(self, timeframe):
        return self._trends[timeframe]


def test_valid_spy_reclaim_is_rich_research_only_signal():
    signal = Ema9MtfZeroDte().evaluate(StrategyState())

    assert signal is not None
    assert signal.direction == Direction.LONG
    assert signal.live_eligible is False
    assert signal.shadow_monitor is True
    assert signal.targets[-1] - signal.entry == pytest.approx(
        3.0 * (signal.entry - signal.stop)
    )
    assert "15m, 1h, and daily" in signal.explain


def test_strategy_rejects_non_allowlisted_or_opposing_higher_timeframe():
    assert Ema9MtfZeroDte().evaluate(StrategyState("AAPL")) is None
    assert Ema9MtfZeroDte().evaluate(StrategyState(opposing_h1=True)) is None


def test_strategy_rejects_late_0dte_entry():
    state = StrategyState()
    late = state.last_bar.model_copy(
        update={"ts_close": datetime(2026, 7, 20, 15, 20, tzinfo=ET)}
    )
    state.recent_5m[-1] = late
    state.last_bar = late

    assert Ema9MtfZeroDte().evaluate(state) is None


def test_runner_routes_non_live_monitor_to_separate_shadow_queue(monkeypatch):
    strategy = Ema9MtfZeroDte()
    runner = EngineRunner(["SPY"], strategies=[strategy])
    state = SimpleNamespace(
        symbol="SPY",
        session_id="2026-07-20",
        ts=lambda: datetime(2026, 7, 20, 10, 35, tzinfo=ET),
        mtf_matrix=lambda: {Timeframe.M5: 60.0, Timeframe.M15: 40.0},
    )
    signal = RawSignal(
        strategy=strategy.key,
        module=Module.ZDTE,
        direction=Direction.LONG,
        trigger_tf=Timeframe.M5,
        entry=100.0,
        stop=99.0,
        targets=[101.0, 102.0, 103.0],
        f2_quality=80.0,
        explain="test",
        invalidation="test",
        live_eligible=False,
        shadow_monitor=True,
    )
    monkeypatch.setattr(
        "intellidhan_engine.runner.score_factors",
        lambda *_args: {"F1_trend": 80.0},
    )
    monkeypatch.setattr("intellidhan_engine.runner.composite", lambda _factors: 80.0)
    monkeypatch.setattr(
        "intellidhan_engine.runner.run_gates",
        lambda *_args: Verdict(True),
    )

    assert runner._score_and_gate(state, signal) is None
    shadow = runner.pop_shadow_setups()
    assert len(shadow) == 1
    assert shadow[0].research_only is True
    assert runner.pop_shadow_setups() == []


@pytest.mark.asyncio
async def test_global_shadow_mode_cannot_deliver_research_strategy(monkeypatch):
    strategy = Ema9MtfZeroDte()
    runner = EngineRunner(["SPY"], strategies=[strategy], shadow=True)
    state = SimpleNamespace(
        symbol="SPY",
        session_id="2026-07-20",
        ts=lambda: datetime(2026, 7, 20, 10, 35, tzinfo=ET),
        mtf_matrix=lambda: {Timeframe.M5: 60.0, Timeframe.M15: 40.0},
    )
    signal = RawSignal(
        strategy=strategy.key, module=Module.ZDTE, direction=Direction.LONG,
        trigger_tf=Timeframe.M5, entry=100.0, stop=99.0,
        targets=[101.0, 102.0, 103.0], f2_quality=80.0,
        explain="test", invalidation="test", live_eligible=False,
        shadow_monitor=True,
    )
    monkeypatch.setattr(
        "intellidhan_engine.runner.score_factors",
        lambda *_args: {"F1_trend": 80.0},
    )
    monkeypatch.setattr("intellidhan_engine.runner.composite", lambda _factors: 80.0)
    monkeypatch.setattr(
        "intellidhan_engine.runner.run_gates", lambda *_args: Verdict(True)
    )
    setup = runner._score_and_gate(state, signal)
    assert setup is not None and setup.research_only is True

    loop = LiveLoop(symbols=["SPY"])
    delivered = []

    async def capture(alert):
        delivered.append(alert)

    monkeypatch.setattr(loop, "_deliver", capture)
    await loop._record_setup(setup, replay=False, deliver=True)

    assert delivered == []
    assert loop.alerts[-1].status == "SHADOW"
    assert loop.alerts[-1].research_only is True
