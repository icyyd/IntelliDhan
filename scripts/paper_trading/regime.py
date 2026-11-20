"""As-of context for the opening-range paper experiment; never order authority.

Inputs must come from one time-consistent data source per series. No network,
account, orders, learned coefficients or current-day VIX close are used here.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from functools import lru_cache
import math
from statistics import median
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
RULE_VERSION = "ORB_RVOL_PRIOR_VIX_V1"
LOOKBACK = 20
MIN_HISTORY = 5
MIN_RVOL = 1.0
MIN_VIX, MAX_VIX = 12.0, 30.0
GAMMA_MAX_AGE = timedelta(minutes=5)


def _at(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware observation timestamp is required")
    return value.astimezone(ET)


def _num(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing observation")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError("Invalid observation")
    return value


def _source(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("A bounded source identifier is required")
    return value.strip()


@lru_cache(maxsize=2048)
def _window(day: date):
    import exchange_calendars as xcals

    cal = xcals.get_calendar("XNYS")
    # Work with the actual schedule labels. exchange_calendars 4.x integer
    # date parsing assumes pandas datetime64[ns], which pandas 3 can represent
    # as [us]; mixing those units rejects valid modern sessions.
    schedule = cal.schedule
    if day.isoformat() not in schedule.index:
        raise ValueError("Decision date is not an XNYS session")
    i = schedule.index.get_loc(day.isoformat())
    if i < LOOKBACK:
        raise ValueError("Insufficient exchange calendar history")
    days = schedule.index[i - LOOKBACK:i + 1]
    # Each boundary is exchange-derived, including holidays and shortened sessions.
    return tuple(x.date() for x in days), schedule.iloc[i - 1]["close"].to_pydatetime().astimezone(ET)


def _unknown(reason, **fields):
    return {"status": "UNKNOWN", "value": None, "reason": reason, **fields}


def _opening_volume(symbol, bars, now, days, source):
    # A complete opening observation requires exactly 09:30, 09:35 and 09:40
    # five-minute bars. Never borrow a later candle or a full day's volume.
    slots = {day: {} for day in days}
    try:
        source = _source(source)
        for row in bars:
            if row.get("symbol", symbol) != symbol:
                raise ValueError("Mixed underlying")
            start = _at(row.get("started_at"))
            bucket = slots.get(start.date())
            if bucket is None or start.hour != 9 or start.minute not in (30, 35, 40):
                continue
            end = start + timedelta(minutes=5)
            if start.second or start.microsecond or end > now:
                continue
            available = _at(row["available_at"]) if "available_at" in row else end
            if available > now:
                continue
            if available < end:
                raise ValueError("Premature completion timestamp")
            # Reject ambiguous dates rather than summing duplicate bars.
            if start.minute in bucket:
                bucket[start.minute] = None
            else:
                try:
                    bucket[start.minute] = _num(row.get("volume"))
                except (TypeError, ValueError, OverflowError):
                    bucket[start.minute] = None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return _unknown("Invalid or mixed opening bar series.")
    totals = {
        day: sum(bucket.values()) for day, bucket in slots.items()
        if len(bucket) == 3 and None not in bucket.values() and math.isfinite(sum(bucket.values()))
    }
    prior = [totals[d] for d in days[:-1] if d in totals]
    common = {"source": source, "prior_sessions": len(prior), "window_sessions": LOOKBACK,
              "minimum_sessions": MIN_HISTORY, "threshold": MIN_RVOL}
    if days[-1] not in totals:
        return _unknown("Today's three opening bars are incomplete or ambiguous.", **common)
    if len(prior) < MIN_HISTORY:
        return _unknown("Not enough complete prior openings in the exchange-session window.", **common)
    typical = median(prior)
    if typical <= 0:
        return _unknown("Median prior opening volume is zero.", **common)
    value = totals[days[-1]] / typical
    if not math.isfinite(value):
        return _unknown("Relative volume cannot be calculated.", **common)
    return {"status": "PASS" if value >= MIN_RVOL else "FAIL", "value": value,
            "opening_volume": totals[days[-1]], "median_opening_volume": typical, **common}


def _prior_vix(vix, now, prior_day, prior_close):
    common = {"required_session": prior_day.isoformat(), "min_inclusive": MIN_VIX,
              "max_exclusive": MAX_VIX, "units": "annualized volatility points"}
    if not isinstance(vix, Mapping):
        return _unknown("No previous-session VIX close provided.", **common)
    try:
        available = _at(vix.get("available_at"))
        value = _num(vix.get("close"), positive=True)
        source = _source(vix.get("source"))
        if (vix.get("symbol", "VIX") != "VIX" or vix.get("session_date") != prior_day.isoformat()
                or not prior_close <= available <= now):
            raise ValueError("Wrong VIX observation")
    except (TypeError, ValueError, OverflowError):
        return _unknown("Prior VIX close is missing, mistimed or from another session.", **common)
    return {"status": "PASS" if MIN_VIX <= value < MAX_VIX else "FAIL", "value": value,
            "source": source, "available_at": available.isoformat(), **common}


def _gross_gamma(symbol, gamma, now, prior_day, prior_close):
    """One-count-per-contract unsigned OI sensitivity; never dealer inventory.

    Only a complete as-of same-symbol 0DTE standard chain is accepted. Upstream
    must attest a common spot/gamma snapshot time and a known OI publication
    time. Contract gamma is d(delta)/d(underlying USD), not gamma cash P/L.
    """
    common = {"units": "USD delta-notional change per 1% underlying move",
              "scope": "standard 0DTE options only", "dealer_sign": "UNKNOWN",
              "influences_decision": False}
    if not isinstance(gamma, Mapping):
        return _unknown("No point-in-time chain with gamma and published open interest.", **common)
    try:
        source = _source(gamma.get("source"))
        observed = _at(gamma.get("observed_at"))
        oi_available = _at(gamma.get("oi_available_at"))
        spot = _num(gamma.get("spot"), positive=True)
        if (gamma.get("symbol") != symbol or gamma.get("expiration_date") != now.date().isoformat()
                or gamma.get("scope") != "ALL_STANDARD_0DTE" or gamma.get("complete") is not True
                or gamma.get("oi_session_date") != prior_day.isoformat()
                or not prior_close <= oi_available <= observed <= now or now - observed > GAMMA_MAX_AGE
                or observed.date() != now.date()):
            raise ValueError("Unsupported chain coverage or timing")
        rows = gamma["contracts"]
        expected = gamma["expected_contract_count"]
        if (not isinstance(rows, (list, tuple)) or not rows or not isinstance(expected, int)
                or isinstance(expected, bool) or len(rows) != expected):
            raise ValueError("Incomplete chain")
        observed_ids = set()
        total = 0.0
        for row in rows:
            identity = _source(row["contract_id"])
            oi = _num(row["open_interest"])
            if (identity in observed_ids or not oi.is_integer()
                    or _num(row["multiplier"]) != 100 or row.get("option_type") not in ("call", "put")):
                raise ValueError("Nonstandard, duplicate or incomplete series")
            observed_ids.add(identity)
            total += _num(row["gamma"]) * oi * 100 * spot ** 2 * 0.01
        if not math.isfinite(total):
            raise ValueError("Overflow")
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return _unknown("Chain is stale, partial, mistimed, or lacks usable contract data.", **common)
    return {"status": "OBSERVED", "value": total, "contracts": len(rows),
            "source": source, "observed_at": observed.isoformat(),
            "oi_session_date": prior_day.isoformat(), "oi_available_at": oi_available.isoformat(), **common}


def regime_context(symbol, bars, now, *, vix=None, gamma=None, volume_source="UNSPECIFIED"):
    """Evaluate frozen research factors for one candidate, not a trade signal.

    bars: single-symbol sequence with aware started_at and 5-minute volume.
    For replay the caller models availability at bar close unless available_at
    is supplied. vix: prior session_date, close, source and aware available_at.
    An unavailable required factor is UNKNOWN, never PASS. Gamma is optional.
    """
    now = _at(now)
    if symbol not in {"SPY", "QQQ"}:
        raise ValueError("Only SPY and QQQ are in this paper experiment")
    days, prior_close = _window(now.date())
    rvol = _opening_volume(symbol, bars, now, days, volume_source)
    vix_obs = _prior_vix(vix, now, days[-2], prior_close)
    gamma_obs = _gross_gamma(symbol, gamma, now, days[-2], prior_close)
    statuses = {rvol["status"], vix_obs["status"]}
    combined = "UNKNOWN" if "UNKNOWN" in statuses else "FAIL" if "FAIL" in statuses else "PASS"
    return {"version": RULE_VERSION, "research_only": True, "live_eligible": False,
            "symbol": symbol, "as_of": now.isoformat(), "rvol": rvol, "vix": vix_obs,
            "gex": gamma_obs, "combined": combined}
