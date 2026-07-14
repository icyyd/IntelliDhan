"""Deterministic smart-play and optional OpenAI synthesis contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import intellidhan_gateway.app as gateway
from intellidhan_analytics.smart_scan import rank_smart_plays
from intellidhan_gateway.ai_thesis import AIThesisUnavailable, OpenAIThesisService
from intellidhan_learning.research_smart_scan import backtest_smart_momentum


def candidate_row(symbol: str, momentum: float, *, breakout_distance: float = -2.0) -> dict:
    return {
        "symbol": symbol,
        "as_of": "2026-07-10T20:00:00+00:00",
        "price": 100.0,
        "return_12_1_pct": momentum,
        "return_6_1_pct": momentum / 2,
        "percent_of_52w_high": 96.0,
        "realized_volatility_20d_pct": 20.0,
        "distance_from_sma_200_pct": 12.0,
        "sma_200_slope_20d_pct": 2.0,
        "distance_from_sma_50_pct": 1.0,
        "distance_from_52w_high_pct": -4.0,
        "distance_to_prior_55d_high_pct": breakout_distance,
        "volume_ratio_5d_to_prior_20d": 1.4,
        "volatility_ratio_20d_to_60d": 0.75,
        "atr_14_pct": 2.0,
        "max_drawdown_1y_pct": -12.0,
        "average_dollar_volume_20d": 50_000_000,
        "prior_20d_low": 94.0,
    }


def test_smart_scan_uses_cross_universe_momentum_and_auditable_rules():
    rows = rank_smart_plays(
        [candidate_row("SLOW", 10.0), candidate_row("FAST", 35.0)]
    )
    assert [row["symbol"] for row in rows] == ["FAST", "SLOW"]
    fast = rows[0]
    play = fast["plays"]["MOMENTUM_LEADER"]
    assert play["eligible"] is True
    slow = next(row for row in rows if row["symbol"] == "SLOW")
    assert play["score"] > slow["plays"]["MOMENTUM_LEADER"]["score"]
    assert fast["universe_percentiles"]["return_12_1_pct"] == 100.0
    assert {fact["id"] for fact in play["evidence"]} == {
        "return_12_1",
        "momentum_rank",
        "sma200_slope",
    }
    assert "200-day" in play["invalidation"]


def test_smart_scan_breakout_requires_volume_for_confirmation():
    confirmed = rank_smart_plays([candidate_row("TEST", 25.0, breakout_distance=0.5)])[0]
    assert confirmed["plays"]["BREAKOUT_WATCH"]["status"] == "CONFIRMED"
    unconfirmed_input = candidate_row("TEST", 25.0, breakout_distance=0.5)
    unconfirmed_input["volume_ratio_5d_to_prior_20d"] = 0.9
    unconfirmed = rank_smart_plays([unconfirmed_input])[0]
    assert unconfirmed["plays"]["BREAKOUT_WATCH"]["status"] == "WAIT_FOR_CONFIRMATION"


def test_smart_scan_does_not_surface_illiquid_names():
    row = candidate_row("THIN", 50.0, breakout_distance=0.5)
    row["average_dollar_volume_20d"] = 1_000_000
    ranked = rank_smart_plays([row])[0]
    assert ranked["best_play"]["key"] == "NO_SETUP"
    assert ranked["plays"]["BREAKOUT_WATCH"]["status"] == "NO_SETUP"


@pytest.mark.asyncio
async def test_ai_thesis_uses_strict_output_and_preserves_execution_boundary():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["text"]["format"]["strict"] is True
        assert "tools" not in body
        assert body["store"] is False
        output = {
            "verdict": "RESEARCH",
            "assessment": "SUPPORTED",
            "confidence": "MEDIUM",
            "primary_evidence_ids": ["return_12_1", "momentum_rank"],
            "risk_evidence_ids": ["sma200_slope"],
            "research_focus": ["EARNINGS_CALENDAR", "FUNDAMENTAL_QUALITY"],
        }
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(output),
                            }
                        ],
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAIThesisService(api_key="test", model="test-model", client=client)
    candidate = rank_smart_plays([candidate_row("TEST", 25.0)])[0]
    result = await service.generate(candidate, user_id="user-1")
    await client.aclose()
    assert result["response_id"] == "resp_test"
    assert result["web_search_used"] is False
    assert "25.0%" in result["thesis"]["why_now"][0]
    assert result["thesis"]["confirmation"] == candidate["best_play"]["confirmation"]
    assert result["thesis"]["research_next"][0].startswith("Check the next earnings")
    assert result["guardrails"]["changes_deterministic_rank"] is False
    assert result["guardrails"]["execution_eligible"] is False
    assert result["guardrails"]["prose_source"] == "server templates"


@pytest.mark.asyncio
async def test_ai_thesis_fails_cleanly_without_api_key():
    service = OpenAIThesisService(api_key="")
    candidate = rank_smart_plays([candidate_row("TEST", 25.0)])[0]
    with pytest.raises(AIThesisUnavailable, match="not configured"):
        await service.generate(candidate, user_id="user-1")


@pytest.mark.asyncio
async def test_ai_thesis_rejects_web_research_before_provider_call():
    service = OpenAIThesisService(api_key="test")
    candidate = rank_smart_plays([candidate_row("TEST", 25.0)])[0]
    with pytest.raises(ValueError, match="inline claim-level citations"):
        await service.generate(candidate, user_id="user-1", web_research=True)


@pytest.mark.asyncio
async def test_ai_thesis_rejects_provider_prose_even_with_allowed_evidence_id():
    malicious = {
        "verdict": "RESEARCH",
        "assessment": "SUPPORTED",
        "confidence": "HIGH",
        "primary_evidence_ids": ["return_12_1"],
        "risk_evidence_ids": [],
        "research_focus": ["RECENT_NEWS"],
        "headline": "Buy now for a target price",
    }

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_malicious",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(malicious)}
                        ],
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = OpenAIThesisService(api_key="test", client=client)
    candidate = rank_smart_plays([candidate_row("TEST", 25.0)])[0]
    with pytest.raises(AIThesisUnavailable, match="closed output contract"):
        await service.generate(candidate, user_id="user-1")
    await client.aclose()


def test_walk_forward_momentum_selection_does_not_use_future_jump():
    dates = pd.bdate_range("2024-01-01", periods=340)
    frame = pd.DataFrame(
        {
            "SPY": [100 + index * 0.12 for index in range(len(dates))],
            "STEADY": [100 + index * 0.25 for index in range(len(dates))],
            "FUTURE": [100 + index * 0.05 for index in range(len(dates))],
        },
        index=dates,
    )
    # This jump happens after the first signal date and must not affect that rank.
    frame.loc[dates[270]:, "FUTURE"] *= 2
    report = backtest_smart_momentum(frame, top_n=1)
    assert report["rebalances"][0]["symbols"] == ["STEADY"]
    assert report["execution"].startswith("signal at close; rebalance at next close")
    assert report["promotion_status"] == "RESEARCH_ONLY"


def test_walk_forward_keeps_initial_cash_regime_in_fixed_evaluation_window():
    dates = pd.bdate_range("2023-01-02", periods=360)
    # All assets fall through the first scheduled signal, then recover enough
    # for a later position. The evaluation must still begin at session 253.
    down_then_up = [
        200 - index * 0.2 if index < 260 else 300 + (index - 260) * 1.0
        for index in range(len(dates))
    ]
    frame = pd.DataFrame(
        {
            "SPY": [200 - index * 0.1 for index in range(len(dates))],
            "RECOVERY": down_then_up,
        },
        index=dates,
    )
    report = backtest_smart_momentum(frame, top_n=1)
    assert report["rebalances"][0]["symbols"] == []
    assert any(item["symbols"] for item in report["rebalances"][1:])
    assert report["evaluation_start"] == str(dates[253].date())
    assert report["observations"] == len(dates) - 253


def test_thesis_endpoint_reloads_server_candidate_and_forwards_no_client_metrics(monkeypatch):
    candidate = rank_smart_plays([candidate_row("TEST", 25.0)])[0]

    class Discovery:
        async def universe_scan(self):
            return {"rows": [candidate], "complete": True}

    class Thesis:
        def __init__(self):
            self.calls = []

        async def generate(self, row, **kwargs):
            self.calls.append((row, kwargs))
            return {"status": "READY", "symbol": row["symbol"]}

    thesis = Thesis()
    monkeypatch.setattr(gateway, "discovery", Discovery())
    monkeypatch.setattr(gateway, "ai_thesis_service", thesis)
    monkeypatch.setattr(
        gateway, "_require_personal", lambda _request: SimpleNamespace(user_id="user-1")
    )
    monkeypatch.setattr(gateway, "rate_limiter", type(gateway.rate_limiter)())
    response = TestClient(gateway.app).post(
        "/api/discover/thesis",
        json={
            "symbol": "test",
            "play_key": "MOMENTUM_LEADER",
            "web_research": False,
            "price": 999999,
        },
    )
    assert response.status_code == 200
    assert thesis.calls[0][0]["price"] == 100.0
    assert thesis.calls[0][0]["selected_play"]["key"] == "MOMENTUM_LEADER"
    assert thesis.calls[0][1] == {"user_id": "user-1", "web_research": False}


def test_thesis_endpoint_rejects_web_research_before_scan(monkeypatch):
    monkeypatch.setattr(
        gateway, "_require_personal", lambda _request: SimpleNamespace(user_id="user-1")
    )
    monkeypatch.setattr(gateway, "rate_limiter", type(gateway.rate_limiter)())
    response = TestClient(gateway.app).post(
        "/api/discover/thesis", json={"symbol": "TEST", "web_research": True}
    )
    assert response.status_code == 422
    assert "inline claim-level citations" in response.json()["detail"]


def test_thesis_endpoint_fails_closed_on_partial_universe_rank(monkeypatch):
    class PartialDiscovery:
        async def universe_scan(self):
            return {
                "rows": [rank_smart_plays([candidate_row("TEST", 25.0)])[0]],
                "complete": False,
                "errors": {"MISSING": "provider unavailable"},
            }

    monkeypatch.setattr(gateway, "discovery", PartialDiscovery())
    monkeypatch.setattr(
        gateway, "_require_personal", lambda _request: SimpleNamespace(user_id="user-1")
    )
    monkeypatch.setattr(gateway, "rate_limiter", type(gateway.rate_limiter)())
    response = TestClient(gateway.app).post(
        "/api/discover/thesis", json={"symbol": "TEST"}
    )
    assert response.status_code == 503
    assert "complete configured-universe scan" in response.json()["detail"]
