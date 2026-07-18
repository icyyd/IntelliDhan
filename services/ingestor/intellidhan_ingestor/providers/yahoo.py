"""Yahoo Finance provider (CORE tier): bars backfill + quote fallback (doc 02 §1).

Unofficial feed — never on the alert-critical path; the Robinhood MCP provider
(gateway-bridged, Phase 1) is the CRITICAL-tier source for live alerting.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import yfinance as yf

from intellidhan_ingestor.providers.base import Capability, FeedTier, ProviderHealth
from intellidhan_schemas import Bar, Quote, Timeframe

_TF_TO_YF = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "60m",
    Timeframe.D1: "1d",
    Timeframe.W1: "1wk",
    Timeframe.MN1: "1mo",
}

# Yahoo ticker aliases for the index universe (doc 02 §6).
_SYMBOL_ALIASES = {"SPX": "^SPX", "NDX": "^NDX", "VIX": "^VIX", "RUT": "^RUT"}


def _normalize_adjusted_ohlc(
    open_: float, high: float, low: float, close: float
) -> tuple[float, float, float, float] | None:
    """Repair only floating-point boundary noise; reject materially invalid bars."""
    observed_high = max(open_, close)
    observed_low = min(open_, close)
    if low <= observed_low and observed_high <= high:
        return open_, high, low, close
    tolerance = max(1e-8, max(abs(open_), abs(high), abs(low), abs(close)) * 1e-10)
    if observed_high - high <= tolerance and low - observed_low <= tolerance:
        return open_, max(high, observed_high), min(low, observed_low), close
    return None


class YahooProvider:
    name = "yahoo"
    tier = FeedTier.CORE
    capabilities = {Capability.BARS, Capability.QUOTES}

    def __init__(self) -> None:
        self.health = ProviderHealth()

    async def get_bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime,
        *, adjusted: bool = False,
    ) -> list[Bar]:
        if timeframe == Timeframe.H4:
            raise ValueError("4H bars are derived by the analytics rollup, not fetched")
        yf_symbol = _SYMBOL_ALIASES.get(symbol, symbol)
        try:
            df = await asyncio.to_thread(
                lambda: yf.Ticker(yf_symbol).history(
                    start=start, end=end, interval=_TF_TO_YF[timeframe], auto_adjust=adjusted
                )
            )
        except Exception as exc:
            self.health.record_error(str(exc))
            raise
        bars: list[Bar] = []
        intraday = timeframe.seconds < Timeframe.D1.seconds
        fetched_at = datetime.now(timezone.utc)
        for ts, row in df.iterrows():
            if row.isna().any():
                continue  # sentinel will flag the gap; never fabricate data (G7)
            ts_utc = ts.to_pydatetime()
            if ts_utc.tzinfo is None:
                ts_utc = ts_utc.replace(tzinfo=timezone.utc)
            # Yahoo stamps bar *open* time; canonical Bar is keyed by close time.
            if intraday:
                ts_utc = ts_utc + timedelta(seconds=timeframe.seconds)
                if ts_utc > fetched_at:
                    # Still-forming candle: Yahoo includes the in-progress bar,
                    # whose OHLCV keeps changing until its window closes. Bar
                    # semantics are close-time only — emitting the partial
                    # snapshot poisons EMAs/VWAP/opening-range downstream, and
                    # a (symbol, ts_close) dedupe would then block the real
                    # completed bar forever.
                    continue
            open_, high, low, close = (
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
            )
            normalized = _normalize_adjusted_ohlc(open_, high, low, close)
            if normalized is None:
                # Do not invent a range for a materially corrupt upstream row.
                # The downstream data-quality checks can observe the resulting gap.
                continue
            open_, high, low, close = normalized
            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts_close=ts_utc.astimezone(timezone.utc),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=float(row["Volume"]),
                    source=f"{self.name}_adjusted" if adjusted else self.name,
                )
            )
        self.health.record_ok(datetime.now(timezone.utc))
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        yf_symbol = _SYMBOL_ALIASES.get(symbol, symbol)
        try:
            info = await asyncio.to_thread(lambda: yf.Ticker(yf_symbol).fast_info)
            last = float(info["last_price"])
        except Exception as exc:
            self.health.record_error(str(exc))
            raise
        self.health.record_ok(datetime.now(timezone.utc))
        return Quote(symbol=symbol, ts=datetime.now(timezone.utc), last=last, source=self.name)
