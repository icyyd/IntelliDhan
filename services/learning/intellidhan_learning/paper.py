"""Paper-track executor — every alert simulated per its own printed plan (doc 10 §1).

Conservative fills (doc 03 §7): entry only if a later bar trades through the
limit; stop checked BEFORE target on the same bar (pessimistic); management
follows the alert's tranche plan with breakeven ratchet after T1.
Underlying-level simulation: options P&L is graded on the underlying R-multiple
(vehicle translation lands with real chain history in a later phase).
"""

from __future__ import annotations

from datetime import datetime, time
from enum import Enum

from pydantic import BaseModel

from intellidhan_ingestor.market_clock import ET
from intellidhan_schemas import Bar
from intellidhan_schemas.signals import Alert, Direction, Module, Setup, stable_plan_key


class Outcome(str, Enum):
    OPEN = "OPEN"
    PENDING = "PENDING"           # not yet filled
    EXPIRED_UNFILLED = "EXPIRED_UNFILLED"
    STOPPED = "STOPPED"
    TP_FULL = "TP_FULL"           # all tranches exited at targets
    FLATTENED_TIME = "FLATTENED_TIME"
    STOPPED_AFTER_BE = "STOPPED_AFTER_BE"  # breakeven after T1 ratchet


class PaperTrade(BaseModel):
    alert_id: str
    plan_key: str | None = None
    symbol: str
    module: Module
    strategy: str
    direction: Direction
    confidence: float
    composite: float | None = None
    # Durable lower bound for replay/catch-up.  Older ledger rows did not carry
    # this field, so it remains optional and the live boot path backfills it
    # from the corresponding alert (or the restart time as a fail-safe).
    created_at: datetime | None = None
    entry: float
    initial_stop: float
    targets: list[float]
    filled_at: datetime | None = None
    outcome: Outcome = Outcome.PENDING
    exit_ts: datetime | None = None
    realized_r: float | None = None
    tranches_exited: int = 0
    mae_r: float = 0.0            # max adverse excursion in R
    mfe_r: float = 0.0            # max favorable excursion in R
    valid_until: datetime

    @classmethod
    def from_alert(cls, alert: Alert, setup: Setup) -> "PaperTrade":
        return cls(
            alert_id=alert.alert_id,
            plan_key=alert.plan_key or stable_plan_key(
                alert.created_at, alert.symbol, alert.module, alert.strategy
            ),
            symbol=alert.symbol, module=alert.module,
            strategy=alert.strategy, direction=setup.direction,
            confidence=alert.confidence, composite=setup.composite, created_at=alert.created_at,
            entry=setup.entry_underlying,
            initial_stop=setup.stop_underlying, targets=setup.targets_underlying,
            valid_until=alert.valid_until,
        )


TRANCHES = (0.33, 0.33, 0.34)
ZDTE_FLATTEN = time(15, 55)


class PaperExecutor:
    """Feed every 5m bar; open trades resolve exactly per their printed plan."""

    def __init__(self) -> None:
        self.trades: list[PaperTrade] = []
        self._active: dict[str, list[PaperTrade]] = {}
        self._by_alert_id: dict[str, PaperTrade] = {}
        self._by_plan_key: dict[str, PaperTrade] = {}

    @staticmethod
    def _identity(trade: PaperTrade) -> str:
        if trade.plan_key:
            return trade.plan_key
        if trade.created_at is not None:
            return stable_plan_key(
                trade.created_at, trade.symbol, trade.module, trade.strategy
            )
        return f"legacy_alert:{trade.alert_id}"

    @staticmethod
    def _progress_rank(trade: PaperTrade) -> int:
        if trade.outcome not in {Outcome.PENDING, Outcome.OPEN}:
            return 3
        return 2 if trade.outcome == Outcome.OPEN else 1

    def restore(self, trades: list[PaperTrade]) -> None:
        """Rebuild the executor after a process restart from its durable ledger."""
        # The natural plan key is the durable identity.  Prefer the most
        # advanced representation if malformed/legacy rows contain duplicates;
        # replay must never regress a settled plan back to PENDING.
        deduped: dict[str, PaperTrade] = {}
        for trade in trades:
            identity = self._identity(trade)
            current = deduped.get(identity)
            if current is None or self._progress_rank(trade) >= self._progress_rank(current):
                deduped[identity] = trade
        self.trades = list(deduped.values())
        self._by_alert_id = {trade.alert_id: trade for trade in trades}
        self._by_plan_key = dict(deduped)
        self._active = {}
        for trade in self.trades:
            if trade.outcome in {Outcome.PENDING, Outcome.OPEN}:
                self._active.setdefault(trade.symbol, []).append(trade)

    def track(self, trade: PaperTrade) -> bool:
        """Track a new plan once; return False for replay-regenerated duplicates."""
        identity = self._identity(trade)
        if trade.alert_id in self._by_alert_id or identity in self._by_plan_key:
            return False
        self.trades.append(trade)
        self._by_alert_id[trade.alert_id] = trade
        self._by_plan_key[identity] = trade
        self._active.setdefault(trade.symbol, []).append(trade)
        return True

    def active_trades(self) -> list[PaperTrade]:
        """Return each unresolved durable plan exactly once."""
        return [trade for trades in self._active.values() for trade in trades]

    def on_bar(self, bar: Bar) -> list[PaperTrade]:
        active = self._active.get(bar.symbol)
        if not active:
            return []
        settled: list[PaperTrade] = []
        for t in active:
            if self._advance(t, bar):
                settled.append(t)
        if settled:
            self._active[bar.symbol] = [t for t in active if t not in settled]
        return settled

    def _advance(self, t: PaperTrade, bar: Bar) -> bool:
        # Boot catch-up replays bars from before a restored plan existed.  A
        # pending plan can only fill on a bar strictly after alert creation;
        # an open plan can only advance on a bar strictly after its recorded
        # fill.  Without these guards a restart can create impossible fills or
        # exits whose timestamps predate the trade itself.
        not_before = (
            t.created_at
            if t.outcome == Outcome.PENDING
            else (t.filled_at or t.created_at)
        )
        if not_before is not None and bar.ts_close <= not_before:
            return False

        sign = 1.0 if t.direction == Direction.LONG else -1.0
        risk = abs(t.entry - t.initial_stop)

        if t.outcome == Outcome.PENDING:
            if bar.ts_close > t.valid_until:
                t.outcome = Outcome.EXPIRED_UNFILLED
                t.exit_ts = bar.ts_close
                return True
            if bar.low <= t.entry <= bar.high:
                t.filled_at = bar.ts_close
                t.outcome = Outcome.OPEN
                # A bar that fills the limit AND trades through the stop is a
                # certain real-world stop-out — any intrabar path reaching the
                # entry has also reached the stop. Exempting the fill bar from
                # the stop check (as before) inflated measured win rates.
                # Targets stay uncredited on the fill bar (pessimistic).
                fill_bar_stopped = (bar.low <= t.initial_stop if sign > 0
                                    else bar.high >= t.initial_stop)
                if fill_bar_stopped:
                    t.realized_r = -1.0
                    t.outcome = Outcome.STOPPED
                    t.exit_ts = bar.ts_close
                    return True
            return False

        # OPEN: excursions in R
        fav = sign * (bar.high - t.entry) if sign > 0 else sign * (bar.low - t.entry)
        adv = sign * (bar.low - t.entry) if sign > 0 else sign * (bar.high - t.entry)
        t.mfe_r = max(t.mfe_r, fav / risk)
        t.mae_r = min(t.mae_r, adv / risk)

        stop = t.entry if t.tranches_exited >= 1 else t.initial_stop  # BE ratchet after T1
        stop_hit = bar.low <= stop if sign > 0 else bar.high >= stop
        if stop_hit:  # pessimistic: stop resolves before target on the same bar
            exited_r = sum(
                TRANCHES[i] * sign * (t.targets[i] - t.entry) / risk
                for i in range(t.tranches_exited)
            )
            remaining = 1.0 - sum(TRANCHES[: t.tranches_exited])
            stop_r = sign * (stop - t.entry) / risk
            t.realized_r = round(exited_r + remaining * stop_r, 3)
            t.outcome = Outcome.STOPPED_AFTER_BE if t.tranches_exited >= 1 else Outcome.STOPPED
            t.exit_ts = bar.ts_close
            return True

        while t.tranches_exited < len(t.targets):
            target = t.targets[t.tranches_exited]
            hit = bar.high >= target if sign > 0 else bar.low <= target
            if not hit:
                break
            t.tranches_exited += 1
            if t.tranches_exited == len(t.targets):
                t.realized_r = round(
                    sum(TRANCHES[i] * sign * (t.targets[i] - t.entry) / risk
                        for i in range(len(t.targets))), 3)
                t.outcome = Outcome.TP_FULL
                t.exit_ts = bar.ts_close
                return True

        # 0DTE hard flatten (doc 04 §4)
        if t.module == Module.ZDTE and bar.ts_close.astimezone(ET).time() >= ZDTE_FLATTEN:
            exited_r = sum(TRANCHES[i] * sign * (t.targets[i] - t.entry) / risk
                           for i in range(t.tranches_exited))
            remaining = 1.0 - sum(TRANCHES[: t.tranches_exited])
            close_r = sign * (bar.close - t.entry) / risk
            t.realized_r = round(exited_r + remaining * close_r, 3)
            t.outcome = Outcome.FLATTENED_TIME
            t.exit_ts = bar.ts_close
            return True
        return False


def performance_report(trades: list[PaperTrade]) -> dict:
    decided = [t for t in trades if t.realized_r is not None]
    if not decided:
        return {"decided": 0}
    wins = [t for t in decided if t.realized_r > 0]
    gross_win = sum(t.realized_r for t in wins)
    gross_loss = -sum(t.realized_r for t in decided if t.realized_r < 0)
    return {
        "decided": len(decided),
        "win_rate": round(len(wins) / len(decided), 3),
        "avg_r": round(sum(t.realized_r for t in decided) / len(decided), 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "by_outcome": {
            o.value: sum(1 for t in decided if t.outcome == o)
            for o in Outcome if any(t.outcome == o for t in decided)
        },
    }
