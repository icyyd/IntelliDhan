"""Yahoo options chain selector — live-path option resolution for the Composer.

Delta targeting 0.30–0.45 (doc 04 §2.A). Yahoo supplies per-contract IV but no
greeks, so delta comes from Black-Scholes with the quoted IV — an estimate,
labeled as such on the leg. CORE tier: the Robinhood MCP chain (Phase 2) is the
alerting-critical source; this powers development and the equity fallback path.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import yfinance as yf

from intellidhan_schemas.signals import Direction, Module, OptionLeg, Setup

TARGET_DELTA = (0.30, 0.45)
RISK_FREE = 0.04  # config in Phase 2


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(spot: float, strike: float, t_years: float, iv: float, is_call: bool) -> float:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (RISK_FREE + iv * iv / 2) * t_years) / (iv * math.sqrt(t_years))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


class YahooOptionSelector:
    def __init__(self) -> None:
        self._chain_cache: dict[tuple[str, str], object] = {}

    def _pick_expiry(self, ticker: yf.Ticker, module: Module, now: datetime) -> str | None:
        expiries = ticker.options
        if not expiries:
            return None
        if module == Module.ZDTE:
            return expiries[0]  # nearest (0-1 DTE class)
        # swing: 30-45 DTE preferred, else nearest ≥ 21
        best = None
        for e in expiries:
            dte = (datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=timezone.utc) - now).days
            if 30 <= dte <= 45:
                return e
            if dte >= 21 and best is None:
                best = e
        return best or expiries[-1]

    def select(self, setup: Setup) -> OptionLeg | None:
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
        t_years = max(
            (datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
             - setup.ts).total_seconds() / (365.25 * 86400),
            0.5 / 365.25,  # same-day floor: half a day
        )
        best: OptionLeg | None = None
        best_dist = 1e9
        for row in table.itertuples():
            iv = float(getattr(row, "impliedVolatility", 0) or 0)
            bid = float(getattr(row, "bid", 0) or 0)
            ask = float(getattr(row, "ask", 0) or 0)
            oi = float(getattr(row, "openInterest", 0) or 0)
            vol = float(getattr(row, "volume", 0) or 0)
            if bid <= 0 or ask <= 0 or oi < 500 or vol < 100:  # RULE-T8
                continue
            delta = bs_delta(spot, float(row.strike), t_years, iv, is_call)
            adelta = abs(delta)
            mid_target = (TARGET_DELTA[0] + TARGET_DELTA[1]) / 2
            if TARGET_DELTA[0] <= adelta <= TARGET_DELTA[1]:
                dist = abs(adelta - mid_target)
                if dist < best_dist:
                    best_dist = dist
                    best = OptionLeg(
                        occ_symbol=str(row.contractSymbol), side="BUY",
                        option_type="CALL" if is_call else "PUT",
                        strike=float(row.strike), expiry=expiry,
                        delta=round(delta, 3), iv=round(iv, 4),
                    )
                    self._chain_cache[(best.occ_symbol, expiry)] = (bid, ask)
        return best

    def leg_quote(self, leg: OptionLeg) -> tuple[float, float] | None:
        return self._chain_cache.get((leg.occ_symbol, leg.expiry))
