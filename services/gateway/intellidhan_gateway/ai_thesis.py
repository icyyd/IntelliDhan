"""Optional OpenAI-powered thesis synthesis for deterministic smart plays."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6-luna"


class AIThesisUnavailable(RuntimeError):
    """Raised when the optional AI research provider cannot serve a request."""


THESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["RESEARCH", "WATCH", "AVOID"]},
        "assessment": {"type": "string", "enum": ["SUPPORTED", "MIXED", "WEAK"]},
        "confidence": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
        "primary_evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 3,
        },
        "risk_evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
        "research_focus": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "EARNINGS_CALENDAR",
                    "RECENT_NEWS",
                    "SECTOR_CONTEXT",
                    "FUNDAMENTAL_QUALITY",
                    "VALUATION",
                    "LIQUIDITY",
                ],
            },
            "minItems": 1,
            "maxItems": 3,
        },
    },
    "required": [
        "verdict",
        "assessment",
        "confidence",
        "primary_evidence_ids",
        "risk_evidence_ids",
        "research_focus",
    ],
}

VERDICTS = {"RESEARCH", "WATCH", "AVOID"}
ASSESSMENTS = {"SUPPORTED", "MIXED", "WEAK"}
CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
RESEARCH_FOCUS = {
    "EARNINGS_CALENDAR": "Check the next earnings date and event risk.",
    "RECENT_NEWS": "Review recent company news from primary sources.",
    "SECTOR_CONTEXT": "Compare the setup with its sector and close peers.",
    "FUNDAMENTAL_QUALITY": "Verify profitability, balance-sheet, and cash-flow quality.",
    "VALUATION": "Compare valuation with its history and close peers.",
    "LIQUIDITY": "Confirm current spread, depth, and executable liquidity.",
}


def _output_text(payload: dict[str, Any]) -> tuple[str, bool]:
    texts: list[str] = []
    web_search_used = False
    for item in payload.get("output", []):
        if item.get("type") == "web_search_call":
            web_search_used = True
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") != "output_text":
                continue
            texts.append(str(content.get("text", "")))
    return "".join(texts), web_search_used


def _fact_value(fact: dict[str, Any]) -> str:
    value = fact.get("value")
    if value is None:
        return "unavailable"
    if fact.get("unit") == "percent":
        return f"{value}%"
    if fact.get("unit") == "percentile":
        return f"{value} percentile"
    if fact.get("unit") == "ratio":
        return f"{value}x"
    return str(value)


def _validate_selection(selection: Any, allowed_ids: set[str]) -> dict[str, Any]:
    required = set(THESIS_SCHEMA["required"])
    if not isinstance(selection, dict) or set(selection) != required:
        raise AIThesisUnavailable("AI thesis selection violated the closed output contract")
    if selection.get("verdict") not in VERDICTS:
        raise AIThesisUnavailable("AI thesis returned an unsupported verdict")
    if selection.get("assessment") not in ASSESSMENTS:
        raise AIThesisUnavailable("AI thesis returned an unsupported assessment")
    if selection.get("confidence") not in CONFIDENCE:
        raise AIThesisUnavailable("AI thesis returned unsupported confidence")
    for key, minimum, maximum in (
        ("primary_evidence_ids", 1, 3),
        ("risk_evidence_ids", 0, 3),
        ("research_focus", 1, 3),
    ):
        values = selection.get(key)
        if (
            not isinstance(values, list)
            or not minimum <= len(values) <= maximum
            or not all(isinstance(value, str) for value in values)
        ):
            raise AIThesisUnavailable("AI thesis selection violated the closed output contract")
    selected_ids = set(selection["primary_evidence_ids"] + selection["risk_evidence_ids"])
    if not selected_ids.issubset(allowed_ids):
        raise AIThesisUnavailable("AI thesis selected evidence outside supplied candidate facts")
    if not set(selection["research_focus"]).issubset(RESEARCH_FOCUS):
        raise AIThesisUnavailable("AI thesis selected an unsupported research focus")
    return selection


def _render_thesis(
    candidate: dict[str, Any], play: dict[str, Any], selection: dict[str, Any]
) -> dict[str, Any]:
    evidence = {str(item["id"]): item for item in play.get("evidence", [])}
    verdict = selection["verdict"]
    if selection["assessment"] == "WEAK":
        verdict = "AVOID"
    elif str(play.get("status", "")).startswith("WAIT") and verdict == "RESEARCH":
        verdict = "WATCH"
    confidence = "MEDIUM" if selection["confidence"] == "HIGH" else selection["confidence"]
    assessment_text = {
        "SUPPORTED": "supported by the selected technical evidence",
        "MIXED": "mixed and needs additional confirmation",
        "WEAK": "weak relative to the available evidence",
    }[selection["assessment"]]
    verdict_text = {
        "RESEARCH": "merits deeper research",
        "WATCH": "needs confirmation before deeper consideration",
        "AVOID": "does not currently merit further action",
    }[verdict]
    why_now = [
        f"{evidence[fact_id]['label']}: {_fact_value(evidence[fact_id])}. "
        f"{evidence[fact_id]['interpretation']}"
        for fact_id in selection["primary_evidence_ids"]
    ]
    risks = [
        f"Monitor {evidence[fact_id]['label'].lower()}: {_fact_value(evidence[fact_id])}."
        for fact_id in selection["risk_evidence_ids"]
    ]
    risks.extend(
        [
            "Historical patterns can fail or reverse without warning.",
            "Fundamentals, scheduled events, and gap risk are not included in this rank.",
            "The percentile rank covers only the configured universe, not the full market.",
        ]
    )
    return {
        "verdict": verdict,
        "headline": f"{play['label']} {verdict_text}",
        "thesis": (
            f"{candidate['symbol']}'s {play['label'].lower()} is {assessment_text}. "
            "This is a research candidate, not an execution signal."
        ),
        "why_now": why_now,
        "risks": risks[:4],
        "confirmation": play.get("confirmation"),
        "invalidation": play.get("invalidation"),
        "time_horizon": play.get("horizon"),
        "confidence": confidence,
        "evidence_ids": selection["primary_evidence_ids"],
        "research_next": [RESEARCH_FOCUS[item] for item in selection["research_focus"]],
    }


class OpenAIThesisService:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_SMART_SCAN_MODEL", DEFAULT_MODEL)
        self._client = client

    async def generate(
        self,
        candidate: dict[str, Any],
        *,
        user_id: str,
        web_research: bool = False,
    ) -> dict[str, Any]:
        if web_research:
            raise ValueError(
                "AI web research is disabled until inline claim-level citations are implemented"
            )
        if not self.api_key:
            raise AIThesisUnavailable(
                "AI thesis generation is not configured; deterministic play evidence is still available"
            )
        play = candidate.get("selected_play") or candidate.get("best_play") or {}
        if not play.get("eligible"):
            raise ValueError("the selected symbol does not have an eligible smart-play setup")

        evidence = {
            "symbol": candidate.get("symbol"),
            "as_of": candidate.get("as_of"),
            "universe_scope": "configured-live",
            "universe_size_note": "Ranks are valid only within the server-configured universe.",
            "setup": play,
            "risk_metrics": {
                key: candidate.get(key)
                for key in (
                    "price",
                    "atr_14_pct",
                    "realized_volatility_20d_pct",
                    "max_drawdown_1y_pct",
                    "average_dollar_volume_20d",
                    "prior_20d_low",
                )
            },
        }
        developer_prompt = (
            "You are IntelliDhan's evidence selector. Return only the closed structured fields. "
            "Do not write narrative, prices, claims, recommendations, targets, or instructions. "
            "Choose only supplied evidence IDs and fixed research-focus enums. The server, not "
            "you, renders all user-visible prose and numeric facts. Use WATCH when confirmation "
            "is pending, RESEARCH for a supported setup worth diligence, and AVOID when the "
            "available evidence is weak. Never override the deterministic rank."
        )
        user_prompt = "Analyze this candidate evidence:\n" + json.dumps(
            evidence, separators=(",", ":"), sort_keys=True
        )
        request_body: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "developer", "content": developer_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "reasoning": {"effort": "low"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "smart_play_thesis",
                    "schema": THESIS_SCHEMA,
                    "strict": True,
                }
            },
            "max_output_tokens": 1600,
            "store": False,
            "safety_identifier": hashlib.sha256(
                f"intellidhan:{user_id}".encode()
            ).hexdigest()[:32],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=40.0)
        try:
            response = await client.post(
                OPENAI_RESPONSES_URL, headers=headers, json=request_body
            )
            response.raise_for_status()
            payload = response.json()
            raw, web_search_used = _output_text(payload)
            selection = json.loads(raw)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise AIThesisUnavailable(
                "AI thesis generation is temporarily unavailable; deterministic evidence is unchanged"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if web_search_used:
            raise AIThesisUnavailable("AI provider used a web tool that was not enabled")
        allowed_ids = {str(item.get("id")) for item in play.get("evidence", [])}
        selection = _validate_selection(selection, allowed_ids)
        thesis = _render_thesis(candidate, play, selection)
        return {
            "status": "READY",
            "symbol": candidate.get("symbol"),
            "as_of": candidate.get("as_of"),
            "setup_key": play.get("key"),
            "model": self.model,
            "response_id": payload.get("id"),
            "web_research": False,
            "web_search_used": False,
            "thesis": thesis,
            "guardrails": {
                "changes_deterministic_rank": False,
                "execution_eligible": False,
                "numeric_facts_source": "server-supplied completed daily evidence",
                "prose_source": "server templates",
                "web_search_enabled": False,
            },
        }
