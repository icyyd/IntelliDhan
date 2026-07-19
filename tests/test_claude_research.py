"""Claude multi-brain research review remains bounded and execution-isolated."""

from __future__ import annotations

import json

import httpx
import pytest

from intellidhan_gateway.claude_research import (
    ANTHROPIC_MESSAGES_URL,
    ClaudeResearchReviewer,
    ClaudeResearchUnavailable,
    build_claude_research_packet,
)
from intellidhan_gateway.research_consensus import build_research_consensus


def analysis() -> dict:
    return {
        "symbol": "AAPL",
        "as_of": "2026-07-17T20:00:00Z",
        "price": 210.0,
        "consensus": {"label": "STRONG_UPTREND", "net_vote": 4},
        "methods": {"sma_200_regime": {"signal": "BULLISH"}},
        "key_levels": {"last_close": 210.0, "sma_200": 185.0},
        "risk": {
            "atr14": 4.0,
            "realized_volatility_20d_annualized_pct": 25.0,
            "risk_budget": "PRIVATE-CAPITAL-SENTINEL",
            "reference_quantity": "PRIVATE-QUANTITY-SENTINEL",
        },
        "forecast": {
            "strategy_context_status": "VALIDATED_CONTEXT",
            "horizons": {
                "one_month": {
                    "label": "FAVORABLE",
                    "walk_forward_validation": {"status": "OUTPERFORMS_BASE"},
                }
            },
        },
    }


def intelligence() -> dict:
    return {
        "company": {
            "name": "Apple Inc.",
            "description": "Consumer technology company.",
            "sector": "Technology",
        },
        "fundamentals": {
            "status": "AVAILABLE",
            "score": 75,
            "coverage": 5,
            "metrics": [
                {"key": "revenue_growth", "value": 10},
                {"key": "gross_margin", "value": 45},
                {"key": "net_margin", "value": 20},
                {"key": "fcf_margin", "value": 18},
                {"key": "liabilities_to_assets", "value": 40},
            ],
        },
        "filings": {"status": "AVAILABLE", "items": [{"form": "10-Q"}]},
        "news": {
            "status": "AVAILABLE",
            "score": 60,
            "article_count": 2,
            "headlines": [{"title": "Product update", "source": "Example"}],
        },
        "social": {"status": "AVAILABLE", "score": 55, "mentions": 20},
    }


def consensus() -> dict:
    return build_research_consensus(analysis(), intelligence())


def claude_payload(*, evidence_ids: list[str] | None = None, stop_reason="end_turn"):
    review = {
        "verdict": "SUPPORTS_POSTURE",
        "confidence": "MODERATE",
        "summary": "The supplied domains broadly agree, with valuation still worth checking.",
        "evidence_ids": evidence_ids or [
            "DETERMINISTIC_POSTURE",
            "DOMAIN_SPECIALISTS",
        ],
        "risks": ["Current attention evidence can reverse quickly."],
        "open_questions": ["How sensitive is the thesis to margin compression?"],
    }
    return {
        "id": "msg_test",
        "model": "claude-sonnet-5",
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": json.dumps(review)}],
    }


def test_packet_contains_public_research_only_and_stable_evidence_ids():
    packet = build_claude_research_packet("AAPL", analysis(), intelligence(), consensus())
    assert packet["research_only"] is True
    assert [item["id"] for item in packet["facts"]] == [
        "DETERMINISTIC_POSTURE",
        "DOMAIN_SPECIALISTS",
        "COMPLETED_BAR_TECHNICALS",
        "FORWARD_VALIDATION",
        "COMPANY_PROFILE",
        "FILED_FUNDAMENTALS",
        "RECENT_FILINGS",
        "RECENT_NEWS",
        "SOCIAL_ATTENTION",
    ]
    serialized = json.dumps(packet).lower()
    assert "autotrade" not in serialized
    assert "robinhood" not in serialized
    assert "api_key" not in serialized
    assert "risk_budget" not in serialized
    assert "reference_quantity" not in serialized
    assert "private-capital-sentinel" not in serialized
    assert "private-quantity-sentinel" not in serialized


@pytest.mark.asyncio
async def test_missing_key_is_visible_without_provider_call():
    called = False

    async def handler(request):
        nonlocal called
        called = True
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = ClaudeResearchReviewer(api_key="", client=client)
        result = await reviewer.review("AAPL", analysis(), intelligence(), consensus())
    assert result["status"] == "NOT_CONFIGURED"
    assert result["guardrails"]["broker_access"] is False
    assert called is False


@pytest.mark.asyncio
async def test_claude_review_uses_structured_output_without_tools_and_caches():
    calls = 0

    async def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        assert request.url == ANTHROPIC_MESSAGES_URL
        assert request.headers["x-api-key"] == "server-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["output_config"]["effort"] == "low"
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
        assert "tools" not in body
        assert "mcp_servers" not in body
        assert "untrusted data" in body["system"]
        return httpx.Response(200, json=claude_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = ClaudeResearchReviewer(
            api_key="server-secret",
            model="test-model",
            client=client,
        )
        first = await reviewer.review("AAPL", analysis(), intelligence(), consensus())
        second = await reviewer.review("AAPL", analysis(), intelligence(), consensus())

    assert first == second
    assert calls == 1
    assert first["status"] == "READY"
    assert first["verdict"] == "SUPPORTS_POSTURE"
    assert first["guardrails"] == {
        "changes_deterministic_posture": False,
        "changes_rank": False,
        "execution_eligible": False,
        "tools_enabled": False,
        "broker_access": False,
    }
    assert "server-secret" not in json.dumps(first)


@pytest.mark.asyncio
async def test_claude_cannot_cite_evidence_outside_packet():
    async def handler(request):
        return httpx.Response(200, json=claude_payload(evidence_ids=["BROKER_POSITION"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = ClaudeResearchReviewer(api_key="test", client=client)
        with pytest.raises(ClaudeResearchUnavailable, match="outside the supplied packet"):
            await reviewer.review("AAPL", analysis(), intelligence(), consensus())


@pytest.mark.asyncio
async def test_claude_rejects_non_string_structured_items():
    payload = claude_payload()
    review = json.loads(payload["content"][0]["text"])
    review["risks"] = [{"not": "a string"}]
    payload["content"][0]["text"] = json.dumps(review)

    async def handler(request):
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = ClaudeResearchReviewer(api_key="test", client=client)
        with pytest.raises(ClaudeResearchUnavailable, match="invalid research review"):
            await reviewer.review("AAPL", analysis(), intelligence(), consensus())


@pytest.mark.asyncio
async def test_refusal_or_truncation_fails_without_mutating_consensus():
    original = consensus()

    async def handler(request):
        return httpx.Response(200, json=claude_payload(stop_reason="max_tokens"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = ClaudeResearchReviewer(api_key="test", client=client)
        with pytest.raises(ClaudeResearchUnavailable, match="did not complete"):
            await reviewer.review("AAPL", analysis(), intelligence(), original)

    assert original == consensus()
