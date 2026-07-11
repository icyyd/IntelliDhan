"""Data-quality sentinel — bad data must never reach the signal plane (G7, doc 01 §2①)."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_schemas import Bar, DataQuality, Quote

STALE_QUOTE_AFTER = timedelta(seconds=30)

_clock = MarketClock()


class QualityReport(BaseModel):
    symbol: str
    quality: DataQuality
    issues: list[str] = []


def check_bars(symbol: str, bars: list[Bar], *, expected_ordered: bool = True) -> QualityReport:
    issues: list[str] = []
    for prev, cur in zip(bars, bars[1:]):
        if expected_ordered and cur.ts_close <= prev.ts_close:
            issues.append(f"out-of-order bar at {cur.ts_close.isoformat()}")
        elif cur.timeframe == prev.timeframe and cur.timeframe.seconds <= 900:
            # Intra-session gap check only: overnight/weekend session breaks are
            # legitimate, so consecutive bars in *different* sessions never gap.
            same_session = _clock.session_id(prev.ts_close) == _clock.session_id(cur.ts_close)
            gap = (cur.ts_close - prev.ts_close).total_seconds()
            if same_session and gap > 3 * cur.timeframe.seconds:
                issues.append(
                    f"gap of {int(gap)}s before {cur.ts_close.isoformat()} "
                    f"(tf={cur.timeframe.value})"
                )
    dupes = len(bars) - len({(b.timeframe, b.ts_close) for b in bars})
    if dupes:
        issues.append(f"{dupes} duplicate bar timestamps")
    quality = DataQuality.OK if not issues else DataQuality.DEGRADED
    return QualityReport(symbol=symbol, quality=quality, issues=issues)


def check_quote(quote: Quote, now: datetime) -> QualityReport:
    issues: list[str] = []
    if quote.crossed:
        issues.append(f"crossed quote bid={quote.bid} ask={quote.ask}")
    age = now - quote.ts
    if age > STALE_QUOTE_AFTER:
        issues.append(f"stale quote: {int(age.total_seconds())}s old")
    if quote.last <= 0:
        issues.append(f"non-positive last price {quote.last}")
    quality = DataQuality.OK if not issues else DataQuality.DEGRADED
    return QualityReport(symbol=quote.symbol, quality=quality, issues=issues)
