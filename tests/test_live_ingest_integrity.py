"""Regression tests for the three confirmed live-integrity findings:

1. (critical) forming-bar ingestion — the provider must never emit the
   still-in-progress intraday candle, and the live loop must never consume a
   bar whose close time is in the future (partial OHLCV poisons EMAs/VWAP/ORB
   and the seen_bars dedupe then blocks the real completed bar forever).
2. (high) paper fill-bar stop exemption — a bar that fills the entry limit
   and also trades through the stop is a certain same-bar stop-out; exempting
   the fill bar from the stop check inflated measured win rates.
3. (high) boot replay re-delivery — historical warm-up bars must never
   re-send Telegram alerts/settlements or create autotrade intents.
"""

from datetime import datetime, timedelta, timezone

import pytest

from intellidhan_gateway.live import LiveLoop
from intellidhan_learning.paper import Outcome, PaperExecutor, PaperTrade
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Direction, Module


def bar5(ts, o, h, lo, c, sym="QQQ", v=1e6):
    return Bar(symbol=sym, timeframe=Timeframe.M5, ts_close=ts, open=o, high=h,
               low=lo, close=c, volume=v, source="fx")


def make_trade(entry=100.0, stop=99.0, valid_minutes=60) -> PaperTrade:
    now = datetime.now(timezone.utc)
    return PaperTrade(
        alert_id="alr_t", symbol="QQQ", module=Module.ZDTE, strategy="T",
        direction=Direction.LONG, confidence=0.8, entry=entry, initial_stop=stop,
        targets=[entry + 1, entry + 2, entry + 3],
        valid_until=now + timedelta(minutes=valid_minutes),
    )


# ---------- finding 2: fill-bar stop check ----------

def test_fill_bar_that_traverses_stop_settles_stopped_at_minus_1r():
    ex = PaperExecutor()
    t = make_trade(entry=100.0, stop=99.0)
    ex.track(t)
    now = datetime.now(timezone.utc)
    # one bar spans entry AND stop: limit fill is certain, so is the stop-out
    settled = ex.on_bar(bar5(now, 100.6, 100.8, 98.5, 98.9))
    assert settled == [t]
    assert t.outcome == Outcome.STOPPED
    assert t.realized_r == -1.0
    assert t.tranches_exited == 0


def test_fill_bar_not_touching_stop_stays_open_without_target_credit():
    ex = PaperExecutor()
    t = make_trade(entry=100.0, stop=99.0)
    ex.track(t)
    now = datetime.now(timezone.utc)
    # fills the limit, stays above the stop, even tags T1 high — no credit yet
    settled = ex.on_bar(bar5(now, 100.6, 101.2, 99.8, 100.9))
    assert settled == []
    assert t.outcome == Outcome.OPEN
    assert t.tranches_exited == 0  # targets stay pessimistically uncredited


def test_short_fill_bar_traversing_stop_settles_stopped():
    ex = PaperExecutor()
    now = datetime.now(timezone.utc)
    t = PaperTrade(
        alert_id="alr_s", symbol="QQQ", module=Module.ZDTE, strategy="T",
        direction=Direction.SHORT, confidence=0.8, entry=100.0, initial_stop=101.0,
        targets=[99.0, 98.0, 97.0], valid_until=now + timedelta(minutes=60),
    )
    ex.track(t)
    settled = ex.on_bar(bar5(now, 99.5, 101.3, 99.4, 101.1))
    assert settled == [t]
    assert t.outcome == Outcome.STOPPED
    assert t.realized_r == -1.0


# ---------- findings 1 + 3: live ingest integrity ----------

class FakeProvider:
    """Returns a canned bar list; records nothing else."""

    def __init__(self, bars):
        self._bars = bars

    async def get_bars(self, symbol, timeframe, start, end, **kw):
        return [b for b in self._bars if b.symbol == symbol]


@pytest.mark.asyncio
async def test_ingest_skips_future_bars_and_leaves_them_undeduped():
    loop = LiveLoop()
    now = datetime.now(timezone.utc)
    past = bar5(now - timedelta(minutes=5), 100, 101, 99, 100.5)
    forming = bar5(now + timedelta(minutes=3), 100.5, 100.7, 100.4, 100.6)
    loop.provider = FakeProvider([past, forming])
    await loop._ingest_recent(days=1)
    assert ("QQQ", past.ts_close) in loop.seen_bars
    # the forming bar was neither consumed nor marked seen — the completed
    # version of it must still be processable on a later poll
    assert ("QQQ", forming.ts_close) not in loop.seen_bars


@pytest.mark.asyncio
async def test_boot_replay_suppresses_delivery_but_live_polling_delivers(monkeypatch):
    loop = LiveLoop()
    now = datetime.now(timezone.utc)
    delivered, notified = [], []

    async def fake_deliver(alert):
        delivered.append(alert)

    async def fake_notify(trade):
        notified.append(trade)

    monkeypatch.setattr(loop, "_deliver", fake_deliver)
    monkeypatch.setattr(loop, "_notify_settlement", fake_notify)

    class FakeSetup:
        module = Module.ZDTE
        symbol = "QQQ"
        strategy = "T"

    class FakeAlert:
        pass

    fake_setup = FakeSetup()
    fake_alert = FakeAlert()
    monkeypatch.setattr(loop.runner, "on_bar_5m", lambda bar: [fake_setup])
    monkeypatch.setattr(loop.composer, "compose", lambda setup: fake_alert)
    monkeypatch.setattr(loop.executor, "track", lambda t: None)
    monkeypatch.setattr(PaperTrade, "from_alert", classmethod(lambda cls, a, s: None))

    # replay phase: started_at is None -> state warms, nothing delivered
    loop.provider = FakeProvider([bar5(now - timedelta(minutes=10), 100, 101, 99, 100.5)])
    assert loop.started_at is None
    await loop._ingest_recent(days=1)
    assert delivered == []
    assert loop.alerts == [fake_alert]  # still recorded for dashboard/audit

    # live phase: started_at set -> the same pipeline delivers
    loop.started_at = now
    loop.provider = FakeProvider([bar5(now - timedelta(minutes=5), 100.5, 101.5, 100, 101)])
    await loop._ingest_recent(days=1)
    assert delivered == [fake_alert]
