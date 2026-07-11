"""Live loop — market-clock-aware polling engine host.

Boot: seed 2y daily + today's 5m bars. During RTH: poll Yahoo each minute for
newly closed 5m bars,
feed EngineRunner, compose alerts, deliver (Telegram/console), track paper.
The gateway reads this object's memory; a Redis-bus split lands when the
engine moves to its own process (doc 01 topology — v1 runs single-process).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from intellidhan_delivery.briefing import build_briefing
from intellidhan_delivery.format import format_alert
from intellidhan_delivery.telegram import TelegramSender
from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.runner import EngineRunner
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_learning.paper import PaperExecutor, PaperTrade, performance_report
from intellidhan_schemas import SessionState, Timeframe
from intellidhan_schemas.signals import Alert

UNIVERSE = ["QQQ", "SPY", "SMH", "TQQQ"]
POLL_SECONDS = 60


class LiveLoop:
    def __init__(self, symbols: list[str] | None = None) -> None:
        self.symbols = symbols or UNIVERSE
        self.provider = YahooProvider()
        self.clock = MarketClock()
        self.runner = EngineRunner(self.symbols)
        self.composer = Composer(Budgets(), option_selector=None)
        self.executor = PaperExecutor()
        self.telegram = TelegramSender()
        self.alerts: list[Alert] = []
        self.last_briefing: dict | None = None
        self._briefed_on: str | None = None
        self.seen_bars: set[tuple[str, datetime]] = set()
        self.started_at: datetime | None = None
        self.last_poll: datetime | None = None
        self.ws_subscribers: list[asyncio.Queue] = []

    async def boot(self) -> None:
        end = datetime.now(timezone.utc)
        from intellidhan_engine.macro import build_macro_series
        vix = await self.provider.get_bars("VIX", Timeframe.D1,
                                           end - timedelta(days=1200), end)
        self.runner.set_macro_series(build_macro_series(vix))
        for sym in self.symbols:
            daily = await self.provider.get_bars(
                sym, Timeframe.D1, end - timedelta(days=730), end - timedelta(days=1))
            self.runner.seed_daily(sym, daily)
        await self._ingest_recent(days=4)  # warm intraday TFs + today so far
        self.started_at = end

    async def _ingest_recent(self, days: int) -> None:
        end = datetime.now(timezone.utc)
        bars = []
        for sym in self.symbols:
            bars.extend(await self.provider.get_bars(
                sym, Timeframe.M5, end - timedelta(days=days), end))
        bars.sort(key=lambda b: (b.ts_close, b.symbol))
        for bar in bars:
            key = (bar.symbol, bar.ts_close)
            if key in self.seen_bars:
                continue
            self.seen_bars.add(key)
            self.executor.on_bar(bar)
            for setup in self.runner.on_bar_5m(bar):
                alert = self.composer.compose(setup)
                if alert is None:
                    continue
                self.alerts.append(alert)
                self.executor.track(PaperTrade.from_alert(alert, setup))
                await self._deliver(alert)

    async def _deliver(self, alert: Alert) -> None:
        await self.telegram.send(format_alert(alert))
        for q in list(self.ws_subscribers):
            q.put_nowait({"type": "alert", "data": alert.model_dump(mode="json")})

    async def maybe_brief(self, now: datetime) -> None:
        """8:30 ET daily briefing (doc 12); once per trading day."""
        from intellidhan_ingestor.market_clock import ET
        local = now.astimezone(ET)
        day = local.date().isoformat()
        if (self._briefed_on == day or local.hour < 8
                or (local.hour == 8 and local.minute < 30)
                or not self.clock.is_trading_day(local.date())):
            return
        self._briefed_on = day
        briefing = build_briefing(self.runner.states, self.profile_states(), now)
        self.last_briefing = briefing["web"]
        await self.telegram.send(briefing["telegram"])

    async def run_forever(self) -> None:
        await self.boot()
        while True:
            now = datetime.now(timezone.utc)
            await self.maybe_brief(now)
            if self.clock.session_state(now) == SessionState.RTH:
                try:
                    await self._ingest_recent(days=1)
                except Exception as exc:  # feed hiccup: log, stay alive, sentinel-honest
                    print(f"[live] ingest error: {exc}")
                self.last_poll = now
            await asyncio.sleep(POLL_SECONDS)

    def profile_states(self) -> dict:
        return {sym: st.profile_state for sym, st in self.runner.states.items()
                if st.profile_state is not None}

    # ----- gateway read API -----

    def snapshot(self) -> dict:
        matrices = {}
        for sym, state in self.runner.states.items():
            matrices[sym] = {
                "matrix": state.mtf_states(),
                "scores": {tf.value: s for tf, s in state.mtf_matrix().items()},
                "last": state.last_bar.close if state.last_bar else None,
                "orb": {"high": state.opening_range.high, "low": state.opening_range.low,
                        "complete": state.opening_range.complete},
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
            "last_poll": self.last_poll.isoformat() if self.last_poll else None,
        }
