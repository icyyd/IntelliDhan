"""Pure, display-only decisions for the beginner desk.

Research opinions, observed trends and the next UI step are deliberately
separate. These models neither authorize an order nor change engine state.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from intellidhan_engine.calibration import CalibrationMap
from intellidhan_engine.scoring import WEIGHTS, composite
from intellidhan_ingestor.market_clock import ET, MarketClock
from intellidhan_schemas import Timeframe

Horizon = Literal["0DTE", "SWING", "LEAPS", "DAILY_CONTEXT"]
Coverage = Literal["RESEARCH_ONLY", "UNAVAILABLE", "CONTEXT_ONLY", "VALIDATED_SETUP"]
Trend = Literal["UP", "DOWN", "SIDEWAYS", "UNKNOWN"]
Freshness = Literal["CURRENT", "STALE", "UNKNOWN"]


class HorizonCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon: Horizon
    label: str
    coverage: Coverage
    note: str


class DecisionLevel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    key: str
    label: str
    unit: Literal["UNDERLYING_USD_PER_SHARE", "OPTION_USD_PER_SHARE"]
    kind: Literal["ENTRY", "STOP", "TARGET", "REFERENCE"]
    value: float | None = Field(None, gt=0)
    low: float | None = Field(None, gt=0)
    high: float | None = Field(None, gt=0)


class SourceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: str
    as_of: datetime | None = None
    age_seconds: float | None = None
    timestamp_kind: Literal["COMPLETED_BAR", "CONTENT", "PROVIDER_OBSERVATION"]
    freshness: Literal["UNKNOWN", "NOT_ASSESSED"]
    note: str


class DecisionSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["decision-summary-v1"] = "decision-summary-v1"
    symbol: str
    horizon: Horizon
    horizon_label: str
    horizon_coverage: Coverage
    horizon_note: str
    horizons: list[HorizonCoverage] = Field(default_factory=list)
    trend: Trend = "UNKNOWN"
    thesis_direction: Literal["UP", "DOWN", "UNKNOWN"] = "UNKNOWN"
    research_view: Literal["BUY", "SELL", "HOLD", "WAIT"] = "WAIT"
    next_step: Literal["WAIT", "REVIEW_SETUP", "TRACK_ONLY"] = "WAIT"
    status: Literal["CURRENT", "STALE", "UNAVAILABLE", "RESEARCH_ONLY"]
    headline: str
    reasons: list[str]
    blockers: list[str]
    levels: list[DecisionLevel] = Field(default_factory=list)
    as_of: datetime | None = None
    data_as_of: datetime | None = None
    age_seconds: float | None = None
    freshness: Freshness = "UNKNOWN"
    research_generated_at: datetime | None = None
    source_observations: list[SourceObservation] = Field(default_factory=list)
    source_alert_id: str | None = None
    execution_authorized: Literal[False] = False


_LABELS = {
    "0DTE": "Today · intraday options",
    "SWING": "Swing · 2–5 trading days",
    "LEAPS": "LEAPS · long-term options",
    "DAILY_CONTEXT": "Daily stock research",
}
_VALIDATED = {"HISTORICAL_OOS", "FORWARD_PAPER", "LIVE_VALIDATED"}
_UP = {"UP", "STRONG_UP", "UPTREND", "STRONG_UPTREND", "BULLISH"}
_DOWN = {"DOWN", "STRONG_DOWN", "DOWNTREND", "STRONG_DOWNTREND", "BEARISH"}
_SIDEWAYS = {"MIXED", "NEUTRAL", "SIDEWAYS"}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _price(value: Any) -> float | None:
    value = _number(value)
    return value if value is not None and value > 0 else None


def _time(value: Any) -> datetime | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def _now(value: datetime) -> datetime:
    result = _time(value)
    if result is None:
        raise ValueError("now must be timezone-aware")
    return result


def _trend(value: Any) -> Trend:
    if not isinstance(value, str):
        return "UNKNOWN"
    if value in _UP:
        return "UP"
    if value in _DOWN:
        return "DOWN"
    return "SIDEWAYS" if value in _SIDEWAYS else "UNKNOWN"


def _matrix_trend(alert: dict, horizon: Horizon) -> Trend:
    matrix = _dict(alert.get("trend_matrix"))
    keys = ("5m", "15m", "1H") if horizon == "0DTE" else ("1H", "4H", "D")
    values = [_trend(matrix.get(key)) for key in keys if key in matrix]
    known = [value for value in values if value != "UNKNOWN"]
    if not known:
        return "UNKNOWN"
    return known[0] if len(set(known)) == 1 else "SIDEWAYS"


def _coverage(horizon: Horizon, *, validated: bool = False) -> HorizonCoverage:
    if horizon == "0DTE":
        coverage = "VALIDATED_SETUP" if validated else "RESEARCH_ONLY"
        note = (
            "Review the current setup; broker checks and approval are still required."
            if validated else
            "Same-day option methods are being tested. No proven option-profit claim."
        )
    elif horizon == "SWING":
        coverage = "RESEARCH_ONLY"
        note = "Swing setups exist, but their exact 2–5-day exit plan is not yet validated."
    elif horizon == "LEAPS":
        coverage = "UNAVAILABLE"
        note = "Long-term stock research is available; no LEAPS trade strategy is implemented."
    else:
        coverage = "CONTEXT_ONLY"
        note = "Daily stock context is not a same-day, 2–5-day, or LEAPS options signal."
    return HorizonCoverage(horizon=horizon, label=_LABELS[horizon], coverage=coverage, note=note)


def _level(key: str, label: str, value: Any, *, kind="REFERENCE", option=False):
    price = _price(value)
    if price is None:
        return None
    return DecisionLevel(
        key=key, label=label, value=price, kind=kind,
        unit="OPTION_USD_PER_SHARE" if option else "UNDERLYING_USD_PER_SHARE",
    )


def _signal_levels(alert: dict) -> list[DecisionLevel]:
    option = alert.get("vehicle") == "OPTION"
    levels = []
    zone = alert.get("entry_zone")
    if isinstance(zone, (tuple, list)) and len(zone) == 2:
        low, high = (_price(value) for value in zone)
        if low is not None and high is not None and low <= high:
            levels.append(DecisionLevel(
                key="entry", label="Option entry reference" if option else "Share entry reference",
                unit="OPTION_USD_PER_SHARE" if option else "UNDERLYING_USD_PER_SHARE",
                kind="ENTRY", low=low, high=high,
            ))
    levels.append(_level("stop", "Stock-price invalidation", alert.get("stop_underlying"), kind="STOP"))
    if option:
        levels.append(_level("option_stop", "Estimated option stop · not guaranteed",
                             alert.get("stop_est_vehicle"), kind="STOP", option=True))
    for index, target in enumerate(alert.get("take_profits") or []):
        if isinstance(target, dict):
            levels.append(_level(f"target_{index + 1}", f"Stock-price target {index + 1}",
                                 target.get("underlying"), kind="TARGET"))
    return [level for level in levels if level is not None]


def _matching_bucket(alert: dict, buckets: dict) -> tuple[dict, float | None]:
    factors = _dict(alert.get("factors"))
    known = {key: _number(value) for key, value in factors.items() if key in WEIGHTS}
    if not known or any(value is None or not 0 <= value <= 100 for value in known.values()):
        return {}, None
    score = composite(known)
    matches = []
    for key, raw in buckets.items():
        try:
            low, high = (float(value) for value in key.split("-"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (math.isfinite(low) and math.isfinite(high) and 0 <= low < high <= 100):
            continue
        if low <= score < high or (score == high == 100):
            matches.append(_dict(raw))
    return (matches[0], score) if len(matches) == 1 else ({}, score)


def summarize_signal(
    alert: dict, *, health: dict, calibration: dict, now: datetime
) -> dict:
    """Summarize one alert without granting execution or inferring holdings.

    ``health`` is the complete LiveLoop.health() result. ``calibration`` is the
    /api/calibration map, or one {strategy, meta, buckets} record.
    """
    now = _now(now)
    symbol = str(alert.get("symbol") or "Unknown")
    raw_horizon = alert.get("module")
    horizon: Horizon = raw_horizon if raw_horizon in {"0DTE", "SWING", "LEAPS"} else "DAILY_CONTEXT"
    created, expires = _time(alert.get("created_at")), _time(alert.get("valid_until"))
    state = _dict(_dict(health.get("symbols")).get(symbol))
    observed = _time(state.get("last_good_bar_at"))
    blockers = []
    freshness: Freshness = "UNKNOWN"
    if observed is not None and observed <= now:
        try:
            expected = MarketClock().latest_completed_bar_close(now - timedelta(seconds=120), Timeframe.M5)
            freshness = "CURRENT" if observed >= expected else "STALE"
        except ValueError:
            pass
    if freshness != "CURRENT":
        blockers.append("Wait for a current, completed price update.")
    data_ready = (
        health.get("ok") is True
        and state.get("actionable") is True
        and state.get("status") == "OK"
    )
    if not data_ready:
        blockers.append("Market data or platform checks are not ready for a new trade.")
    active = alert.get("status") in {"ACTIVE", "SHADOW"}
    if not active:
        blockers.append("This setup is no longer active.")
    timing_ready = created is not None and created <= now and expires is not None and expires > now and expires > created
    if not timing_ready:
        blockers.append("This setup is expired or its timing cannot be verified.")

    strategy = str(alert.get("strategy") or "")
    record = _dict(calibration.get(strategy))
    if not record and calibration.get("strategy") == strategy:
        record = calibration
    meta = _dict(record.get("meta"))
    buckets = _dict(record.get("buckets"))
    bucket, score = _matching_bucket(alert, buckets)
    sufficient = bucket.get("sufficient") is True and (_number(bucket.get("n")) or 0) >= 15
    evidence = _dict(alert.get("evidence"))
    confidence = _number(alert.get("confidence"))
    bucket_win_rate = _number(bucket.get("wr"))
    evidence_win_rate = _number(evidence.get("point_estimate"))
    try:
        calibrated_net_r = CalibrationMap(strategy, buckets, meta).build_evidence(score).expected_net_r
    except (TypeError, ValueError, KeyError):
        calibrated_net_r = None
    evidence_net_r = _number(evidence.get("expected_net_r"))
    positive_expectancy = (
        evidence_net_r is not None and calibrated_net_r is not None
        and evidence_net_r > 0 and calibrated_net_r > 0
        and abs(evidence_net_r - calibrated_net_r) < 0.00001
    )
    confidence_supported = (
        confidence is not None and bucket_win_rate is not None and evidence_win_rate is not None
        and 0.75 <= confidence <= 0.95 and 0 <= bucket_win_rate <= 1
        and abs(confidence - min(bucket_win_rate, 0.95)) < 0.00001
        and abs(evidence_win_rate - bucket_win_rate) < 0.00001
        and _number(evidence.get("sample_size")) == _number(bucket.get("n"))
    )
    validated = (
        meta.get("live_eligible") is True
        and meta.get("evidence_status") in _VALIDATED
        and evidence.get("evidence_status") in _VALIDATED
        and sufficient
        and confidence_supported
        and positive_expectancy
        and alert.get("research_only") is not True
        and alert.get("status") == "ACTIVE"
    )
    if not validated:
        blockers.append("This method is research-only or lacks verified strategy evidence.")
    coverage = _coverage(horizon, validated=validated)
    if horizon != "0DTE":
        blockers.append(coverage.note)

    direction = "UNKNOWN"
    legs = alert.get("legs") if isinstance(alert.get("legs"), list) else []
    leg = _dict(legs[0]) if len(legs) == 1 else {}
    reasons = []
    if alert.get("vehicle") == "OPTION":
        if alert.get("action") == "BTO" and leg.get("side") == "BUY":
            direction = {"CALL": "UP", "PUT": "DOWN"}.get(leg.get("option_type"), "UNKNOWN")
        if direction == "DOWN":
            reasons.append("A bought put is a falling-price idea, not an instruction to sell shares.")
        elif direction == "UP":
            reasons.append("A bought call is a rising-price idea; the full premium can be lost.")
        if leg.get("research_only") is True:
            blockers.append("The option quote is a research reference, not an executable quote.")
    elif alert.get("vehicle") == "EQUITY":
        direction = {"EQUITY_BUY": "UP", "EQUITY_SELL": "DOWN"}.get(alert.get("action"), "UNKNOWN")
        reasons.append("Share-price setup only; it is not an option contract or option-return forecast.")
        if horizon == "0DTE":
            blockers.append("A same-day option contract has not been verified.")
    if direction == "UNKNOWN":
        blockers.append("The trade direction or instrument cannot be verified.")
    trend = _matrix_trend(alert, horizon)
    trend_words = {"UP": "point up", "DOWN": "point down", "SIDEWAYS": "do not agree", "UNKNOWN": "are unavailable"}
    reasons.append(f"The setup's recorded timeframes {trend_words[trend]}.")
    if trend in {"UNKNOWN", "SIDEWAYS"} or (direction != "UNKNOWN" and trend != direction):
        blockers.append("Wait for the recorded timeframes to agree with the trade idea.")
    if horizon == "0DTE" and any(
        _trend(_dict(alert.get("trend_matrix")).get(key)) == "UNKNOWN"
        for key in ("5m", "15m", "1H")
    ):
        blockers.append("The 5-minute, 15-minute and hourly trend checks must all be available.")
    levels = _signal_levels(alert)
    if not any(level.kind == "ENTRY" for level in levels) or not any(level.key == "stop" for level in levels):
        blockers.append("A complete entry and stock-price invalidation plan is unavailable.")

    blockers = list(dict.fromkeys(blockers))
    can_review = not blockers
    view = ("BUY" if direction == "UP" else "SELL") if can_review else "WAIT"
    status = "CURRENT" if can_review else "RESEARCH_ONLY"
    if freshness == "STALE" or (expires is not None and expires <= now):
        status = "STALE"
    elif freshness == "UNKNOWN" or not active or not data_ready or not timing_ready:
        status = "UNAVAILABLE"
    phrase = {"UP": "rising-price", "DOWN": "falling-price", "UNKNOWN": "unconfirmed"}[direction]
    return DecisionSummary(
        symbol=symbol, horizon=horizon, horizon_label=coverage.label,
        horizon_coverage=coverage.coverage, horizon_note=coverage.note,
        horizons=[coverage], trend=trend, thesis_direction=direction,
        research_view=view, next_step="REVIEW_SETUP" if can_review else "WAIT",
        status=status, headline=f"Review a {phrase} setup" if can_review else f"Wait · {phrase} idea",
        reasons=reasons, blockers=blockers, levels=levels, as_of=created,
        data_as_of=observed, age_seconds=max(0, (now - observed).total_seconds()) if observed else None,
        freshness=freshness, source_alert_id=alert.get("alert_id"),
    ).model_dump(mode="json")


def _forecast_ready(analysis: dict) -> bool:
    horizons = _dict(_dict(analysis.get("forecast")).get("horizons"))
    return any(
        _dict(item).get("label") in {"FAVORABLE", "UNFAVORABLE", "NO_CLEAR_EDGE"}
        and _dict(item).get("confidence") in {"MODERATE", "HIGH"}
        and (_number(_dict(item).get("matched_state_samples")) or 0) >= 12
        and _dict(_dict(item).get("walk_forward_validation")).get("status") == "OUTPERFORMS_BASE"
        and (_number(_dict(_dict(item).get("walk_forward_validation")).get("brier_skill_pct")) or 0) > 0
        and _dict(item).get("trading_days") == {"one_month": 21, "three_months": 63}[key]
        for key, item in horizons.items() if key in {"one_month", "three_months"}
    )


def _source_observations(intelligence: dict, analysis: dict, now: datetime) -> list[SourceObservation]:
    supplied = intelligence.get("sources")
    source_map = {
        row.get("name"): row for row in supplied if isinstance(row, dict)
    } if isinstance(supplied, list) else {}
    observations = []
    for name, kind, fallback in (
        ("IntelliDhan scan", "COMPLETED_BAR", {"as_of": analysis.get("as_of"), "status": "AVAILABLE"}),
        ("SEC EDGAR", "PROVIDER_OBSERVATION", {}),
        ("Alpha Vantage", "CONTENT", _dict(intelligence.get("news"))),
        ("Alpha Vantage company overview", "PROVIDER_OBSERVATION", {}),
        ("Finnhub", "PROVIDER_OBSERVATION", _dict(intelligence.get("social"))),
    ):
        source = source_map.get(name, fallback)
        stamp = _time(source.get("as_of") or source.get("content_as_of"))
        observed = stamp is not None and stamp <= now
        status = str(source.get("status") or "NOT_CONNECTED")
        available = status in {"AVAILABLE", "PARTIAL"}
        note = (
            "Timestamp records content age, not feed completeness."
            if kind == "CONTENT" else
            "Provider observation time is not the filing date or a guarantee of current coverage."
            if kind == "PROVIDER_OBSERVATION" else
            "Completed daily history; not a live quote."
        )
        if not available:
            note = "This feed is missing or unavailable; it does not confirm the research view."
        elif not observed:
            note = "Source age is unknown; no freshness claim is made."
        observations.append(SourceObservation(
            name=name, status=status, as_of=stamp,
            age_seconds=(now - stamp).total_seconds() if observed else None,
            timestamp_kind=kind, freshness="NOT_ASSESSED" if available and observed else "UNKNOWN",
            note=note,
        ))
    return observations


def summarize_dossier(dossier: dict, *, now: datetime) -> dict:
    """Condense the complete /api/dossier payload, never an execution signal."""
    now = _now(now)
    analysis = _dict(dossier.get("analysis"))
    intelligence = _dict(dossier.get("intelligence"))
    multi = _dict(intelligence.get("multi_brain"))
    symbol = str(analysis.get("symbol") or _dict(dossier.get("security")).get("symbol") or "Unknown")
    observed = _time(analysis.get("as_of"))
    freshness: Freshness = "UNKNOWN"
    if observed is not None and observed <= now:
        try:
            # Yahoo daily bars are session-date stamped, not always close-time stamped.
            session = MarketClock().latest_completed_session_close(now).astimezone(ET).date()
            observed_day = observed.astimezone(ET).date()
            freshness = "CURRENT" if observed_day == session else "STALE"
            if observed_day > session:
                freshness = "UNKNOWN"
        except ValueError:
            pass
    trend = _trend(_dict(analysis.get("consensus")).get("label"))
    blockers = []
    if freshness != "CURRENT":
        blockers.append("Wait for the latest completed trading day's price history.")
    if trend == "UNKNOWN":
        blockers.append("The stock's daily trend is unavailable.")
    if not _forecast_ready(analysis):
        blockers.append("Historical forward context has not cleared the sample and validation checks.")
    if multi.get("status") != "RESEARCH_ONLY" or multi.get("posture") not in {"BUY", "SELL", "HOLD"}:
        blockers.append("Price and business evidence are insufficient for a combined research view.")
    if multi.get("critical_blockers"):
        blockers.append("The independent research checks still have unresolved gaps.")
    fundamentals = _dict(intelligence.get("fundamentals"))
    metrics = fundamentals.get("metrics")
    if (
        (_number(fundamentals.get("coverage")) or 0) < 3
        or not isinstance(metrics, list)
        or len(metrics) < 3
        or _number(fundamentals.get("score")) is None
    ):
        blockers.append("At least three filed business metrics are needed for a combined view.")
    view = multi.get("posture") if not blockers else "WAIT"
    headline = {
        "BUY": "Buy-side research · not a trade instruction",
        "SELL": "Sell-side research · not an instruction to sell holdings",
        "HOLD": "Hold · no clear new opportunity",
        "WAIT": "Wait · more evidence is needed",
    }[view]
    reasons = [{
        "UP": "The completed daily price trend points up.",
        "DOWN": "The completed daily price trend points down.",
        "SIDEWAYS": "The daily trend is mixed; there is no clear direction.",
        "UNKNOWN": "There is not enough verified price history to describe the trend.",
    }[trend]]
    reasons.append("This research uses daily history, not a same-day or 2–5-day trade forecast.")
    reasons.append("Hold means no new opportunity; the app has not assessed your existing holdings.")
    reasons.append("Research update time records packet assembly; source ages and missing feeds are shown separately.")
    references = _dict(analysis.get("key_levels"))
    levels = [_level(key, label, references.get(key)) for key, label in (
        ("last_close", "Last completed close"),
        ("breakout_confirmation_55d", "Price to watch for a breakout"),
        ("invalidation_reference_20d", "Prior 20-day low · risk reference"),
        ("sma_200", "Long-term average price"),
    )]
    coverage = _coverage("DAILY_CONTEXT")
    status = "RESEARCH_ONLY" if not blockers else "UNAVAILABLE"
    if freshness == "STALE":
        status = "STALE"
    return DecisionSummary(
        symbol=symbol, horizon="DAILY_CONTEXT", horizon_label=coverage.label,
        horizon_coverage=coverage.coverage, horizon_note=coverage.note,
        horizons=[_coverage(horizon) for horizon in ("0DTE", "SWING", "LEAPS")],
        trend=trend, research_view=view, next_step="TRACK_ONLY" if not blockers else "WAIT",
        status=status, headline=headline, reasons=reasons, blockers=blockers,
        levels=[level for level in levels if level is not None], as_of=observed,
        data_as_of=observed, age_seconds=max(0, (now - observed).total_seconds()) if observed else None,
        freshness=freshness, research_generated_at=_time(intelligence.get("generated_at")),
        source_observations=_source_observations(intelligence, analysis, now),
    ).model_dump(mode="json")
