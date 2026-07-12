"""Swing-timeframe tuning harness — 2 years of 1H bars, 3-way split (doc 08 §4).

The 5m campaign proved 55 days can't support stable claims (train 76-89% WR
collapsed to 25-39% on validation). 1H bars reach back ~2 years, so this
harness tunes the doc 05 PULLBACK_CONTINUATION family with TRAIN (55%) /
VALIDATION (25%) / TEST (20%) date splits. A config qualifies only when
validation AND test independently clear the bar — test is looked at ONCE for
the chosen config, not browsed.

Usage: python -m intellidhan_learning.research_swing
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from intellidhan_analytics.trend import TrendEngine
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_schemas import Bar, Timeframe

UNIVERSE = ["QQQ", "SPY", "SMH", "TQQQ", "AAPL", "NVDA", "MSFT", "AMZN",
            "META", "GOOGL", "AMD", "TSLA"]
TARGET_WR = 0.75
MIN_N = 50
TRANCHES = (0.33, 0.33, 0.34)


@dataclass
class Config:
    key: str
    t_mults: tuple[float, float, float]   # ATR(1H) target multiples
    d_trend_min: float                     # daily trend score floor
    rsi_min: float                         # 1H RSI floor on pullback (long side)
    stop_atr: float                        # stop = ema50 - stop_atr*ATR (long)
    long_only: bool


@dataclass
class Trade:
    symbol: str
    config: str
    entry: float
    stop: float
    targets: list[float]
    direction: int                        # +1 / -1
    opened: datetime
    tranches: int = 0
    realized_r: float | None = None
    closed: datetime | None = None

    def advance(self, bar: Bar) -> bool:
        """1H-bar management, pessimistic same-bar stop-first, BE after T1."""
        sign = self.direction
        risk = abs(self.entry - self.stop)
        stop = self.entry if self.tranches >= 1 else self.stop
        stop_hit = bar.low <= stop if sign > 0 else bar.high >= stop
        if stop_hit:
            banked = sum(TRANCHES[i] * sign * (self.targets[i] - self.entry) / risk
                         for i in range(self.tranches))
            rem = 1.0 - sum(TRANCHES[: self.tranches])
            self.realized_r = banked + rem * sign * (stop - self.entry) / risk
            self.closed = bar.ts_close
            return True
        while self.tranches < 3:
            tgt = self.targets[self.tranches]
            if not (bar.high >= tgt if sign > 0 else bar.low <= tgt):
                break
            self.tranches += 1
            if self.tranches == 3:
                self.realized_r = sum(
                    TRANCHES[i] * sign * (self.targets[i] - self.entry) / risk
                    for i in range(3))
                self.closed = bar.ts_close
                return True
        return False


class SwingSim:
    """One symbol, one config: 1H pullback-continuation per doc 05."""

    def __init__(self, symbol: str, cfg: Config) -> None:
        self.symbol = symbol
        self.cfg = cfg
        self.h1 = TrendEngine(symbol, Timeframe.H1)
        self.d1 = TrendEngine(symbol, Timeframe.D1)
        self.clock = MarketClock()
        self.open_trade: Trade | None = None
        self.trades: list[Trade] = []
        self._daily_iter: list[Bar] = []
        self._daily_idx = 0

    def set_daily(self, daily: list[Bar]) -> None:
        self._daily_iter = sorted(daily, key=lambda b: b.ts_close)

    def _advance_daily_to(self, ts: datetime) -> None:
        while (self._daily_idx < len(self._daily_iter)
               and self._daily_iter[self._daily_idx].ts_close <= ts):
            b = self._daily_iter[self._daily_idx]
            self.d1.update(b, self.clock.session_id(b.ts_close))
            self._daily_idx += 1

    def on_h1(self, bar: Bar) -> None:
        self._advance_daily_to(bar.ts_close - timedelta(hours=20))  # only *closed* days
        if self.open_trade is not None:
            if self.open_trade.advance(bar):
                self.trades.append(self.open_trade)
                self.open_trade = None
        snap = self.h1.update(bar, self.clock.session_id(bar.ts_close))
        ind = self.h1.last_indicators
        d_snap = self.d1.snapshot
        if (self.open_trade is not None or ind is None or d_snap is None
                or ind.ema21 is None or ind.ema50 is None or ind.atr14 is None
                or ind.rsi14 is None):
            return
        cfg = self.cfg
        # LONG: D uptrend, 1H pullback into 21/50 EMA zone, RSI holds, reclaim 9EMA
        if d_snap.score >= cfg.d_trend_min and ind.ema21 > ind.ema50:
            zone_lo, zone_hi = ind.ema50, ind.ema21
            touched = bar.low <= zone_hi and bar.low >= zone_lo - 0.5 * ind.atr14
            if touched and ind.rsi14 >= cfg.rsi_min and bar.close > ind.ema9:
                entry = bar.close
                stop = ind.ema50 - cfg.stop_atr * ind.atr14
                if entry > stop:
                    self.open_trade = Trade(
                        symbol=self.symbol, config=cfg.key, entry=entry, stop=stop,
                        targets=[entry + m * ind.atr14 for m in cfg.t_mults],
                        direction=1, opened=bar.ts_close)
                return
        if cfg.long_only:
            return
        if d_snap.score <= -cfg.d_trend_min and ind.ema21 < ind.ema50:
            zone_hi, zone_lo = ind.ema50, ind.ema21
            touched = bar.high >= zone_lo and bar.high <= zone_hi + 0.5 * ind.atr14
            if touched and ind.rsi14 <= 100 - cfg.rsi_min and bar.close < ind.ema9:
                entry = bar.close
                stop = ind.ema50 + cfg.stop_atr * ind.atr14
                if stop > entry:
                    self.open_trade = Trade(
                        symbol=self.symbol, config=cfg.key, entry=entry, stop=stop,
                        targets=[entry - m * ind.atr14 for m in cfg.t_mults],
                        direction=-1, opened=bar.ts_close)


def build_configs() -> list[Config]:
    grid = itertools.product(
        [(0.6, 1.4, 2.4), (0.8, 1.6, 2.8), (1.0, 2.0, 3.5)],
        [25.0, 45.0],
        [45.0, 52.0],
        [0.5, 1.0],
        [True],          # long-only first: shorts in an 11-year bull tape are a
                         # separate hypothesis, not a free parameter
    )
    return [Config(key=f"SW{i:02d}", t_mults=t, d_trend_min=dt, rsi_min=rs,
                   stop_atr=sa, long_only=lo)
            for i, (t, dt, rs, sa, lo) in enumerate(grid)]


def stats(trades: list[Trade]) -> dict:
    done = [t for t in trades if t.realized_r is not None]
    if not done:
        return {"n": 0}
    wins = sum(1 for t in done if t.tranches >= 1)          # TP1-before-stop
    avg_r = sum(t.realized_r for t in done) / len(done)
    gross_w = sum(t.realized_r for t in done if t.realized_r > 0)
    gross_l = -sum(t.realized_r for t in done if t.realized_r < 0)
    return {"n": len(done), "wr": round(wins / len(done), 3),
            "avg_r": round(avg_r, 3),
            "pf": round(gross_w / gross_l, 2) if gross_l else None}


async def run() -> dict:
    provider = YahooProvider()
    end = datetime.now(timezone.utc)
    configs = build_configs()
    sims: dict[tuple[str, str], SwingSim] = {}
    h1_all: list[Bar] = []
    for sym in UNIVERSE:
        h1 = await provider.get_bars(sym, Timeframe.H1, end - timedelta(days=715), end)
        daily = await provider.get_bars(sym, Timeframe.D1, end - timedelta(days=1600), end)
        print(f"{sym}: {len(h1)} 1H bars, {len(daily)} daily", file=sys.stderr)
        h1_all.extend(h1)
        for cfg in configs:
            sim = SwingSim(sym, cfg)
            sim.set_daily(daily)
            sims[(sym, cfg.key)] = sim
    h1_all.sort(key=lambda b: (b.ts_close, b.symbol))

    for bar in h1_all:
        for cfg in configs:
            sims[(bar.symbol, cfg.key)].on_h1(bar)

    all_trades: list[Trade] = []
    for sim in sims.values():
        all_trades.extend(sim.trades)

    days_sorted = sorted({t.opened.date() for t in all_trades})
    d_train = days_sorted[int(len(days_sorted) * 0.55)]
    d_val = days_sorted[int(len(days_sorted) * 0.80)]

    rows = []
    for cfg in configs:
        mine = [t for t in all_trades if t.config == cfg.key]
        tr = stats([t for t in mine if t.opened.date() < d_train])
        va = stats([t for t in mine if d_train <= t.opened.date() < d_val])
        te = stats([t for t in mine if t.opened.date() >= d_val])
        rows.append({"key": cfg.key,
                     "params": {"t_mults": cfg.t_mults, "d_trend_min": cfg.d_trend_min,
                                "rsi_min": cfg.rsi_min, "stop_atr": cfg.stop_atr},
                     "train": tr, "val": va, "test": te})

    qualified = [r for r in rows
                 if r["train"].get("n", 0) >= MIN_N and r["val"].get("n", 0) >= MIN_N
                 and r["train"].get("wr", 0) >= TARGET_WR
                 and r["val"].get("wr", 0) >= TARGET_WR
                 and r["val"].get("avg_r", -1) > 0]
    qualified.sort(key=lambda r: r["val"]["wr"], reverse=True)
    rows.sort(key=lambda r: min(r["train"].get("wr", 0), r["val"].get("wr", 0)),
              reverse=True)
    return {"universe": UNIVERSE, "splits": {"train_until": str(d_train),
            "val_until": str(d_val)}, "qualified": qualified, "all": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    report = asyncio.run(run())
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
