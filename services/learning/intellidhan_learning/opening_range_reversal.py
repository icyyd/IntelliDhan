"""Research-only Opening Range Reversal (ORR) backtest.

The video rule is translated into explicit, bar-close-safe conditions:

* build the first 15 minutes from three completed 5-minute bars;
* require that range to be at least ``manipulation_fraction`` of the
  *prior completed day's* ATR14 (the video's ``20%`` means range / daily ATR,
  not ATR / price);
* find one sufficiently full opposite-colour 5-minute candle before the
  entry cutoff; and
* enter only when the immediately following candle breaks that signal candle.

The simulator trades the underlying, not 0DTE options. Option-chain history,
spread, delta/gamma, and assignment/fill behaviour require a separate replay.
This module is intentionally separate from ``ORB_BREAKOUT`` and is not wired
into live delivery. It is a diagnostic backtest until a longer, point-in-time
intraday dataset and shadow evidence qualify it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

from intellidhan_analytics.indicators import ATR
from intellidhan_ingestor.market_clock import ET, MarketClock
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_schemas import Bar, Timeframe


CLOCK = MarketClock()


@dataclass(frozen=True)
class OpeningRangeReversalConfig:
    """Frozen, auditable parameters for one ORR candidate."""

    daily_atr_period: int = 14
    manipulation_fraction: float = 0.20
    min_reversal_body_fraction: float = 0.50
    entry_cutoff_et: str = "11:30"
    stop_buffer_atr5: float = 0.15
    partial_fraction: float = 0.50
    runner_target_r: float = 2.0
    prior_level_filter: bool = False
    prior_level_tolerance_atr5: float = 0.25
    cost_bps_per_side: float = 2.0
    slippage_atr5: float = 0.02
    flatten_time_et: str = "15:55"
    # 40 accepts both a full 09:30-16:00 session (78 bars) and the
    # 09:30-13:00 exchange half-day (42 bars) without admitting fragments.
    min_session_bars: int = 40

    def __post_init__(self) -> None:
        if self.daily_atr_period < 2:
            raise ValueError("daily_atr_period must be >= 2")
        if not 0 < self.manipulation_fraction <= 1:
            raise ValueError("manipulation_fraction must be in (0, 1]")
        if not 0 <= self.min_reversal_body_fraction <= 1:
            raise ValueError("min_reversal_body_fraction must be in [0, 1]")
        if self.stop_buffer_atr5 < 0 or self.runner_target_r <= 0:
            raise ValueError("stop buffer and runner target must be non-negative/positive")
        if not 0 < self.partial_fraction <= 1:
            raise ValueError("partial_fraction must be in (0, 1]")
        if self.prior_level_tolerance_atr5 < 0:
            raise ValueError("prior_level_tolerance_atr5 must be non-negative")
        if self.cost_bps_per_side < 0 or self.slippage_atr5 < 0:
            raise ValueError("cost and slippage must be non-negative")


# Profiles are research labels only. ``control`` remains the default and the
# shadow candidate is deliberately not wired into live delivery or calibration.
ORR_PROFILES: dict[str, OpeningRangeReversalConfig] = {
    "control": OpeningRangeReversalConfig(),
    "shadow_candidate": OpeningRangeReversalConfig(
        manipulation_fraction=0.25,
        min_reversal_body_fraction=0.25,
        entry_cutoff_et="10:30",
        stop_buffer_atr5=0.15,
        partial_fraction=0.50,
        runner_target_r=2.0,
        prior_level_filter=False,
    ),
}


@dataclass(frozen=True)
class OpeningRangeReversalTrade:
    symbol: str
    session_date: str
    direction: str
    initial_push: str
    opening_range_high: float
    opening_range_low: float
    opening_range_atr_ratio: float
    prior_day_high: float | None
    prior_day_low: float | None
    level_confluence: bool
    signal_ts: datetime
    entry_ts: datetime
    entry: float
    initial_stop: float
    target_one: float
    target_two: float
    exit_ts: datetime
    exit: float
    exit_reason: str
    partial_taken: bool
    risk: float
    gross_r: float
    cost_r: float
    net_r: float
    mae_r: float
    mfe_r: float
    partial_exit: float | None = None
    partial_exit_ts: datetime | None = None


@dataclass
class _Position:
    symbol: str
    session_date: str
    direction: int
    initial_push: int
    opening_range_high: float
    opening_range_low: float
    opening_range_atr_ratio: float
    prior_day_high: float | None
    prior_day_low: float | None
    level_confluence: bool
    signal_ts: datetime
    entry_ts: datetime
    entry: float
    initial_stop: float
    stop: float
    target_one: float
    target_two: float
    risk: float
    slippage: float
    partial_taken: bool = False
    partial_exit: float | None = None
    partial_exit_ts: datetime | None = None
    mae_r: float = 0.0
    mfe_r: float = 0.0


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _local(bar: Bar) -> datetime:
    return bar.ts_close.astimezone(ET)


def _rth_5m(bar: Bar) -> bool:
    local = _local(bar)
    return CLOCK.is_trading_day(local.date()) and time(9, 30) < local.time() <= CLOCK.rth_close(
        local.date()
    )


def _session_bars(bars: Iterable[Bar]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        if bar.timeframe != Timeframe.M5:
            raise ValueError(f"expected 5m bars, got {bar.timeframe.value}")
        if _rth_5m(bar):
            grouped[_local(bar).date().isoformat()].append(bar)
    sessions: dict[str, list[Bar]] = {}
    expected_step = timedelta(seconds=Timeframe.M5.seconds)
    for day, rows in grouped.items():
        ordered = sorted(rows, key=lambda b: b.ts_close)
        if any(
            current.ts_close - previous.ts_close != expected_step
            for previous, current in zip(ordered, ordered[1:])
        ):
            # A later bar must never stand in for the immediately following
            # candle. Reject the complete session when a bar is missing or
            # duplicated; the research contract requires valid contiguous rows.
            continue
        sessions[day] = ordered
    return sessions


def _daily_context(
    daily: Iterable[Bar], period: int
) -> dict[str, tuple[float | None, float | None, float | None]]:
    """Map each date to ATR/high/low from the prior completed daily bar."""

    atr = ATR(period)
    result: dict[str, tuple[float | None, float | None, float | None]] = {}
    prior_high: float | None = None
    prior_low: float | None = None
    for bar in sorted(daily, key=lambda b: b.ts_close):
        day = _local(bar).date().isoformat()
        result[day] = (atr.value, prior_high, prior_low)
        atr.update(bar.high, bar.low, bar.close)
        prior_high, prior_low = bar.high, bar.low
    return result


def _atr5_before(bars: Iterable[Bar], period: int = 14) -> dict[tuple[str, datetime], float | None]:
    """Return 5m ATR immediately before each bar (no signal-bar lookahead)."""

    engines: dict[str, ATR] = {}
    values: dict[tuple[str, datetime], float | None] = {}
    for bar in sorted(bars, key=lambda b: (b.ts_close, b.symbol)):
        if not _rth_5m(bar):
            continue
        engine = engines.setdefault(bar.symbol, ATR(period))
        values[(bar.symbol, bar.ts_close)] = engine.value
        engine.update(bar.high, bar.low, bar.close)
    return values


def _level_confluence(
    *,
    direction: int,
    opening_low: float,
    opening_high: float,
    prior_high: float | None,
    prior_low: float | None,
    atr5: float,
    tolerance: float,
) -> bool:
    if direction < 0:
        return prior_high is not None and opening_high >= prior_high - tolerance * atr5
    return prior_low is not None and opening_low <= prior_low + tolerance * atr5


def _fill_price(direction: int, bar: Bar, trigger: float, slippage: float) -> float:
    """Conservative stop-entry fill: gap at open, otherwise trigger + slippage."""

    if direction > 0:
        raw = bar.open if bar.open >= trigger else trigger
        return raw + slippage
    raw = bar.open if bar.open <= trigger else trigger
    return raw - slippage


def _exit_fill(direction: int, raw: float, slippage: float) -> float:
    """Apply adverse exit slippage to every partial/full exit."""

    return raw - slippage if direction > 0 else raw + slippage


def _close_position(
    position: _Position,
    bar: Bar,
    exit_price: float,
    reason: str,
    fraction: float,
    config: OpeningRangeReversalConfig,
) -> tuple[float, float]:
    signed = position.direction * (exit_price - position.entry) / position.risk
    gross = signed * fraction
    cost = (position.entry + exit_price) * config.cost_bps_per_side / 10_000.0
    cost_r = cost / position.risk * fraction
    return gross, cost_r


def _simulate_session(
    bars: list[Bar],
    daily_atr: float | None,
    prior_high: float | None,
    prior_low: float | None,
    atr5_values: dict[tuple[str, datetime], float | None],
    config: OpeningRangeReversalConfig,
) -> OpeningRangeReversalTrade | None:
    """Simulate at most one trade for one session."""

    if len(bars) < config.min_session_bars or len(bars) < 4 or daily_atr is None:
        return None
    first = bars[:3]
    local_times = [_local(bar).time() for bar in first]
    if local_times != [time(9, 35), time(9, 40), time(9, 45)]:
        return None
    opening_high = max(bar.high for bar in first)
    opening_low = min(bar.low for bar in first)
    opening_width = opening_high - opening_low
    if opening_width <= 0 or opening_width / daily_atr < config.manipulation_fraction:
        return None
    initial_body = first[-1].close - first[0].open
    initial_push = 1 if initial_body > 0 else -1 if initial_body < 0 else 0
    if initial_push == 0:
        return None
    session_date = _local(first[-1]).date().isoformat()
    entry_cutoff = _parse_time(config.entry_cutoff_et)
    flatten_time = _parse_time(config.flatten_time_et)

    candidate: tuple[int, Bar, Bar, float, bool] | None = None
    for index in range(3, len(bars) - 1):
        signal = bars[index]
        signal_local = _local(signal)
        if signal_local.time() > entry_cutoff:
            break
        signal_range = max(signal.high - signal.low, 1e-9)
        body = signal.close - signal.open
        opposite = body * initial_push < 0
        full_enough = abs(body) / signal_range >= config.min_reversal_body_fraction
        if not opposite or not full_enough:
            continue
        direction = -initial_push
        next_bar = bars[index + 1]
        if next_bar.ts_close - signal.ts_close != timedelta(seconds=Timeframe.M5.seconds):
            continue
        trigger = signal.high if direction > 0 else signal.low
        broken = next_bar.high >= trigger if direction > 0 else next_bar.low <= trigger
        if not broken:
            continue
        atr5 = atr5_values.get((signal.symbol, signal.ts_close))
        atr5 = max(atr5 or 0.0, signal_range)
        confluence = _level_confluence(
            direction=direction,
            opening_low=opening_low,
            opening_high=opening_high,
            prior_high=prior_high,
            prior_low=prior_low,
            atr5=atr5,
            tolerance=config.prior_level_tolerance_atr5,
        )
        if config.prior_level_filter and not confluence:
            continue
        slippage = config.slippage_atr5 * atr5
        entry = _fill_price(direction, next_bar, trigger, slippage)
        stop = (
            signal.low - config.stop_buffer_atr5 * atr5
            if direction > 0
            else signal.high + config.stop_buffer_atr5 * atr5
        )
        risk = abs(entry - stop)
        target_one = opening_high if direction > 0 else opening_low
        if (direction > 0 and target_one <= entry) or (direction < 0 and target_one >= entry):
            continue
        target_two = entry + direction * config.runner_target_r * risk
        candidate = (direction, signal, next_bar, entry, confluence)
        break
    if candidate is None:
        return None

    direction, signal, entry_bar, entry, confluence = candidate
    atr5 = max(
        atr5_values.get((signal.symbol, signal.ts_close)) or 0.0, signal.high - signal.low, 1e-9
    )
    stop = (
        signal.low - config.stop_buffer_atr5 * atr5
        if direction > 0
        else signal.high + config.stop_buffer_atr5 * atr5
    )
    risk = abs(entry - stop)
    target_one = opening_high if direction > 0 else opening_low
    target_two = entry + direction * config.runner_target_r * risk
    position = _Position(
        symbol=signal.symbol,
        session_date=session_date,
        direction=direction,
        initial_push=initial_push,
        opening_range_high=opening_high,
        opening_range_low=opening_low,
        opening_range_atr_ratio=opening_width / daily_atr,
        prior_day_high=prior_high,
        prior_day_low=prior_low,
        level_confluence=confluence,
        signal_ts=signal.ts_close,
        entry_ts=entry_bar.ts_close - timedelta(seconds=entry_bar.timeframe.seconds),
        entry=entry,
        initial_stop=stop,
        stop=stop,
        target_one=target_one,
        target_two=target_two,
        risk=risk,
        slippage=slippage,
    )
    gross = cost_r = 0.0
    exit_bar: Bar | None = None
    exit_price = entry
    exit_reason = "DATA_END"
    for bar in bars[bars.index(entry_bar) :]:
        local = _local(bar)
        favorable = (bar.high - entry) if direction > 0 else (entry - bar.low)
        adverse = (bar.low - entry) if direction > 0 else (entry - bar.high)
        position.mfe_r = max(position.mfe_r, favorable / risk)
        position.mae_r = min(position.mae_r, adverse / risk)
        if local.time() >= flatten_time:
            exit_bar, exit_price, exit_reason = (
                bar,
                _exit_fill(direction, bar.open, position.slippage),
                "SESSION_FLATTEN",
            )
            g, c = _close_position(
                position,
                bar,
                exit_price,
                exit_reason,
                1.0 if not position.partial_taken else 1.0 - config.partial_fraction,
                config,
            )
            gross += g
            cost_r += c
            break
        stop_hit = bar.low <= position.stop if direction > 0 else bar.high >= position.stop
        if stop_hit:
            exit_bar, exit_price, exit_reason = bar, position.stop, "STOP"
            if direction > 0 and bar.open < position.stop:
                exit_price = bar.open
            if direction < 0 and bar.open > position.stop:
                exit_price = bar.open
            exit_price = _exit_fill(direction, exit_price, position.slippage)
            fraction = 1.0 if not position.partial_taken else 1.0 - config.partial_fraction
            g, c = _close_position(position, bar, exit_price, exit_reason, fraction, config)
            gross += g
            cost_r += c
            break
        target = position.target_two if position.partial_taken else position.target_one
        target_hit = bar.high >= target if direction > 0 else bar.low <= target
        if target_hit:
            if not position.partial_taken and config.partial_fraction < 1.0:
                partial_exit = _exit_fill(direction, target, position.slippage)
                g, c = _close_position(
                    position,
                    bar,
                    partial_exit,
                    "TARGET_ONE",
                    config.partial_fraction,
                    config,
                )
                gross += g
                cost_r += c
                position.partial_taken = True
                position.partial_exit = partial_exit
                position.partial_exit_ts = bar.ts_close
                position.stop = position.entry
                continue
            exit_bar, exit_price, exit_reason = (
                bar,
                _exit_fill(direction, target, position.slippage),
                "TARGET_TWO",
            )
            fraction = 1.0 if not position.partial_taken else 1.0 - config.partial_fraction
            g, c = _close_position(
                position,
                bar,
                exit_price,
                exit_reason,
                fraction,
                config,
            )
            gross += g
            cost_r += c
            break
    if exit_bar is None:
        exit_bar = bars[-1]
        exit_price = _exit_fill(direction, exit_bar.close, position.slippage)
        fraction = 1.0 if not position.partial_taken else 1.0 - config.partial_fraction
        g, c = _close_position(
            position,
            exit_bar,
            exit_price,
            "DATA_END",
            fraction,
            config,
        )
        gross += g
        cost_r += c
    return OpeningRangeReversalTrade(
        symbol=position.symbol,
        session_date=position.session_date,
        direction="LONG" if direction > 0 else "SHORT",
        initial_push="UP" if initial_push > 0 else "DOWN",
        opening_range_high=round(opening_high, 6),
        opening_range_low=round(opening_low, 6),
        opening_range_atr_ratio=round(opening_width / daily_atr, 6),
        prior_day_high=prior_high,
        prior_day_low=prior_low,
        level_confluence=confluence,
        signal_ts=position.signal_ts,
        entry_ts=position.entry_ts,
        entry=round(position.entry, 6),
        initial_stop=round(position.initial_stop, 6),
        target_one=round(target_one, 6),
        target_two=round(target_two, 6),
        exit_ts=exit_bar.ts_close,
        exit=round(exit_price, 6),
        exit_reason=exit_reason,
        partial_taken=position.partial_taken,
        risk=round(risk, 6),
        gross_r=round(gross, 6),
        cost_r=round(cost_r, 6),
        net_r=round(gross - cost_r, 6),
        mae_r=round(position.mae_r, 6),
        mfe_r=round(position.mfe_r, 6),
        partial_exit=round(position.partial_exit, 6) if position.partial_exit is not None else None,
        partial_exit_ts=position.partial_exit_ts,
    )


def backtest_opening_range_reversal(
    intraday_bars: Iterable[Bar],
    daily_bars: Iterable[Bar],
    config: OpeningRangeReversalConfig | None = None,
) -> list[OpeningRangeReversalTrade]:
    """Backtest one or more symbols with next-bar, no-lookahead semantics."""

    config = config or OpeningRangeReversalConfig()
    intraday = sorted(intraday_bars, key=lambda b: (b.ts_close, b.symbol))
    daily_by_symbol: dict[str, list[Bar]] = defaultdict(list)
    for bar in daily_bars:
        if bar.timeframe != Timeframe.D1:
            raise ValueError(f"expected daily bars, got {bar.timeframe.value}")
        daily_by_symbol[bar.symbol].append(bar)
    atr5_values = _atr5_before(intraday)
    trades: list[OpeningRangeReversalTrade] = []
    for symbol in sorted({b.symbol for b in intraday}):
        symbol_bars = [b for b in intraday if b.symbol == symbol]
        contexts = _daily_context(daily_by_symbol.get(symbol, []), config.daily_atr_period)
        for day, session in _session_bars(symbol_bars).items():
            atr, prior_high, prior_low = contexts.get(day, (None, None, None))
            trade = _simulate_session(session, atr, prior_high, prior_low, atr5_values, config)
            if trade is not None:
                trades.append(trade)
    return sorted(trades, key=lambda t: (t.entry_ts, t.symbol))


def performance_report(
    trades: Iterable[OpeningRangeReversalTrade], *, include_breakdown: bool = True
) -> dict:
    rows = list(trades)
    if not rows:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_net_r": 0.0,
            "profit_factor": None,
            "total_net_r": 0.0,
            "max_drawdown_r": 0.0,
        }
    wins = [t for t in rows if t.net_r > 0]
    gross_wins = sum(t.net_r for t in wins)
    gross_losses = -sum(t.net_r for t in rows if t.net_r < 0)
    equity = peak = max_drawdown = 0.0
    for trade in sorted(rows, key=lambda t: t.exit_ts):
        equity += trade.net_r
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    report = {
        "trades": len(rows),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(rows), 4),
        "avg_net_r": round(sum(t.net_r for t in rows) / len(rows), 6),
        "profit_factor": round(gross_wins / gross_losses, 4) if gross_losses else None,
        "total_net_r": round(sum(t.net_r for t in rows), 6),
        "max_drawdown_r": round(max_drawdown, 6),
        "target_one_rate": round(sum(t.partial_taken for t in rows) / len(rows), 4),
        "stop_rate": round(sum(t.exit_reason == "STOP" for t in rows) / len(rows), 4),
        "level_confluence_rate": round(sum(t.level_confluence for t in rows) / len(rows), 4),
    }
    if include_breakdown:
        report["by_symbol"] = {
            symbol: performance_report(
                [t for t in rows if t.symbol == symbol], include_breakdown=False
            )
            for symbol in sorted({t.symbol for t in rows})
        }
    return report


def _split_by_session(
    trades: list[OpeningRangeReversalTrade],
    session_dates: Iterable[str],
) -> dict[str, list[OpeningRangeReversalTrade]]:
    sessions = sorted(set(session_dates))
    if len(sessions) < 3:
        return {"train": trades, "validation": [], "test": []}
    first = sessions[max(1, int(len(sessions) * 0.60))]
    second = sessions[max(2, int(len(sessions) * 0.80))]
    return {
        "train": [t for t in trades if t.session_date < first],
        "validation": [t for t in trades if first <= t.session_date < second],
        "test": [t for t in trades if t.session_date >= second],
    }


def tune_opening_range_reversal(
    intraday_bars: Iterable[Bar],
    daily_bars: Iterable[Bar],
    base: OpeningRangeReversalConfig | None = None,
) -> dict:
    """Select on validation, score the selected parameters once on test."""

    base = base or OpeningRangeReversalConfig()
    intraday_bars = list(intraday_bars)
    daily_bars = list(daily_bars)
    # Establish walk-forward boundaries once from every observed session,
    # including sessions that produced no trade for a particular variant.
    session_dates = sorted(_session_bars(intraday_bars))
    variants = [
        replace(
            base,
            min_reversal_body_fraction=body,
            stop_buffer_atr5=buffer,
            runner_target_r=target,
            prior_level_filter=levels,
        )
        for body in (0.0, 0.5, 0.75)
        for buffer in (0.0, 0.15, 0.30)
        for target in (1.5, 2.0, 2.5)
        for levels in (False, True)
    ]
    rows = []
    results = []
    for config in variants:
        trades = backtest_opening_range_reversal(intraday_bars, daily_bars, config)
        splits = _split_by_session(trades, session_dates)
        result = {split: performance_report(rows_) for split, rows_ in splits.items()}
        result["parameters"] = {
            "min_reversal_body_fraction": config.min_reversal_body_fraction,
            "stop_buffer_atr5": config.stop_buffer_atr5,
            "runner_target_r": config.runner_target_r,
            "prior_level_filter": config.prior_level_filter,
        }
        rows.append(result)
        results.append((config, splits))
    eligible = [
        (idx, row)
        for idx, row in enumerate(rows)
        if row["train"]["trades"] >= 20
        and row["validation"]["trades"] >= 10
        and row["train"]["avg_net_r"] > 0
        and row["validation"]["avg_net_r"] > 0
    ]
    eligible.sort(
        key=lambda item: (
            item[1]["validation"]["avg_net_r"],
            item[1]["validation"]["profit_factor"] or 0,
        ),
        reverse=True,
    )
    winner = None
    if eligible:
        idx, row = eligible[0]
        winner = dict(row)
        winner["test"] = performance_report(results[idx][1]["test"])
    return {
        "strategy": "ORB_REVERSAL_15M",
        "status": "FORWARD_SHADOW_ONLY",
        "variant_count": len(variants),
        "selection_rule": "train n>=20 and validation n>=10 with positive net expectancy; test scored once",
        "winner": winner,
        "all_variants": rows,
    }


async def _fetch(symbols: list[str], days: int) -> tuple[list[Bar], list[Bar]]:
    provider = YahooProvider()
    end = datetime.now(timezone.utc)
    intraday: list[Bar] = []
    daily: list[Bar] = []
    for symbol in symbols:
        intraday_rows = await provider.get_bars(
            symbol, Timeframe.M5, end - timedelta(days=days), end
        )
        daily_rows = await provider.get_bars(
            symbol,
            Timeframe.D1,
            end - timedelta(days=500),
            end,
            # Keep daily ATR/levels on the same raw price basis as Yahoo's
            # unadjusted intraday bars. Vendor auto-adjustment can apply
            # today's corporate-action factors to old rows and is not
            # point-in-time safe for this diagnostic CLI.
            adjusted=False,
        )
        intraday.extend(intraday_rows)
        daily.extend(daily_rows)
        print(
            f"{symbol}: {len(intraday_rows)} 5m bars, {len(daily_rows)} daily bars", file=sys.stderr
        )
    return intraday, daily


async def _main(args) -> int:
    intraday, daily = await _fetch(args.symbols, args.days)
    config = ORR_PROFILES[args.profile]
    if args.tune:
        report = tune_opening_range_reversal(intraday, daily, config)
        report["profile"] = args.profile
    else:
        trades = backtest_opening_range_reversal(intraday, daily, config)
        report = {
            "strategy": "ORB_REVERSAL_15M",
            "profile": args.profile,
            "config": asdict(config),
            "performance": performance_report(trades),
            "trades": [asdict(t) for t in trades],
        }
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the 15m opening-range reversal")
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    parser.add_argument("--days", type=int, default=59)
    parser.add_argument("--profile", choices=sorted(ORR_PROFILES), default="control")
    parser.add_argument("--tune", action="store_true")
    parser.add_argument("--out", type=Path)
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
