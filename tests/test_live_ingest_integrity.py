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

from intellidhan_engine.composer import Budgets, Composer
from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.terminal_store import TerminalStore
from intellidhan_learning.paper import Outcome, PaperExecutor, PaperTrade
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Direction, Module, Setup


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


def test_restored_trades_ignore_bars_before_creation_or_fill():
    created = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)
    filled = created + timedelta(hours=1)

    pending = make_trade()
    pending.created_at = created
    pending.valid_until = created + timedelta(hours=2)
    pending_executor = PaperExecutor()
    pending_executor.restore([pending])
    assert pending_executor.on_bar(
        bar5(created - timedelta(minutes=5), 100.5, 100.8, 99.8, 100.2)
    ) == []
    assert pending.outcome == Outcome.PENDING
    assert pending_executor.on_bar(
        bar5(created + timedelta(minutes=5), 100.5, 100.8, 99.8, 100.2)
    ) == []
    assert pending.outcome == Outcome.OPEN
    assert pending.filled_at == created + timedelta(minutes=5)

    opened = make_trade()
    opened.created_at = created
    opened.filled_at = filled
    opened.outcome = Outcome.OPEN
    open_executor = PaperExecutor()
    open_executor.restore([opened])
    assert open_executor.on_bar(
        bar5(filled - timedelta(minutes=5), 99.5, 99.8, 98.5, 98.8)
    ) == []
    assert opened.outcome == Outcome.OPEN
    settled = open_executor.on_bar(
        bar5(filled + timedelta(minutes=5), 99.5, 99.8, 98.5, 98.8)
    )
    assert settled == [opened]
    assert opened.exit_ts == filled + timedelta(minutes=5)


def test_executor_deduplicates_replay_regenerated_trade_ids():
    original = make_trade()
    original.created_at = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)
    duplicate = original.model_copy(deep=True)
    duplicate.alert_id = "alr_same_plan_different_process_sequence"
    executor = PaperExecutor()
    executor.restore([original])
    assert len(executor.trades) == 1
    assert executor.track(duplicate) is False
    assert len(executor.active_trades()) == 1


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
        alert_id = "alr_replay_fixture"
        plan_key = "pln_replay_fixture"

    fake_setup = FakeSetup()
    fake_alert = FakeAlert()
    monkeypatch.setattr(loop.runner, "on_bar_5m", lambda bar: [fake_setup])
    monkeypatch.setattr(loop.composer, "compose", lambda setup: fake_alert)
    monkeypatch.setattr(loop.executor, "track", lambda t: True)
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


@pytest.mark.asyncio
async def test_restart_replay_is_time_safe_persists_settlement_and_restores_controls(tmp_path):
    store = TerminalStore(tmp_path / "restart.sqlite3")
    store.init_schema()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    filled = now - timedelta(minutes=10)

    settles_after_fill = make_trade()
    settles_after_fill.alert_id = "alr_restart_settle"
    settles_after_fill.created_at = now - timedelta(minutes=20)
    settles_after_fill.filled_at = filled
    settles_after_fill.outcome = Outcome.OPEN
    settles_after_fill.valid_until = now + timedelta(hours=1)

    remains_active = make_trade(entry=150.0)
    remains_active.alert_id = "alr_restart_active"
    remains_active.created_at = now - timedelta(days=10)
    remains_active.valid_until = now + timedelta(hours=1)
    store.upsert_paper_trade(settles_after_fill.model_dump(mode="json"))
    store.upsert_paper_trade(remains_active.model_dump(mode="json"))

    replay_bars = [
        # Stop geometry before filled_at must be ignored.
        bar5(filled - timedelta(minutes=5), 99.5, 99.8, 98.5, 98.8),
        # The first post-fill bar can legitimately settle the restored trade.
        bar5(filled + timedelta(minutes=5), 99.5, 99.8, 98.5, 98.8),
    ]

    class RestartProvider:
        async def get_bars(self, symbol, timeframe, start, end, **kwargs):
            if timeframe == Timeframe.M5:
                return replay_bars
            return [
                Bar(
                    symbol=symbol,
                    timeframe=Timeframe.D1,
                    ts_close=now - timedelta(days=1),
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1e6,
                    source="fx",
                )
            ]

    loop = LiveLoop(symbols=["QQQ"], store=store)
    loop.provider = RestartProvider()
    await loop.boot()

    durable = {
        item["alert_id"]: PaperTrade.model_validate(item)
        for item in store.list_paper_trades()
    }
    settled = durable["alr_restart_settle"]
    assert settled.outcome == Outcome.STOPPED
    assert settled.exit_ts == filled + timedelta(minutes=5)
    assert settled.exit_ts > settled.filled_at
    assert loop.runner.controls.open_by_module[Module.ZDTE] == 1
    assert loop.runner.controls.open_by_cluster["NDX"] == 1
    assert loop.runner.controls.open_symbol_strategy[("QQQ", "T")] == 1


@pytest.mark.asyncio
async def test_boot_migrates_legacy_plan_identity_beyond_default_alert_window(tmp_path):
    store = TerminalStore(tmp_path / "legacy-window.sqlite3")
    store.init_schema()
    created = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)

    def setup_at(ts):
        return Setup(
            setup_id=f"stp_{ts:%Y%m%d_%H%M}",
            module=Module.ZDTE,
            strategy="ORB_BREAKOUT",
            symbol="QQQ",
            direction=Direction.LONG,
            trigger_tf=Timeframe.M5,
            ts=ts,
            mtf_matrix={"5m": 80.0, "15m": 70.0},
            factors={"F1_trend": 80.0},
            composite=82.0,
            confidence=0.8,
            entry_underlying=100.0,
            stop_underlying=99.0,
            targets_underlying=[101.0, 102.0, 103.0],
            reward_risk=2.0,
            explain="Fixture breakout confirmation.",
            invalidation="Two closes below the opening range.",
        )

    composer = Composer(Budgets("config/budgets.yaml"), option_selector=None)
    target_setup = setup_at(created)
    target_alert = composer.compose(target_setup)
    legacy_alert_id = "alr_legacy_target_1"
    legacy_alert = target_alert.model_dump(mode="json", exclude={"plan_key"})
    legacy_alert["alert_id"] = legacy_alert_id
    store.upsert_alert(legacy_alert)

    terminal_trade = PaperTrade.from_alert(target_alert, target_setup)
    terminal_trade.alert_id = legacy_alert_id
    terminal_trade.plan_key = None
    terminal_trade.created_at = None
    terminal_trade.outcome = Outcome.STOPPED
    terminal_trade.realized_r = -1.0
    terminal_trade.exit_ts = created + timedelta(minutes=5)
    store.upsert_paper_trade(terminal_trade.model_dump(mode="json"))

    # Push the matching legacy alert just outside the normal newest-250 view.
    for index in range(250):
        newer = composer.compose(setup_at(created + timedelta(minutes=5 * (index + 1))))
        store.upsert_alert(newer.model_dump(mode="json"))
    assert len(store.list_alerts()) == 250
    assert len(store.list_alerts(limit=None)) == 251

    class MigrationProvider:
        async def get_bars(self, symbol, timeframe, start, end, **kwargs):
            if timeframe == Timeframe.M5:
                return []
            return [
                Bar(
                    symbol=symbol,
                    timeframe=Timeframe.D1,
                    ts_close=created - timedelta(days=1),
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1e6,
                    source="fx",
                )
            ]

    loop = LiveLoop(symbols=["QQQ"], store=store)
    loop.provider = MigrationProvider()
    await loop.boot()

    migrated = next(
        trade for trade in loop.executor.trades if trade.alert_id == legacy_alert_id
    )
    assert migrated.created_at == created
    assert migrated.plan_key == target_alert.plan_key
    assert migrated.outcome == Outcome.STOPPED
    regenerated = PaperTrade.from_alert(target_alert, target_setup)
    assert loop.executor.track(regenerated) is False
    assert migrated.outcome == Outcome.STOPPED
