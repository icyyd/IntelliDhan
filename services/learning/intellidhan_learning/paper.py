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
from intellidhan_schemas.signals import Alert, Direction, Module, Setup


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
    symbol: str
    module: Module
    strategy: str
    direction: Direction
    confidence: float
    composite: float | None = None
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
            alert_id=alert.alert_id, symbol=alert.symbol, module=alert.module,
            strategy=alert.strategy, direction=setup.direction,
            confidence=alert.confidence, composite=setup.composite, entry=setup.entry_underlying,
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

    def track(self, trade: PaperTrade) -> None:
        self.trades.append(trade)
        self._active.setdefault(trade.symbol, []).append(trade)

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
