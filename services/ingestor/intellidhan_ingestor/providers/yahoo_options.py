"""Yahoo options chain selector — live-path option resolution for the Composer.

Yahoo supplies per-contract IV but no verified real-time quote timestamps or
Greeks. Contracts and estimated delta are therefore always research-only.
Expiration and delta policies are horizon-specific; unavailable horizons use
the Composer's equity fallback rather than silently changing the mandate.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timezone

import yfinance as yf

from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_schemas.signals import Direction, Module, OptionLeg, Setup
from intellidhan_schemas.option_policy import (
    OPTION_HORIZONS,
    eligible_option_expiry,
    option_dte,
)

RISK_FREE = 0.04  # config in Phase 2
QUOTE_CACHE_SECONDS = 30


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(spot: float, strike: float, t_years: float, iv: float, is_call: bool) -> float:
    if (not all(math.isfinite(v) for v in (spot, strike, t_years, iv))
            or t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0):
        return 0.0
    d1 = (math.log(spot / strike) + (RISK_FREE + iv * iv / 2) * t_years) / (iv * math.sqrt(t_years))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


class YahooOptionSelector:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._market_clock = MarketClock()
        self._chain_cache: dict[tuple[str, str], tuple[float, float, datetime]] = {}

    def _pick_expiry(self, ticker: yf.Ticker, module: Module, now: datetime) -> str | None:
        policy = OPTION_HORIZONS.get(module)
        if policy is None:
            return None
        eligible = sorted(e for e in ticker.options if eligible_option_expiry(
            module, e, now, expiry_close=self._market_clock.option_expiry_close(e),
        ))
        preferred = [e for e in eligible if
                     policy.preferred_min_dte <= option_dte(e, now) <= policy.preferred_max_dte]
        return next(iter(preferred or eligible), None)

    def select(self, setup: Setup) -> OptionLeg | None:
        policy = OPTION_HORIZONS.get(setup.module)
        if policy is None:
            return None  # HODL is an equity mandate, not a short-dated option.
        observed_at = self._clock()
        self._chain_cache = {
            key: value for key, value in self._chain_cache.items()
            if 0 <= (observed_at - value[2]).total_seconds() <= QUOTE_CACHE_SECONDS
        }
        try:
            ticker = yf.Ticker(setup.symbol)
            expiry = self._pick_expiry(ticker, setup.module, setup.ts)
            if expiry is None:
                return None
            chain = ticker.option_chain(expiry)
        except Exception:
            return None  # composer falls back to equity
        is_call = setup.direction == Direction.LONG
        table = chain.calls if is_call else chain.puts
        spot = setup.entry_underlying
        expiry_close = self._market_clock.option_expiry_close(expiry)
        if expiry_close is None:
            return None
        t_years = (expiry_close - setup.ts).total_seconds() / (365.25 * 86400)
        best: OptionLeg | None = None
        best_dist = 1e9
        for row in table.itertuples():
            try:
                iv = float(getattr(row, "impliedVolatility", 0) or 0)
                bid = float(getattr(row, "bid", 0) or 0)
                ask = float(getattr(row, "ask", 0) or 0)
                oi = float(getattr(row, "openInterest", 0) or 0)
                vol = float(getattr(row, "volume", 0) or 0)
                strike = float(row.strike)
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(v) for v in (iv, bid, ask, oi, vol, strike)):
                continue
            if iv <= 0 or strike <= 0 or bid <= 0 or ask < bid or oi < 500 or vol < 100:
                continue
            if (ask - bid) / ((ask + bid) / 2) > 0.10:  # RULE-T8
                continue
            delta = bs_delta(spot, strike, t_years, iv, is_call)
            adelta = abs(delta)
            mid_target = sum(policy.delta_range) / 2
            if policy.delta_range[0] <= adelta <= policy.delta_range[1]:
                dist = abs(adelta - mid_target)
                if dist < best_dist:
                    best_dist = dist
                    best = OptionLeg(
                        occ_symbol=str(row.contractSymbol), side="BUY",
                        option_type="CALL" if is_call else "PUT",
                        strike=strike, expiry=expiry,
                        delta=round(delta, 3), iv=round(iv, 4),
                        quote_source="YAHOO_RESEARCH", delta_source="BLACK_SCHOLES_ESTIMATE",
                        research_only=True,
                    )
                    self._chain_cache[(best.occ_symbol, expiry)] = (bid, ask, observed_at)
        return best

    def leg_quote(self, leg: OptionLeg) -> tuple[float, float] | None:
        key = (leg.occ_symbol, leg.expiry)
        cached = self._chain_cache.get(key)
        if cached is None:
            return None
        bid, ask, observed_at = cached
        if not 0 <= (self._clock() - observed_at).total_seconds() <= QUOTE_CACHE_SECONDS:
            self._chain_cache.pop(key, None)
            return None
        return bid, ask
