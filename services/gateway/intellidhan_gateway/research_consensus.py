"""Evidence-isolated stock research specialists and deterministic reconciliation.

This adapts the blind-review pattern used by intellidhan-daily-brief without
making a language model an execution authority. Each specialist receives only
its own evidence domain. The reconciler is fixed code and returns a research
posture, never an order or a probability of profit.
"""

from __future__ import annotations

from typing import Any


CONSENSUS_VERSION = "multibrain-research-v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _stance(score: float | None, bullish: float = 65.0, bearish: float = 35.0) -> str:
    if score is None:
        return "UNAVAILABLE"
    if score >= bullish:
        return "BULLISH"
    if score <= bearish:
        return "BEARISH"
    return "NEUTRAL"


def technical_score(analysis: dict[str, Any]) -> float | None:
    """Map transparent method votes and volatility to a bounded research score."""
    consensus = analysis.get("consensus", {})
    net_vote = _number(consensus.get("net_vote"))
    if net_vote is None:
        return None
    score = 50.0 + net_vote * 11.0
    volatility = _number(
        analysis.get("risk", {}).get("realized_volatility_20d_annualized_pct")
    )
    # Extreme realized volatility is a risk penalty, not a directional vote.
    if volatility is not None and volatility > 65.0:
        score -= min(15.0, (volatility - 65.0) * 0.3)
    return round(max(0.0, min(100.0, score)), 1)


def _technical_brain(analysis: dict[str, Any]) -> dict[str, Any]:
    score = technical_score(analysis)
    consensus = analysis.get("consensus", {})
    forecast = analysis.get("forecast", {})
    horizons = forecast.get("horizons", {})
    validated = [
        name
        for name, row in horizons.items()
        if row.get("walk_forward_validation", {}).get("status") == "OUTPERFORMS_BASE"
    ]
    evidence = [
        f"trend:{consensus.get('label', 'UNAVAILABLE')}",
        f"net_vote:{consensus.get('net_vote', 'UNAVAILABLE')}",
    ]
    blockers = []
    if forecast.get("strategy_context_status") != "VALIDATED_CONTEXT":
        blockers.append("Forward context has not beaten its expanding base-rate benchmark.")
    return {
        "key": "PRICE_RISK",
        "label": "Price & risk",
        "stance": _stance(score),
        "score": score,
        "confidence": "MODERATE" if validated else "LOW",
        "evidence": evidence,
        "blockers": blockers,
        "evidence_scope": "completed adjusted bars only",
    }


def _fundamental_brain(intelligence: dict[str, Any]) -> dict[str, Any]:
    fundamental = intelligence.get("fundamentals", {})
    score = _number(fundamental.get("score"))
    coverage = int(_number(fundamental.get("coverage")) or 0)
    evidence = [
        f"{item.get('key')}:{item.get('value')}"
        for item in fundamental.get("metrics", [])
        if item.get("key")
    ]
    blockers = []
    if coverage < 3:
        blockers.append("Fewer than three comparable filed financial metrics are available.")
    return {
        "key": "BUSINESS_QUALITY",
        "label": "Business quality",
        "stance": _stance(score, bullish=62.0, bearish=38.0),
        "score": score,
        "confidence": "MODERATE" if coverage >= 4 else "LOW",
        "evidence": evidence[:6],
        "blockers": blockers,
        "evidence_scope": "current SEC filing facts only",
    }


def _attention_brain(intelligence: dict[str, Any]) -> dict[str, Any]:
    news = intelligence.get("news", {})
    social = intelligence.get("social", {})
    values = [value for value in (_number(news.get("score")), _number(social.get("score"))) if value is not None]
    score = round(sum(values) / len(values), 1) if values else None
    evidence = []
    if news.get("article_count") is not None:
        evidence.append(f"news_articles:{news.get('article_count')}")
    if social.get("mentions") is not None:
        evidence.append(f"social_mentions:{social.get('mentions')}")
    return {
        "key": "CATALYST_ATTENTION",
        "label": "Catalyst & attention",
        "stance": _stance(score, bullish=68.0, bearish=32.0),
        "score": score,
        "confidence": "LOW",
        "evidence": evidence,
        "blockers": [] if values else ["No current news or social score is available."],
        "evidence_scope": "recent provider tone and attention only",
    }


def build_research_consensus(
    analysis: dict[str, Any], intelligence: dict[str, Any]
) -> dict[str, Any]:
    """Return blind specialist assessments and a fail-closed research posture."""
    specialists = [
        _technical_brain(analysis),
        _fundamental_brain(intelligence),
        _attention_brain(intelligence),
    ]
    by_key = {item["key"]: item for item in specialists}
    technical = by_key["PRICE_RISK"]
    fundamental = by_key["BUSINESS_QUALITY"]
    available = [item for item in specialists if item["stance"] != "UNAVAILABLE"]
    bullish = [item["key"] for item in available if item["stance"] == "BULLISH"]
    bearish = [item["key"] for item in available if item["stance"] == "BEARISH"]
    conflicts = bool(bullish and bearish)
    critical_blockers = []
    if technical["stance"] == "UNAVAILABLE":
        critical_blockers.append("Completed-bar technical evidence is unavailable.")
    if fundamental["stance"] == "UNAVAILABLE" or len(fundamental["evidence"]) < 3:
        critical_blockers.append("Business-quality coverage is insufficient for a combined posture.")

    if critical_blockers:
        posture = "INSUFFICIENT_EVIDENCE"
        confidence = "LOW"
        summary = "Wait for adequate price and filed business evidence."
    elif (
        technical["stance"] == "BULLISH"
        and fundamental["stance"] in {"BULLISH", "NEUTRAL"}
        and not bearish
        and len(bullish) >= 2
    ):
        posture = "BUY"
        confidence = "MODERATE"
        summary = "Price and business evidence agree constructively; confirm the stated levels."
    elif (
        technical["stance"] == "BEARISH"
        and fundamental["stance"] in {"BEARISH", "NEUTRAL"}
        and not bullish
        and len(bearish) >= 2
    ):
        posture = "SELL"
        confidence = "MODERATE"
        summary = "Price and business evidence agree defensively; avoid assuming a rebound."
    else:
        posture = "HOLD"
        confidence = "LOW" if conflicts else "MODERATE"
        summary = (
            "Specialists conflict, so conviction is reduced."
            if conflicts
            else "The available specialists do not yet form a qualified buy or sell agreement."
        )

    return {
        "version": CONSENSUS_VERSION,
        "status": "RESEARCH_ONLY",
        "posture": posture,
        "confidence": confidence,
        "summary": summary,
        "agreement": {
            "available_specialists": len(available),
            "bullish": bullish,
            "bearish": bearish,
            "conflict": conflicts,
        },
        "critical_blockers": critical_blockers,
        "specialists": specialists,
        "policy": (
            "Research posture only. It cannot rank the universe, create a broker intent, "
            "size a position, or change execution mode."
        ),
    }

