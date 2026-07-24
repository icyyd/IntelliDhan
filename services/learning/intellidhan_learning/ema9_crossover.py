"""Exact 9EMA cross strategy and walk-forward research harness.

This module is deliberately separate from ``EMA9_TREND_PULLBACK``.  The
existing strategy is a pullback/reclaim setup; this one has the literal rule
requested by the trading workflow:

* a completed bar crossing from at/below the 9EMA to above it arms a buy;
* a completed bar crossing from at/above the 9EMA to below it arms a sell;
* entries are filled at the next bar open (no close-to-close look-ahead);
* a protective stop is re-priced after every completed bar, never widened;
* a reverse cross exits the position at the close after stop checks.

The simulator is for underlying research.  It intentionally does not claim
that underlying results transfer to 0DTE options: option-chain spreads,
delta/gamma, and broker fill quality still need a separate replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from intellidhan_analytics.indicators import ATR, EMA
from intellidhan_ingestor.market_clock import ET
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_schemas import Bar, Timeframe


MIN_TRAIN_TRADES = 30
MIN_VALIDATION_TRADES = 20


@dataclass(frozen=True)
class Ema9CrossoverConfig:
    """Frozen parameters for one research candidate.

    ``confirmation_bars=1`` is the literal one-bar cross.  Two completed
    closes is the conservative debounce used by the initial tuning grid.  The
    default signal timeframe is 30m because the existing 5m raw cross is
    demonstrably noisy in the current data window; this is a research default,
    not a profitability claim.
    """

    timeframe: Timeframe = Timeframe.M30
    ema_period: int = 9
    atr_period: int = 14
    initial_stop_atr: float = 1.25
    trail_atr: float = 1.50
    trail_activation_r: float = 0.75
    confirmation_bars: int = 2
    require_ema21_alignment: bool = True
    reentry_cooldown_bars: int = 1
    allow_short: bool = False
    rth_only: bool = True
    flatten_time_et: str = "15:55"
    cost_bps_per_side: float = 2.0
    stop_limit_offset_atr: float = 0.10

    def __post_init__(self) -> None:
        if self.ema_period < 2 or self.atr_period < 2:
            raise ValueError("EMA and ATR periods must be >= 2")
        if self.initial_stop_atr <= 0 or self.trail_atr <= 0:
            raise ValueError("stop ATR multipliers must be positive")
        if self.trail_activation_r < 0:
            raise ValueError("trail activation cannot be negative")
        if self.confirmation_bars not in (1, 2, 3):
            raise ValueError("confirmation_bars must be 1, 2, or 3")
        if self.reentry_cooldown_bars < 0 or self.reentry_cooldown_bars > 5:
            raise ValueError("reentry_cooldown_bars must be between 0 and 5")
        if self.cost_bps_per_side < 0 or self.stop_limit_offset_atr < 0:
            raise ValueError("cost and stop-limit offset cannot be negative")


@dataclass(frozen=True)
class Ema9Trade:
    symbol: str
    direction: str
    signal_ts: datetime
    entry_ts: datetime
    exit_ts: datetime
    entry: float
    exit: float
    initial_stop: float
    final_stop: float
    risk: float
    exit_reason: str
    gross_r: float
    cost_r: float
    net_r: float
    stop_updates: int
    gap_fallback: bool
    mae_r: float
    mfe_r: float


@dataclass
class _Position:
    symbol: str
    direction: int
    signal_ts: datetime
    entry_ts: datetime
    entry: float
    initial_stop: float
    current_stop: float
    risk: float
    highest: float
    lowest: float
    stop_updates: int = 0
    gap_fallback: bool = False
    mae_r: float = 0.0
    mfe_r: float = 0.0


def _parse_flatten_time(value: str):
    hour, minute = value.split(":", 1)
    from datetime import time

    return time(int(hour), int(minute))


def _is_rth(bar: Bar) -> bool:
    local = bar.ts_close.astimezone(ET).time()
    from datetime import time

    # Bar timestamps are close times.  A 09:30 close represents the interval
    # that ended at the open, so it is not an RTH bar; the first valid 5m close
    # is 09:35 (and the first valid 30m close is 10:00).
    return time(9, 30) < local <= time(16, 0)


def _is_flatten_bar(bar: Bar, config: Ema9CrossoverConfig) -> bool:
    if bar.timeframe.seconds >= Timeframe.D1.seconds:
        return False
    flatten = _parse_flatten_time(config.flatten_time_et)
    close_local = bar.ts_close.astimezone(ET)
    open_local = (bar.ts_close - timedelta(seconds=bar.timeframe.seconds)).astimezone(ET)
    return open_local.time() < flatten <= close_local.time()


def _stop_fill(position: _Position, bar: Bar, config: Ema9CrossoverConfig,
               atr: float | None) -> tuple[float, bool] | None:
    """Return a conservative stop-limit fill, with gap fallback to the open.

    A long stop is triggered when the low reaches it; a short stop when the
    high reaches it.  If the opening gap exceeds the configured limit offset,
    the simulated protective order falls back to a marketable fill at the open
    so the report does not hide capital-protection failure.
    """

    offset = (atr or 0.0) * config.stop_limit_offset_atr
    if position.direction > 0 and bar.low <= position.current_stop:
        gap = bar.open < position.current_stop - offset
        return (bar.open if gap else position.current_stop), gap
    if position.direction < 0 and bar.high >= position.current_stop:
        gap = bar.open > position.current_stop + offset
        return (bar.open if gap else position.current_stop), gap
    return None


def _close_trade(position: _Position, exit_ts: datetime, exit_price: float,
                 reason: str, config: Ema9CrossoverConfig) -> Ema9Trade:
    signed_move = position.direction * (exit_price - position.entry)
    gross_r = signed_move / position.risk
    # A bps charge is applied independently to entry and exit.  Expressing it
    # in R keeps reports comparable across SPY/QQQ price levels.
    cost = (position.entry + exit_price) * config.cost_bps_per_side / 10_000.0
    cost_r = cost / position.risk
    return Ema9Trade(
        symbol=position.symbol,
        direction="LONG" if position.direction > 0 else "SHORT",
        signal_ts=position.signal_ts,
        entry_ts=position.entry_ts,
        exit_ts=exit_ts,
        entry=round(position.entry, 6),
        exit=round(exit_price, 6),
        initial_stop=round(position.initial_stop, 6),
        final_stop=round(position.current_stop, 6),
        risk=round(position.risk, 6),
        exit_reason=reason,
        gross_r=round(gross_r, 6),
        cost_r=round(cost_r, 6),
        net_r=round(gross_r - cost_r, 6),
        stop_updates=position.stop_updates,
        gap_fallback=position.gap_fallback,
        mae_r=round(position.mae_r, 6),
        mfe_r=round(position.mfe_r, 6),
    )


def _simulate_symbol(bars: list[Bar], config: Ema9CrossoverConfig) -> list[Ema9Trade]:
    bars = sorted(bars, key=lambda b: b.ts_close)
    ema = EMA(config.ema_period)
    ema21 = EMA(21)
    atr_engine = ATR(config.atr_period)
    prev_close: float | None = None
    prev_ema: float | None = None
    prev_ema21: float | None = None
    position: _Position | None = None
    pending: tuple[int, datetime, float] | None = None
    cooldown = 0
    last_processed_bar: Bar | None = None
    above_count = below_count = 0
    trades: list[Ema9Trade] = []

    for bar in bars:
        if config.rth_only and not _is_rth(bar):
            continue
        last_processed_bar = bar
        cooldown_active = cooldown > 0
        if cooldown_active:
            cooldown -= 1
        just_stopped = False

        flatten_bar = _is_flatten_bar(bar, config)

        # Entries happen on the next bar, using only the prior completed bar's
        # ATR.  The order is then exposed to this bar's full range.
        current_atr = atr_engine.value
        if pending is not None and position is None and not flatten_bar and not cooldown_active:
            direction, signal_ts, signal_atr = pending
            entry = bar.open
            risk = max(entry * 1e-6, signal_atr * config.initial_stop_atr)
            initial_stop = entry - risk if direction > 0 else entry + risk
            position = _Position(
                symbol=bar.symbol, direction=direction, signal_ts=signal_ts,
                # Store the actual open timestamp, not the canonical close
                # timestamp carried by the OHLC bar.
                entry_ts=bar.ts_close - timedelta(seconds=bar.timeframe.seconds),
                entry=entry, initial_stop=initial_stop,
                current_stop=initial_stop, risk=risk, highest=entry, lowest=entry,
            )
            pending = None

        # Coarse bars can straddle 15:55 (for example a 15:30–16:00 30m bar).
        # Exit at the bar open conservatively instead of allowing the position
        # to traverse beyond the intended 0DTE flatten deadline.
        if position is not None and flatten_bar:
            trades.append(_close_trade(position, bar.ts_close - timedelta(seconds=bar.timeframe.seconds),
                                        bar.open, "SESSION_FLATTEN", config))
            position = None

        # Stop checks use the stop from the previous completed bar.  This
        # prevents a close from updating the trail and then retroactively
        # stopping out inside that same candle.
        if position is not None:
            position.highest = max(position.highest, bar.high)
            position.lowest = min(position.lowest, bar.low)
            fav = position.direction * (
                (position.highest - position.entry)
                if position.direction > 0 else (position.lowest - position.entry)
            )
            adverse = position.direction * (
                (position.lowest - position.entry)
                if position.direction > 0 else (position.highest - position.entry)
            )
            position.mfe_r = max(position.mfe_r, fav / position.risk)
            position.mae_r = min(position.mae_r, adverse / position.risk)
            stop = _stop_fill(position, bar, config, current_atr)
            if stop is not None:
                fill, fallback = stop
                position.gap_fallback = position.gap_fallback or fallback
                trades.append(_close_trade(position, bar.ts_close, fill, "TRAIL_STOP", config))
                position = None
                cooldown = config.reentry_cooldown_bars
                just_stopped = config.reentry_cooldown_bars > 0

        # Update indicators only after the intrabar risk check.  Values are
        # therefore those of the just-closed bar, exactly what a live engine
        # can know at signal time.
        current_ema = ema.update(bar.close)
        current_ema21 = ema21.update(bar.close)
        updated_atr = atr_engine.update(bar.high, bar.low, bar.close)
        if current_ema is None or current_ema21 is None or updated_atr is None:
            prev_close, prev_ema, prev_ema21 = bar.close, current_ema, current_ema21
            continue

        up_cross = prev_close is not None and prev_ema is not None \
            and prev_close <= prev_ema and bar.close > current_ema
        down_cross = prev_close is not None and prev_ema is not None \
            and prev_close >= prev_ema and bar.close < current_ema
        long_allowed = (not config.require_ema21_alignment
                         or (bar.close > current_ema21
                             and (prev_ema21 is None or current_ema21 >= prev_ema21)))
        short_allowed = (not config.require_ema21_alignment
                         or (bar.close < current_ema21
                             and (prev_ema21 is None or current_ema21 <= prev_ema21)))
        if up_cross:
            above_count += 1
            below_count = 0
        elif bar.close > current_ema:
            # Confirmation only continues after a *fresh* cross.  Starting a
            # count merely because price is already above EMA9 would create
            # phantom entries after a stop and violate the literal rule.
            above_count = above_count + 1 if above_count else 0
            below_count = 0
        elif down_cross:
            below_count += 1
            above_count = 0
        elif bar.close < current_ema:
            below_count = below_count + 1 if below_count else 0
            above_count = 0
        else:
            above_count = below_count = 0

        # A reverse cross is a thesis exit.  It is evaluated after the stop,
        # and only on the completed close.  Long-only mode treats it as SELL;
        # optional shorts make the same rule symmetric for research.
        if position is not None:
            if position.direction > 0 and below_count >= config.confirmation_bars:
                trades.append(_close_trade(position, bar.ts_close, bar.close,
                                            "EMA9_CROSS_BELOW", config))
                position = None
            elif position.direction < 0 and above_count >= config.confirmation_bars:
                trades.append(_close_trade(position, bar.ts_close, bar.close,
                                            "EMA9_CROSS_ABOVE", config))
                position = None

        # Ratchet the stop for the *next* bar.  The EMA trail is deliberately
        # wide enough to breathe; monotonicity is the invariant that protects
        # both capital and gains.
        if position is not None:
            if position.direction > 0:
                candidate = current_ema - config.trail_atr * updated_atr
                if position.mfe_r >= config.trail_activation_r:
                    candidate = max(candidate, position.entry)
                if candidate > position.current_stop:
                    position.current_stop = candidate
                    position.stop_updates += 1
            else:
                candidate = current_ema + config.trail_atr * updated_atr
                if position.mfe_r >= config.trail_activation_r:
                    candidate = min(candidate, position.entry)
                if candidate < position.current_stop:
                    position.current_stop = candidate
                    position.stop_updates += 1

        if position is None and not just_stopped and not cooldown_active:
            if above_count >= config.confirmation_bars and long_allowed:
                pending = (1, bar.ts_close, updated_atr)
                above_count = 0
            elif below_count >= config.confirmation_bars and config.allow_short and short_allowed:
                pending = (-1, bar.ts_close, updated_atr)
                below_count = 0
            elif below_count >= config.confirmation_bars:
                pending = None

        if flatten_bar:
            pending = None
        prev_close, prev_ema, prev_ema21 = bar.close, current_ema, current_ema21

    if position is not None and last_processed_bar is not None:
        last = last_processed_bar
        trades.append(_close_trade(position, last.ts_close, last.close,
                                    "DATA_END", config))
    return trades


def backtest_ema9_crossover(bars: Iterable[Bar], config: Ema9CrossoverConfig | None = None) -> list[Ema9Trade]:
    """Backtest one or more symbols with the exact close-cross rule."""

    config = config or Ema9CrossoverConfig()
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        if bar.timeframe != config.timeframe:
            raise ValueError(
                f"expected {config.timeframe.value} bars, got {bar.timeframe.value}"
            )
        grouped[bar.symbol].append(bar)
    trades: list[Ema9Trade] = []
    for symbol_bars in grouped.values():
        trades.extend(_simulate_symbol(symbol_bars, config))
    return sorted(trades, key=lambda t: (t.entry_ts, t.symbol))


def performance_report(trades: Iterable[Ema9Trade]) -> dict:
    rows = list(trades)
    if not rows:
        return {"trades": 0, "win_rate": 0.0, "avg_net_r": 0.0,
                "profit_factor": None, "max_drawdown_r": 0.0}
    wins = [t for t in rows if t.net_r > 0]
    gross_wins = sum(t.net_r for t in wins)
    gross_losses = -sum(t.net_r for t in rows if t.net_r < 0)
    equity = peak = drawdown = 0.0
    for trade in sorted(rows, key=lambda t: t.exit_ts):
        equity += trade.net_r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(rows), 4),
        "avg_net_r": round(sum(t.net_r for t in rows) / len(rows), 6),
        "avg_gross_r": round(sum(t.gross_r for t in rows) / len(rows), 6),
        "profit_factor": round(gross_wins / gross_losses, 4) if gross_losses else None,
        "total_net_r": round(sum(t.net_r for t in rows), 6),
        "max_drawdown_r": round(drawdown, 6),
        "stop_exits": sum(t.exit_reason == "TRAIL_STOP" for t in rows),
        "cross_exits": sum(t.exit_reason.startswith("EMA9_CROSS") for t in rows),
        "gap_fallbacks": sum(t.gap_fallback for t in rows),
        "avg_stop_updates": round(sum(t.stop_updates for t in rows) / len(rows), 2),
    }


def _split_trades(trades: list[Ema9Trade], bars: list[Bar]) -> dict[str, list[Ema9Trade]]:
    sessions = sorted({b.ts_close.astimezone(ET).date().isoformat() for b in bars})
    if len(sessions) < 3:
        return {"train": trades, "validation": [], "test": []}
    first = sessions[max(1, int(len(sessions) * 0.60))]
    second = sessions[max(2, int(len(sessions) * 0.80))]
    result = {"train": [], "validation": [], "test": []}
    for trade in trades:
        key = "train" if trade.entry_ts.astimezone(ET).date().isoformat() < first \
            else ("validation" if trade.entry_ts.astimezone(ET).date().isoformat() < second else "test")
        result[key].append(trade)
    return result


def tune_ema9_crossover(bars: Iterable[Bar], base: Ema9CrossoverConfig | None = None) -> dict:
    """Tune only stop/debounce parameters, then score the winner once on test.

    The selection score rewards validation expectancy but requires positive
    train expectancy when there are enough observations.  It reports all
    variants and never silently promotes a winner to live eligibility.
    """

    base = base or Ema9CrossoverConfig()
    bars = list(bars)
    grid = []
    for initial, trail, activation, confirmation, trend_filter, cooldown in (
        (i, t, a, c, tf, cd)
        for i in (1.0, 1.25, 1.5)
        for t in (1.25, 1.5, 1.75, 2.0)
        for a in (0.5, 0.75, 1.0)
        for c in (1, 2)
        for tf in (False, True)
        for cd in (0, 1)
    ):
        grid.append(replace(base, initial_stop_atr=initial, trail_atr=trail,
                            trail_activation_r=activation, confirmation_bars=confirmation,
                            require_ema21_alignment=trend_filter,
                            reentry_cooldown_bars=cooldown))
    rows = []
    split_by_index: list[dict[str, list[Ema9Trade]]] = []
    for config in grid:
        trades = backtest_ema9_crossover(bars, config)
        splits = _split_trades(trades, bars)
        split_by_index.append(splits)
        rows.append({
            "parameters": {"initial_stop_atr": config.initial_stop_atr,
                           "trail_atr": config.trail_atr,
                           "trail_activation_r": config.trail_activation_r,
                           "confirmation_bars": config.confirmation_bars,
                           "require_ema21_alignment": config.require_ema21_alignment,
                           "reentry_cooldown_bars": config.reentry_cooldown_bars},
            "train": performance_report(splits["train"]),
            "validation": performance_report(splits["validation"]),
        })
    eligible = [r for r in rows if r["train"]["trades"] >= MIN_TRAIN_TRADES
                and r["validation"]["trades"] >= MIN_VALIDATION_TRADES
                and r["train"]["avg_net_r"] > 0
                and r["validation"]["avg_net_r"] > 0]
    eligible.sort(key=lambda r: (r["validation"]["avg_net_r"],
                                 r["validation"]["profit_factor"] or 0), reverse=True)
    winner = None
    if eligible:
        winner = dict(eligible[0])
        winner_index = rows.index(eligible[0])
        # Test is deliberately evaluated once, after validation selection.
        winner["test"] = performance_report(split_by_index[winner_index]["test"])
    first_bar = min((b.ts_close for b in bars), default=None)
    last_bar = max((b.ts_close for b in bars), default=None)
    return {
        "strategy": "EMA9_CROSSOVER",
        "status": "FORWARD_SHADOW_ONLY",
        "data_bars": len(bars),
        "symbols": sorted({b.symbol for b in bars}),
        "data_window": {"start": first_bar, "end": last_bar},
        "base_parameters": asdict(base),
        "grid_size": len(rows),
        "selection_rule": (f"train n>={MIN_TRAIN_TRADES}, validation n>={MIN_VALIDATION_TRADES}, "
                           "positive net expectancy in both; test scored once"),
        "winner": winner,
        "all_variants": rows,
    }


async def _fetch(symbols: list[str], timeframe: Timeframe, days: int) -> list[Bar]:
    provider = YahooProvider()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    all_bars: list[Bar] = []
    for symbol in symbols:
        rows = await provider.get_bars(symbol, timeframe, start, end)
        all_bars.extend(rows)
        print(f"{symbol}: {len(rows)} {timeframe.value} bars", file=sys.stderr)
    return sorted(all_bars, key=lambda b: (b.ts_close, b.symbol))


async def _main(args) -> int:
    bars = await _fetch(args.symbols, Timeframe(args.timeframe), args.days)
    base = Ema9CrossoverConfig(timeframe=Timeframe(args.timeframe),
                               allow_short=args.allow_short,
                               cost_bps_per_side=args.cost_bps)
    report = tune_ema9_crossover(bars, base)
    print(json.dumps(report, indent=2, default=str))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tune the 9EMA crossover in walk-forward splits")
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    parser.add_argument("--timeframe", choices=[tf.value for tf in (Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1)], default="30m")
    parser.add_argument("--days", type=int, default=55)
    parser.add_argument("--cost-bps", type=float, default=2.0)
    parser.add_argument("--allow-short", action="store_true")
    parser.add_argument("--out", type=Path)
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
