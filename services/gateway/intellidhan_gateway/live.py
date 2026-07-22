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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intellidhan_delivery.briefing import build_briefing
from intellidhan_delivery.format import format_alert
from intellidhan_delivery.telegram import TelegramSender
from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.runner import EngineRunner
from intellidhan_ingestor.market_clock import ET, MarketClock
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
FETCH_TIMEOUT_SECONDS = 20
FETCH_CONCURRENCY = 4
BAR_PUBLICATION_GRACE = timedelta(seconds=90)


class DataQualityError(RuntimeError):
    """Expected sentinel rejection, safe to quarantine at symbol scope."""


@dataclass(frozen=True)
class IngestResult:
    accepted: tuple[str, ...]
    quarantined: dict[str, str]
    waiting: tuple[str, ...] = ()

    @property
    def summary(self) -> str | None:
        if not self.quarantined:
            return None
        details = "; ".join(
            f"{symbol}: {reason}" for symbol, reason in self.quarantined.items()
        )
        return f"quarantined this poll — {details}"


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
        self.symbol_health: dict[str, dict] = {
            symbol: {
                "symbol": symbol,
                "status": "NOT_READY",
                "quality": DataQuality.QUARANTINED.value,
                "actionable": False,
                "last_checked_at": None,
                "last_good_bar_at": None,
                "expected_bar_at": None,
                "source": None,
                "failure_kind": "NOT_READY",
                "last_error": "market data has not been validated",
                "consecutive_failures": 0,
                "last_recovered_at": None,
            }
            for symbol in self.symbols
        }
        self.autotrade = AutotradeManager(symbol_gate=self.symbol_block_reason)
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
        self.last_partial_poll: datetime | None = None
        self.last_close_catchup_at: datetime | None = None
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
            symbol_gate=self.symbol_block_reason,
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
            controls = (
                self.runner.shadow_controls if trade.research_only
                else self.runner.controls
            )
            controls.register_open(trade.module, trade.symbol, trade.strategy)
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
        self._accept_quality(
            "VIX:D",
            "VIX",
            vix,
            expected_timeframe=Timeframe.D1,
            require_bars=True,
            checked_at=end,
        )
        self.runner.set_macro_series(build_macro_series(vix))
        for symbol, daily in zip(self.symbols, daily_results):
            self._accept_quality(
                f"{symbol}:D",
                symbol,
                daily,
                expected_timeframe=Timeframe.D1,
                require_bars=True,
                checked_at=end,
            )
            self.runner.seed_daily(symbol, daily)
        intraday = await self._ingest_recent(
            days=4, now=end
        )  # warm intraday TFs + today so far
        self.started_at = end
        self.last_heartbeat = end
        self.last_error = intraday.summary
        if intraday.quarantined:
            self.last_partial_poll = end
            self.provider_state = "PARTIAL"
        else:
            self.last_successful_poll = end
            self.provider_state = "READY"
            required_close = self.clock.latest_completed_bar_close(
                end - BAR_PUBLICATION_GRACE, Timeframe.M5
            )
            completed_session = self.clock.latest_completed_session_close(
                end - BAR_PUBLICATION_GRACE
            )
            if required_close == completed_session:
                self.last_close_catchup_at = completed_session
        self.boot_state = "READY"

    def _accept_quality(
        self,
        key: str,
        symbol: str,
        bars: list,
        *,
        expected_timeframe: Timeframe | None = None,
        require_bars: bool = False,
        latest_required_close: datetime | None = None,
        checked_at: datetime | None = None,
    ) -> None:
        report = check_bars(
            symbol,
            bars,
            expected_timeframe=expected_timeframe,
            require_bars=require_bars,
            latest_required_close=latest_required_close,
        )
        payload = report.model_dump(mode="json")
        payload.update(
            {
                "checked_at": checked_at.isoformat() if checked_at else None,
                "last_bar_at": max(bar.ts_close for bar in bars).isoformat()
                if bars
                else None,
            }
        )
        self.quality_reports[key] = payload
        if report.quality != DataQuality.OK:
            raise DataQualityError(
                f"data quality rejected {key}: {'; '.join(report.issues)}"
            )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = " ".join(str(exc).split()) or type(exc).__name__
        return message[:320]

    def _record_symbol_success(
        self,
        symbol: str,
        bars: list,
        *,
        checked_at: datetime,
        expected_close: datetime | None,
        actionable: bool,
        inactive_status: str = "WAITING",
        inactive_reason: str = "waiting for the first completed 5-minute bar",
    ) -> None:
        previous = self.symbol_health[symbol]
        latest = max(bars, key=lambda bar: bar.ts_close) if bars else None
        recovered_at = previous.get("last_recovered_at")
        if previous.get("status") == "QUARANTINED":
            recovered_at = checked_at.isoformat()
        self.symbol_health[symbol] = {
            "symbol": symbol,
            "status": "OK" if actionable else inactive_status,
            "quality": DataQuality.OK.value,
            "actionable": actionable,
            "last_checked_at": checked_at.isoformat(),
            "last_good_bar_at": (
                latest.ts_close.isoformat()
                if latest
                else previous.get("last_good_bar_at")
            ),
            "expected_bar_at": expected_close.isoformat() if expected_close else None,
            "source": latest.source if latest else previous.get("source"),
            "failure_kind": None,
            "last_error": None if actionable else inactive_reason,
            "consecutive_failures": 0,
            "last_recovered_at": recovered_at,
        }

    def _record_symbol_failure(
        self,
        symbol: str,
        exc: Exception,
        *,
        checked_at: datetime,
        expected_close: datetime | None,
        failure_kind: str,
    ) -> str:
        reason = self._safe_error(exc)
        previous = self.symbol_health[symbol]
        self.symbol_health[symbol] = {
            **previous,
            "status": "QUARANTINED",
            "quality": DataQuality.QUARANTINED.value,
            "actionable": False,
            "last_checked_at": checked_at.isoformat(),
            "expected_bar_at": expected_close.isoformat() if expected_close else None,
            "failure_kind": failure_kind,
            "last_error": reason,
            "consecutive_failures": int(previous.get("consecutive_failures", 0)) + 1,
        }
        self.quality_reports[f"{symbol}:5m"] = {
            "symbol": symbol,
            "quality": DataQuality.QUARANTINED.value,
            "issues": [reason],
            "checked_at": checked_at.isoformat(),
            "last_bar_at": previous.get("last_good_bar_at"),
        }
        try:
            self.autotrade.block_symbol(symbol, f"{symbol} data is quarantined: {reason}")
        except Exception as persist_exc:
            # The in-memory symbol gate remains fail-closed even if durable
            # intent revocation cannot be written during a store outage.
            self.persistence_ready = False
            print(f"[autotrade] could not persist {symbol} quarantine: {persist_exc}")
        cancelled_alerts = self._cancel_symbol_alerts(symbol, reason, checked_at)
        self.symbol_health[symbol]["cancelled_alerts"] = cancelled_alerts
        return reason

    def _cancel_symbol_alerts(
        self, symbol: str, reason: str, checked_at: datetime
    ) -> int:
        """Permanently retire plans whose market context became untrustworthy."""
        cancelled = 0
        for index, alert in enumerate(self.alerts):
            if alert.symbol.upper() != symbol.upper():
                continue
            if alert.status in {"CANCELLED", "EXPIRED"} or alert.valid_until <= checked_at:
                continue
            risk_note = f"Cancelled after market-data quarantine: {reason}"
            risks = list(alert.risks)
            if risk_note not in risks:
                risks.append(risk_note)
            updated = alert.model_copy(update={"status": "CANCELLED", "risks": risks})
            self.alerts[index] = updated
            if self.persistence_ready:
                try:
                    self.store.upsert_alert(updated.model_dump(mode="json"))
                except Exception as persist_exc:
                    # Keep the in-memory retirement and fail global readiness;
                    # recovery must not make an undurable old plan executable.
                    self.persistence_ready = False
                    print(
                        f"[store] could not persist {symbol} alert retirement: "
                        f"{persist_exc}"
                    )
            cancelled += 1
        return cancelled

    def symbol_block_reason(self, symbol: str) -> str | None:
        status = self.symbol_health.get(symbol.upper())
        if status is None:
            return f"{symbol.upper()} market data is not monitored"
        if status.get("actionable") is True:
            return None
        detail = status.get("last_error") or "market data has not been validated"
        return f"{symbol.upper()} market data is not actionable: {detail}"

    def _mark_market_closed(self) -> None:
        """Disable execution outside RTH without mislabeling valid data as bad."""
        for symbol, state in self.symbol_health.items():
            if state.get("status") == "QUARANTINED":
                continue
            self.symbol_health[symbol] = {
                **state,
                "status": "MARKET_CLOSED",
                "actionable": False,
                "failure_kind": None,
                "last_error": "regular market session is closed",
            }

    async def _ingest_recent(
        self,
        days: int,
        *,
        now: datetime | None = None,
        allow_new_entries: bool = True,
    ) -> IngestResult:
        # Before boot() completes (started_at is None) this is a historical
        # replay: it must warm engine/executor/risk state but never re-send
        # Telegram messages, re-broadcast, or create autotrade intents —
        # otherwise every restart re-delivers days of stale alerts as new.
        replay = self.started_at is None
        end = now or datetime.now(timezone.utc)
        session = self.clock.session_state(end)
        expected_close = self.clock.latest_completed_bar_close(
            end - BAR_PUBLICATION_GRACE, Timeframe.M5
        )
        waiting_for_first_close = (
            session == SessionState.RTH
            and expected_close.astimezone(ET).date() < end.astimezone(ET).date()
        )
        fetch_start = end - timedelta(days=days)
        if expected_close.astimezone(ET).date() < end.astimezone(ET).date():
            # A one-day Monday/premarket request starts after Friday's close.
            # Extend just enough to include the boundary we are validating.
            fetch_start = min(fetch_start, expected_close - timedelta(days=1))
        bars = []
        quarantined: dict[str, str] = {}
        accepted: list[str] = []
        waiting: list[str] = []
        semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def fetch(symbol: str):
            try:
                async with semaphore:
                    fetched = await asyncio.wait_for(
                        self.provider.get_bars(
                            symbol, Timeframe.M5, fetch_start, end
                        ),
                        timeout=FETCH_TIMEOUT_SECONDS,
                    )
                return symbol, fetched, None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return symbol, None, exc

        results = await asyncio.gather(*(fetch(symbol) for symbol in self.symbols))
        for symbol, fetched, fetch_error in results:
            # Per-symbol quarantine: one symbol's halt gap or feed hole must
            # not discard every other symbol's bars or settlement processing.
            if fetch_error is not None:
                kind = "TIMEOUT" if isinstance(fetch_error, TimeoutError) else "PROVIDER"
                quarantined[symbol] = self._record_symbol_failure(
                    symbol,
                    fetch_error,
                    checked_at=end,
                    expected_close=expected_close,
                    failure_kind=kind,
                )
                continue
            completed = [bar for bar in fetched if bar.ts_close <= end]
            try:
                self._accept_quality(
                    f"{symbol}:5m",
                    symbol,
                    completed,
                    expected_timeframe=Timeframe.M5,
                    require_bars=True,
                    latest_required_close=expected_close,
                    checked_at=end,
                )
            except DataQualityError as exc:
                quarantined[symbol] = self._record_symbol_failure(
                    symbol,
                    exc,
                    checked_at=end,
                    expected_close=expected_close,
                    failure_kind="DATA_QUALITY",
                )
                continue
            actionable = session == SessionState.RTH and not waiting_for_first_close
            inactive_status = "WAITING" if waiting_for_first_close else "MARKET_CLOSED"
            inactive_reason = (
                "waiting for the first completed 5-minute bar"
                if waiting_for_first_close
                else "regular market session is closed"
            )
            self._record_symbol_success(
                symbol,
                completed,
                checked_at=end,
                expected_close=expected_close,
                actionable=actionable,
                inactive_status=inactive_status,
                inactive_reason=inactive_reason,
            )
            accepted.append(symbol)
            if not actionable:
                waiting.append(symbol)
            bars.extend(completed)

        result = IngestResult(tuple(accepted), quarantined, tuple(waiting))
        if result.summary:
            print(f"[live] {result.summary}")
        if not accepted and quarantined:
            # Total feed outage is still a degraded poll, not a quiet one.
            raise RuntimeError(result.summary)
        bars.sort(key=lambda b: (b.ts_close, b.symbol))
        for bar in bars:
            if bar.ts_close > end:
                continue  # belt-and-suspenders: never consume a forming bar
            key = (bar.symbol, bar.ts_close)
            if key in self.seen_bars:
                continue
            self.seen_bars.add(key)
            for settled in self.executor.on_bar(bar):
                controls = (
                    self.runner.shadow_controls if settled.research_only
                    else self.runner.controls
                )
                controls.register_close(
                    settled.module, settled.symbol, settled.strategy)
                if replay:
                    # Replay is notification-silent, not durability-silent.
                    # Persist the terminal state before a later restart can
                    # regenerate/overwrite the plan as PENDING.
                    if self.persistence_ready:
                        self.store.upsert_paper_trade(settled.model_dump(mode="json"))
                else:
                    await self._notify_settlement(settled)
            setups = self.runner.on_bar_5m(bar)
            shadow_setups = self.runner.pop_shadow_setups()
            if not allow_new_entries:
                # The post-close pass updates state and settles open paper
                # trades, but must not publish entries that expire overnight.
                continue
            for setup in setups:
                await self._record_setup(setup, replay=replay, deliver=True)
            for setup in shadow_setups:
                await self._record_setup(setup, replay=replay, deliver=False)
        return result

    async def _record_setup(self, setup, *, replay: bool, deliver: bool) -> None:
        """Persist one setup and its paper plan; research plans stay silent."""
        alert = self.composer.compose(setup)
        if alert is None:
            return
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
            return
        if self.persistence_ready:
            self.store.upsert_paper_trade(trade.model_dump(mode="json"))
        controls = (
            self.runner.shadow_controls if getattr(setup, "research_only", False)
            else self.runner.controls
        )
        controls.register_open(setup.module, setup.symbol, setup.strategy)
        if replay:
            return
        if (
            deliver
            and getattr(alert, "status", "ACTIVE") == "ACTIVE"
            and not getattr(alert, "research_only", False)
        ):
            await self._deliver(alert)
            return
        # A SHADOW signal is visible in the terminal but never sent as a live
        # trade alert and never enters the broker intent queue.
        self.publish_ws({"type": "shadow_signal", "data": alert.model_dump(mode="json")})

    def _claim_send(self, key: str) -> bool:
        """Cross-instance exactly-once gate for outbound Telegram.

        Koyeb's rolling deploys briefly run two instances at once; each keeps its
        own in-memory bar/plan dedup, so without a shared claim both would send
        the same alert (the duplicate-Telegram bug). The first instance to claim
        the key in the shared store sends; the rest skip. Fail OPEN: if the store
        is unavailable we send anyway — a rare duplicate beats a missed alert,
        and single-process/no-DB runs have their own in-memory dedup."""
        if not self.persistence_ready:
            return True
        try:
            return self.store.claim_delivery(key)
        except Exception as exc:
            print(f"[deliver] delivery claim failed, sending anyway: {exc}")
            return True

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
        settle_key = trade.plan_key or stable_plan_key(
            trade.created_at, trade.symbol, trade.module, trade.strategy)
        if self._claim_send(f"settle:{settle_key}:{trade.outcome.value}"):
            await self.telegram.send(msg)
        self.publish_ws({"type": "settlement", "data": trade.model_dump(mode="json")})

    async def _deliver(self, alert: Alert) -> None:
        intent = None
        try:
            intent = self.autotrade.on_alert(alert)
        except Exception as exc:  # automation must fail closed without blocking alerts
            print(f"[autotrade] intent creation failed: {exc}")
        alert_key = alert.plan_key or stable_plan_key(
            alert.created_at, alert.symbol, alert.module, alert.strategy)
        if self._claim_send(f"alert:{alert_key}"):
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
        if self._claim_send(f"briefing:{day}"):
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
        session = self.clock.session_state(now)
        required_close = self.clock.latest_completed_bar_close(
            now - BAR_PUBLICATION_GRACE, Timeframe.M5
        )
        completed_session = self.clock.latest_completed_session_close(
            now - BAR_PUBLICATION_GRACE
        )
        close_catchup_due = (
            session != SessionState.RTH
            and required_close == completed_session
            and self.last_close_catchup_at != completed_session
        )
        if session == SessionState.RTH or close_catchup_due:
            try:
                result = await self._ingest_recent(
                    days=1,
                    now=now,
                    allow_new_entries=not close_catchup_due,
                )
                if result.quarantined:
                    self.last_partial_poll = now
                    self.provider_state = "PARTIAL"
                    errors.append(result.summary or "one or more symbols quarantined")
                else:
                    self.last_successful_poll = now
                    self.provider_state = "READY"
                    if close_catchup_due:
                        self.last_close_catchup_at = completed_session
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # feed hiccup: stay alive, sentinel-honest
                self.provider_state = "DEGRADED"
                errors.append(f"ingest: {exc}")
                print(f"[live] ingest error: {exc}")
            self.last_poll = now
        elif session != SessionState.RTH:
            self._mark_market_closed()
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
            # Keep the snapshot contract JSON-native even though FastAPI also
            # applies jsonable_encoder at the HTTP boundary.
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_poll": self.last_poll.isoformat() if self.last_poll else None,
            "last_successful_poll": (self.last_successful_poll.isoformat()
                                     if self.last_successful_poll else None),
            "last_partial_poll": (
                self.last_partial_poll.isoformat() if self.last_partial_poll else None
            ),
            "last_close_catchup_at": (
                self.last_close_catchup_at.isoformat()
                if self.last_close_catchup_at
                else None
            ),
            "last_heartbeat": (self.last_heartbeat.isoformat()
                               if self.last_heartbeat else None),
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "last_error": self.last_error,
            "data_quality": self.quality_reports,
            "symbols": self.symbol_health,
            "quarantined_symbols": [
                symbol
                for symbol, status in self.symbol_health.items()
                if status["status"] == "QUARANTINED"
            ],
            "actionable_symbols": [
                symbol
                for symbol, status in self.symbol_health.items()
                if status["actionable"]
            ],
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
                "data_quality": self.symbol_health.get(sym),
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
