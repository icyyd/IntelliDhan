"""Live loop — market-clock-aware polling engine host.

Boot: seed 2y daily + today's 5m bars. During RTH: poll Yahoo each minute for
newly closed 5m bars,
feed EngineRunner, compose alerts, deliver (Telegram/console), track paper.
The gateway reads this object's memory; a Redis-bus split lands when the
engine moves to its own process (doc 01 topology — v1 runs single-process).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intellidhan_delivery.briefing import build_briefing
from intellidhan_delivery.format import format_alert
from intellidhan_delivery.telegram import TelegramSender
from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.runner import EngineRunner
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_ingestor.sentinel import check_bars
from intellidhan_learning.paper import PaperExecutor, PaperTrade, performance_report
from intellidhan_schemas import DataQuality, SessionState, Timeframe
from intellidhan_schemas.signals import Alert, stable_plan_key

from intellidhan_gateway.autotrade import AutotradeManager
from intellidhan_gateway.terminal_store import TerminalStore
from intellidhan_gateway.universe import load_live_symbols, security_records

def _load_dotenv() -> None:
    """Load repo .env into the environment (existing vars win) so Telegram
    credentials and DB passwords work without shell exports. Runs at module
    import — before any LiveLoop/TelegramSender is constructed."""
    env = Path(__file__).resolve().parents[3] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

POLL_SECONDS = 60


class LiveLoop:
    def __init__(
        self,
        symbols: list[str] | None = None,
        store: TerminalStore | None = None,
    ) -> None:
        self.symbols = symbols or load_live_symbols()
        self.provider = YahooProvider()
        self.clock = MarketClock()
        self.runner = EngineRunner(self.symbols)
        self.composer = Composer(Budgets(), option_selector=None)
        self.executor = PaperExecutor()
        self.telegram = TelegramSender()
        self.autotrade = AutotradeManager()
        self.store = store or TerminalStore()
        self.alerts: list[Alert] = []
        self._alert_ids: set[str] = set()
        self._alert_plan_keys: set[str] = set()
        self.last_briefing: dict | None = None
        self._briefed_on: str | None = None
        self.seen_bars: set[tuple[str, datetime]] = set()
        self.started_at: datetime | None = None
        self.last_poll: datetime | None = None
        self.last_successful_poll: datetime | None = None
        self.last_heartbeat: datetime | None = None
        self.boot_state = "NOT_STARTED"
        self.loop_state = "NOT_STARTED"
        self.provider_state = "NOT_READY"
        self.last_error: str | None = None
        self.persistence_ready = False
        self.quality_reports: dict[str, dict] = {}
        self.ws_subscribers: list[asyncio.Queue] = []

    async def boot(self) -> None:
        self.boot_state = "STARTING"
        self.last_error = None
        restart_at = datetime.now(timezone.utc)
        self.store.init_schema()
        self.persistence_ready = True
        self.store.seed_universe(security_records())
        self.autotrade = AutotradeManager(
            self.autotrade.policy_path,
            self.autotrade.state_path,
            state_store=self.store,
        )
        persisted_budgets = self.store.get_setting("budgets")
        if persisted_budgets:
            self.composer.budgets.update(persisted_budgets)
        loaded_alerts: list[Alert] = []
        # Migration and replay idempotency require the identity of every
        # durable alert, not only the dashboard's default newest-250 window.
        for item in self.store.list_alerts(limit=None):
            alert = Alert.model_validate(item)
            if alert.plan_key is None:
                alert = alert.model_copy(update={"plan_key": stable_plan_key(
                    alert.created_at, alert.symbol, alert.module, alert.strategy
                )})
                self.store.upsert_alert(alert.model_dump(mode="json"))
            loaded_alerts.append(alert)
        self._alert_ids = {alert.alert_id for alert in loaded_alerts}
        alerts_by_plan = {alert.plan_key: alert for alert in loaded_alerts}
        self.alerts = list(alerts_by_plan.values())
        self._alert_plan_keys = set(alerts_by_plan)
        alert_created_at = {alert.alert_id: alert.created_at for alert in loaded_alerts}
        restored_trades: list[PaperTrade] = []
        for item in self.store.list_paper_trades():
            trade = PaperTrade.model_validate(item)
            changed = False
            if trade.created_at is None:
                # Migrate pre-created_at rows safely.  A matching alert retains
                # downtime catch-up; an orphan is bounded at restart so stale
                # historical bars can never mutate it.
                trade.created_at = alert_created_at.get(trade.alert_id, restart_at)
                changed = True
            if trade.plan_key is None:
                trade.plan_key = stable_plan_key(
                    trade.created_at, trade.symbol, trade.module, trade.strategy
                )
                changed = True
            if changed:
                self.store.upsert_paper_trade(trade.model_dump(mode="json"))
            restored_trades.append(trade)
        self.executor.restore(restored_trades)
        self.last_briefing = self.store.latest_briefing()
        self.runner = EngineRunner(self.symbols)
        for trade in self.executor.active_trades():
            self.runner.controls.register_open(trade.module, trade.symbol, trade.strategy)
        self.seen_bars = set()

        end = restart_at
        from intellidhan_engine.macro import build_macro_series
        vix_task = self.provider.get_bars(
            "VIX", Timeframe.D1, end - timedelta(days=1200), end
        )
        daily_tasks = [
            self.provider.get_bars(
                symbol,
                Timeframe.D1,
                end - timedelta(days=730),
                end - timedelta(days=1),
                adjusted=True,
            )
            for symbol in self.symbols
        ]
        vix, *daily_results = await asyncio.gather(vix_task, *daily_tasks)
        self._accept_quality("VIX:D", "VIX", vix)
        self.runner.set_macro_series(build_macro_series(vix))
        for symbol, daily in zip(self.symbols, daily_results):
            self._accept_quality(f"{symbol}:D", symbol, daily)
            if not daily:
                raise RuntimeError(f"no completed daily bars returned for {symbol}")
            self.runner.seed_daily(symbol, daily)
        await self._ingest_recent(days=4)  # warm intraday TFs + today so far
        self.started_at = end
        self.last_heartbeat = end
        self.provider_state = "READY"
        self.boot_state = "READY"

    def _accept_quality(self, key: str, symbol: str, bars: list) -> None:
        report = check_bars(symbol, bars)
        self.quality_reports[key] = report.model_dump(mode="json")
        if report.quality != DataQuality.OK:
            raise RuntimeError(f"data quality rejected {key}: {'; '.join(report.issues)}")

    async def _ingest_recent(self, days: int) -> None:
        # Before boot() completes (started_at is None) this is a historical
        # replay: it must warm engine/executor/risk state but never re-send
        # Telegram messages, re-broadcast, or create autotrade intents —
        # otherwise every restart re-delivers days of stale alerts as new.
        replay = self.started_at is None
        end = datetime.now(timezone.utc)
        bars = []
        quarantined: list[str] = []
        for sym in self.symbols:
            # Per-symbol quarantine: one symbol's halt gap or feed hole must
            # not discard every other symbol's bars for the poll — that would
            # freeze alerting AND paper-trade stop/target settlement across
            # the whole universe until the bad symbol's window rolls over.
            try:
                fetched = await self.provider.get_bars(
                    sym, Timeframe.M5, end - timedelta(days=days), end
                )
                self._accept_quality(f"{sym}:5m", sym, fetched)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                quarantined.append(f"{sym}: {exc}")
                continue
            bars.extend(fetched)
        if quarantined:
            self.last_error = "quarantined this poll — " + "; ".join(quarantined)
            print(f"[live] {self.last_error}")
            if len(quarantined) == len(self.symbols):
                # total feed outage is still a degraded poll, not a quiet one
                raise RuntimeError(self.last_error)
        bars.sort(key=lambda b: (b.ts_close, b.symbol))
        for bar in bars:
            if bar.ts_close > end:
                continue  # belt-and-suspenders: never consume a forming bar
            key = (bar.symbol, bar.ts_close)
            if key in self.seen_bars:
                continue
            self.seen_bars.add(key)
            for settled in self.executor.on_bar(bar):
                self.runner.controls.register_close(
                    settled.module, settled.symbol, settled.strategy)
                if replay:
                    # Replay is notification-silent, not durability-silent.
                    # Persist the terminal state before a later restart can
                    # regenerate/overwrite the plan as PENDING.
                    if self.persistence_ready:
                        self.store.upsert_paper_trade(settled.model_dump(mode="json"))
                else:
                    await self._notify_settlement(settled)
            for setup in self.runner.on_bar_5m(bar):
                alert = self.composer.compose(setup)
                if alert is None:
                    continue
                plan_key = alert.plan_key or stable_plan_key(
                    alert.created_at, alert.symbol, alert.module, alert.strategy
                )
                is_new_alert = (
                    alert.alert_id not in self._alert_ids
                    and plan_key not in self._alert_plan_keys
                )
                self._alert_ids.add(alert.alert_id)
                if is_new_alert:
                    self.alerts.append(alert)
                    self._alert_plan_keys.add(plan_key)
                    if self.persistence_ready:
                        self.store.upsert_alert(alert.model_dump(mode="json"))
                trade = PaperTrade.from_alert(alert, setup)
                if not self.executor.track(trade):
                    continue
                if self.persistence_ready:
                    self.store.upsert_paper_trade(trade.model_dump(mode="json"))
                self.runner.controls.register_open(setup.module, setup.symbol, setup.strategy)
                if not replay:
                    await self._deliver(alert)

    async def _notify_settlement(self, trade) -> None:
        """Stop/TP/flatten follow-ups (doc 05 §4 lifecycle, v1)."""
        if self.persistence_ready:
            self.store.upsert_paper_trade(trade.model_dump(mode="json"))
        emoji = {"STOPPED": "🛑", "STOPPED_AFTER_BE": "🛡", "TP_FULL": "💰",
                 "FLATTENED_TIME": "⏱", "EXPIRED_UNFILLED": "⌛"}.get(
            trade.outcome.value, "ℹ️")
        r = f"{trade.realized_r:+.2f}R" if trade.realized_r is not None else "n/a"
        msg = (f"{emoji} {trade.symbol} {trade.strategy.replace('_', ' ')} — "
               f"{trade.outcome.value.replace('_', ' ').title()} at {r} "
               f"(entry {trade.entry:.2f}, tranches exited {trade.tranches_exited}/3)\n"
               f"⚠️ Educational tool — not financial advice.")
        await self.telegram.send(msg)
        self.publish_ws({"type": "settlement", "data": trade.model_dump(mode="json")})

    async def _deliver(self, alert: Alert) -> None:
        intent = None
        try:
            intent = self.autotrade.on_alert(alert)
        except Exception as exc:  # automation must fail closed without blocking alerts
            print(f"[autotrade] intent creation failed: {exc}")
        await self.telegram.send(format_alert(alert))
        self.publish_ws({"type": "alert", "data": alert.model_dump(mode="json")})
        if intent is not None:
            self.publish_ws(
                {"type": "autotrade_intent", "data": intent.model_dump(mode="json")}
            )

    def publish_ws(self, event: dict) -> None:
        """Bound subscriber memory; slow clients receive the newest state change."""
        for queue in list(self.ws_subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    async def maybe_brief(self, now: datetime) -> None:
        """8:30 ET daily briefing (doc 12); once per trading day."""
        from intellidhan_ingestor.market_clock import ET
        local = now.astimezone(ET)
        day = local.date().isoformat()
        if (self._briefed_on == day or local.hour < 8
                or (local.hour == 8 and local.minute < 30)
                or not self.clock.is_trading_day(local.date())):
            return
        briefing = build_briefing(self.runner.states, self.profile_states(), now)
        self.last_briefing = briefing["web"]
        if self.persistence_ready:
            self.store.put_briefing(self.last_briefing)
        await self.telegram.send(briefing["telegram"])
        self._briefed_on = day

    async def run_forever(self) -> None:
        self.loop_state = "STARTING"
        try:
            while self.started_at is None:
                try:
                    await self.boot()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.boot_state = "FAILED"
                    self.loop_state = "DEGRADED"
                    self.provider_state = "NOT_READY"
                    self.last_error = str(exc)
                    self.last_heartbeat = datetime.now(timezone.utc)
                    print(f"[live] boot failed: {exc}")
                    await asyncio.sleep(POLL_SECONDS)
            self.loop_state = "RUNNING"
            while True:
                await self.poll_once(datetime.now(timezone.utc))
                await asyncio.sleep(POLL_SECONDS)
        finally:
            self.loop_state = "STOPPED"

    async def poll_once(self, now: datetime) -> None:
        """Run one supervised iteration; operational failures degrade, never kill it."""
        errors: list[str] = []
        try:
            await self.maybe_brief(now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(f"briefing: {exc}")
            print(f"[live] briefing error: {exc}")
        if self.clock.session_state(now) == SessionState.RTH:
            try:
                await self._ingest_recent(days=1)
                self.last_successful_poll = now
                self.provider_state = "READY"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # feed hiccup: stay alive, sentinel-honest
                self.provider_state = "DEGRADED"
                errors.append(f"ingest: {exc}")
                print(f"[live] ingest error: {exc}")
            self.last_poll = now
        self.last_error = "; ".join(errors) or None
        self.loop_state = "DEGRADED" if errors else "RUNNING"
        self.last_heartbeat = datetime.now(timezone.utc)

    def profile_states(self) -> dict:
        return {sym: st.profile_state for sym, st in self.runner.states.items()
                if st.profile_state is not None}

    # ----- gateway read API -----

    def health(self) -> dict:
        heartbeat_age_seconds = (
            (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()
            if self.last_heartbeat
            else None
        )
        heartbeat_fresh = (
            heartbeat_age_seconds is not None
            and heartbeat_age_seconds <= POLL_SECONDS * 3 + 30
        )
        persistence = self.store.readiness()
        durability_required = os.getenv(
            "INTELLIDHAN_REQUIRE_DURABLE_STATE", ""
        ).lower() in {"1", "true", "yes"}
        durability_ready = not durability_required or persistence["deploy_durable"]
        ready = (
            self.boot_state == "READY"
            and self.loop_state == "RUNNING"
            and self.provider_state == "READY"
            and self.persistence_ready
            and durability_ready
            and heartbeat_fresh
        )
        return {
            "ok": ready,
            "boot_state": self.boot_state,
            "loop_state": self.loop_state,
            "provider_state": self.provider_state,
            "persistence": persistence,
            "durability_required": durability_required,
            "durability_ready": durability_ready,
            # ISO strings, not datetimes: snapshot() embeds this dict and
            # /api/state serializes with plain json.dumps (no encoder) — raw
            # datetimes 500 the dashboard the moment the system turns READY.
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_poll": self.last_poll.isoformat() if self.last_poll else None,
            "last_successful_poll": (self.last_successful_poll.isoformat()
                                     if self.last_successful_poll else None),
            "last_heartbeat": (self.last_heartbeat.isoformat()
                               if self.last_heartbeat else None),
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "last_error": self.last_error,
            "data_quality": self.quality_reports,
        }

    def snapshot(self) -> dict:
        matrices = {}
        for sym, state in self.runner.states.items():
            indicator_snapshots = {}
            for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4,
                       Timeframe.D1):
                indicators = state.indicators(tf)
                if indicators is not None:
                    indicator_snapshots[tf.value] = indicators.model_dump(mode="json")
            matrices[sym] = {
                "matrix": state.mtf_states(),
                "scores": {tf.value: s for tf, s in state.mtf_matrix().items()},
                "last": state.last_bar.close if state.last_bar else None,
                "orb": {"high": state.opening_range.high, "low": state.opening_range.low,
                        "complete": state.opening_range.complete},
                # Bounded, display-only context for the chart-first signal workspace.
                # Keeping this in the existing snapshot avoids a second polling stream.
                "bars": [{
                    "ts_close": bar.ts_close.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                } for bar in state.recent_5m[-72:]],
                "daily_bars": [{
                    "ts_close": bar.ts_close.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                } for bar in state.recent_daily[-150:]],
                "indicators": indicator_snapshots,
            }
        return {
            "session": self.clock.session_state(datetime.now(timezone.utc)).value,
            "symbols": matrices,
            "alerts": [a.model_dump(mode="json") for a in self.alerts[-50:]],
            "suppressed": [s.model_dump(mode="json") for s in self.runner.suppressed[-40:]],
            "performance": performance_report(self.executor.trades),
            "profiles": {k: v.model_dump(mode="json")
                         for k, v in self.profile_states().items()},
            "briefing": self.last_briefing,
            "autotrade": self.autotrade.status(),
            "readiness": self.health(),
            "last_poll": self.last_poll.isoformat() if self.last_poll else None,
        }
