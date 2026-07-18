"""Data-quality sentinel — bad data must never reach the signal plane (G7, doc 01 §2①)."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_schemas import Bar, DataQuality, Quote, Timeframe

STALE_QUOTE_AFTER = timedelta(seconds=30)

_clock = MarketClock()


class QualityReport(BaseModel):
    symbol: str
    quality: DataQuality
    issues: list[str] = []


def check_bars(
    symbol: str,
    bars: list[Bar],
    *,
    expected_ordered: bool = True,
    expected_timeframe: Timeframe | None = None,
    require_bars: bool = False,
    latest_required_close: datetime | None = None,
) -> QualityReport:
    """Validate bar integrity and, when requested, live-feed completeness.

    Historical/replay callers retain the original structural-only behavior.
    Live callers pass ``require_bars`` and ``latest_required_close`` so an empty
    or boundary-stale provider response cannot be reported as healthy.
    """
    issues: list[str] = []
    if require_bars and not bars:
        issues.append("no bars returned for the required window")
    wrong_symbols = sorted({bar.symbol for bar in bars if bar.symbol != symbol})
    if wrong_symbols:
        issues.append(f"unexpected symbols in {symbol} series: {', '.join(wrong_symbols)}")
    if expected_timeframe is not None:
        wrong_timeframes = sorted(
            {bar.timeframe.value for bar in bars if bar.timeframe != expected_timeframe}
        )
        if wrong_timeframes:
            issues.append(
                f"unexpected timeframes for {symbol}: {', '.join(wrong_timeframes)}"
            )
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
    if latest_required_close is not None:
        if latest_required_close.tzinfo is None:
            raise ValueError("latest_required_close must be timezone-aware")
        if not bars:
            if not require_bars:
                issues.append("no current bar available")
        else:
            latest = max(bar.ts_close for bar in bars)
            if latest < latest_required_close:
                issues.append(
                    "latest bar is stale: "
                    f"{latest.isoformat()} < required {latest_required_close.isoformat()}"
                )
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
