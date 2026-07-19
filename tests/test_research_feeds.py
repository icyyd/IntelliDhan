from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from intellidhan_gateway.app import app, loop
from intellidhan_gateway.claude_research import ClaudeResearchReviewer
from intellidhan_gateway.research_feeds import (
    ResearchFeedService,
    parse_alpha_news,
    parse_alpha_overview,
    parse_finnhub_social,
    parse_sec_filings,
    parse_sec_fundamentals,
)
from intellidhan_gateway.terminal_store import TerminalStore

OWNER = "research-feed-owner-token-0123456789"


def annual(value, start, end, filed):
    return {
        "start": start,
        "end": end,
        "filed": filed,
        "form": "10-K",
        "fp": "FY",
        "val": value,
    }


def instant(value, end, filed, form="10-Q"):
    return {"end": end, "filed": filed, "form": form, "val": value}


def test_sec_fundamentals_produces_auditable_financial_strength_score():
    current = ("2024-01-01", "2024-12-31", "2025-02-15")
    prior = ("2023-01-01", "2023-12-31", "2024-02-15")
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": [annual(1200, *current), annual(1000, *prior)]}
                },
                "GrossProfit": {"units": {"USD": [annual(720, *current)]}},
                "NetIncomeLoss": {"units": {"USD": [annual(240, *current)]}},
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {"USD": [annual(300, *current)]}
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {"USD": [annual(60, *current)]}
                },
                "Assets": {"units": {"USD": [instant(2000, "2025-03-31", "2025-05-01")]}},
                "Liabilities": {
                    "units": {"USD": [instant(800, "2025-03-31", "2025-05-01")]}
                },
            }
        }
    }
    result = parse_sec_fundamentals(facts)
    assert result["status"] == "AVAILABLE"
    assert result["period_end"] == "2024-12-31"
    assert result["coverage"] == 5
    assert result["score"] > 70
    assert {item["key"] for item in result["metrics"]} == {
        "revenue_growth",
        "gross_margin",
        "net_margin",
        "fcf_margin",
        "liabilities_to_assets",
    }
    assert "not sector-relative" in result["limitation"]


def test_sec_balance_sheet_ratio_requires_a_matching_period():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            annual(1200, "2024-01-01", "2024-12-31", "2025-02-15"),
                            annual(1000, "2023-01-01", "2023-12-31", "2024-02-15"),
                        ]
                    }
                },
                "Assets": {
                    "units": {"USD": [instant(2000, "2025-03-31", "2025-05-01")]}
                },
                "Liabilities": {
                    "units": {"USD": [instant(800, "2024-12-31", "2025-02-15", "10-K")]}
                },
            }
        }
    }
    result = parse_sec_fundamentals(facts)
    assert "liabilities_to_assets" not in {item["key"] for item in result["metrics"]}


def test_sec_filing_links_are_allowlisted_and_bounded():
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-Q", "8-K", "S-1"],
                "accessionNumber": ["0001234567-26-000001", "bad", "0001234567-26-000003"],
                "primaryDocument": ["report.htm", "../bad.htm", "registration.htm"],
                "filingDate": ["2026-05-01", "2026-04-20", "2026-03-01"],
                "acceptanceDateTime": ["20260501120000", "20260420120000", "20260301120000"],
            }
        }
    }
    result = parse_sec_filings(submissions, "0001234567")
    assert len(result["items"]) == 1
    assert result["items"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/")
    assert ".." not in result["items"][0]["url"]


def test_news_and_social_scores_are_low_level_context_not_prose():
    news = parse_alpha_news(
        {
            "feed": [
                {
                    "title": "Results improve",
                    "source": "Example",
                    "time_published": "20260717T120000",
                    "ticker_sentiment": [
                        {
                            "ticker": "AAPL",
                            "relevance_score": "0.8",
                            "ticker_sentiment_score": "0.4",
                        }
                    ],
                }
            ]
        },
        "AAPL",
        now=datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc),
    )
    social = parse_finnhub_social(
        {
            "symbol": "AAPL",
            "reddit": [
                {
                    "atTime": "2026-07-17T19:00:00Z",
                    "mention": 10,
                    "positiveMention": 7,
                    "negativeMention": 3,
                }
            ],
        },
        "AAPL",
        now=datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc),
    )
    assert news["score"] == 70.0
    assert news["article_count"] == 1
    assert social["score"] == 70.0
    assert social["mentions"] == 10
    assert "never triggers a trade" in social["limitation"]


def test_company_overview_is_descriptive_and_keeps_estimates_display_only():
    result = parse_alpha_overview(
        {
            "Symbol": "AAPL",
            "Name": "Apple Inc",
            "Description": "Apple designs and sells consumer technology products and services.",
            "Sector": "TECHNOLOGY",
            "Industry": "CONSUMER ELECTRONICS",
            "Currency": "None",
            "MarketCapitalization": "3500000000000",
            "PERatio": "31.2",
            "AnalystTargetPrice": "250.00",
        }
    )
    assert result["status"] == "AVAILABLE"
    assert result["profile"]["market_cap"] == 3_500_000_000_000
    assert result["profile"]["analyst_target_price"] == 250.0
    assert "currency" not in result["profile"]
    assert "display-only" in result["limitation"]


def test_company_overview_rejects_a_mismatched_provider_symbol():
    result = parse_alpha_overview(
        {"Symbol": "MSFT", "Description": "Wrong company"}, "AAPL"
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["profile"] == {}


def test_social_parser_rejects_wrong_stale_and_future_observations():
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    wrong = parse_finnhub_social(
        {"symbol": "MSFT", "reddit": []}, "AAPL", now=now
    )
    assert wrong["status"] == "UNAVAILABLE"
    filtered = parse_finnhub_social(
        {
            "symbol": "AAPL",
            "reddit": [
                {"atTime": "2026-07-01T12:00:00Z", "mention": 100, "positiveMention": 100},
                {"atTime": "2026-07-18T12:00:00Z", "mention": 100, "positiveMention": 100},
            ],
        },
        "AAPL",
        now=now,
    )
    assert filtered["status"] == "UNAVAILABLE"
    assert filtered["mentions"] == 0


def test_news_parser_excludes_stale_and_malformed_articles():
    payload = {
        "feed": [
            {
                "title": "Stale",
                "source": "Example",
                "time_published": "20260601T120000",
                "ticker_sentiment": [
                    {"ticker": "AAPL", "relevance_score": "1", "ticker_sentiment_score": "1"}
                ],
            },
            {
                "title": "Malformed",
                "source": "Example",
                "time_published": "not-a-time",
                "ticker_sentiment": [
                    {"ticker": "AAPL", "relevance_score": "1", "ticker_sentiment_score": "1"}
                ],
            },
        ]
    }
    result = parse_alpha_news(
        payload,
        "AAPL",
        now=datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc),
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["article_count"] == 0
    assert result["content_as_of"] is None


@pytest.mark.asyncio
async def test_overall_rank_renormalizes_missing_pillars(monkeypatch):
    service = ResearchFeedService()

    async def sec(symbol, refresh=False):
        return {"status": "NOT_CONFIGURED", "source": "SEC EDGAR"}

    async def news(symbol, refresh=False):
        return {"status": "AVAILABLE", "score": 80.0, "tier": "A"}

    async def social(symbol, refresh=False):
        return {"status": "NOT_CONFIGURED", "source": "Finnhub"}

    monkeypatch.setattr(service, "_sec", sec)
    monkeypatch.setattr(service, "_news", news)
    monkeypatch.setattr(service, "_social", social)
    result = await service.analyze(
        "AAPL", {"symbol": "AAPL", "technical_score": 60.0, "as_of": "2026-07-17"}
    )
    assert result["overall"]["coverage_weight"] == 0.6
    assert result["overall"]["score"] == 63.3
    assert result["pillars"]["fundamentals"]["score"] is None


@pytest.mark.asyncio
async def test_provider_failure_never_reflects_alpha_vantage_key(monkeypatch):
    service = ResearchFeedService()
    secret = "alpha-secret-that-must-not-escape"
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", secret)

    async def fail(*args, **kwargs):
        raise RuntimeError(f"request failed: https://example.invalid/?apikey={secret}")

    monkeypatch.setattr(service, "_json", fail)
    result = await service._news("AAPL", refresh=True)
    assert result["status"] == "UNAVAILABLE"
    assert secret not in result["reason"]
    assert "could not be refreshed" in result["reason"]


@pytest.mark.asyncio
async def test_partial_sec_concepts_receive_partial_effective_weight(monkeypatch):
    service = ResearchFeedService()

    async def sec(symbol, refresh=False):
        return {
            "status": "AVAILABLE",
            "source": "SEC EDGAR",
            "fundamentals": {
                "status": "PARTIAL",
                "score": 100.0,
                "tier": "A",
                "coverage": 1,
            },
        }

    async def missing(symbol, refresh=False):
        return {"status": "NOT_CONFIGURED"}

    monkeypatch.setattr(service, "_sec", sec)
    monkeypatch.setattr(service, "_news", missing)
    monkeypatch.setattr(service, "_social", missing)
    result = await service.analyze("AAPL", {"technical_score": 50.0})
    assert result["pillars"]["fundamentals"]["effective_weight"] == 0.07
    assert result["overall"]["coverage_weight"] == 0.57
    assert result["overall"]["score"] == 56.1


@pytest.mark.asyncio
async def test_sec_identity_stays_authoritative_over_company_overview(monkeypatch):
    service = ResearchFeedService()

    async def sec(symbol, refresh=False):
        return {
            "status": "AVAILABLE",
            "profile": {
                "name": "SEC Registrant Name",
                "industry": "SEC Industry",
                "exchanges": ["Nasdaq"],
            },
            "fundamentals": {"status": "UNAVAILABLE", "coverage": 0},
        }

    async def overview(symbol, refresh=False):
        return {
            "status": "AVAILABLE",
            "profile": {
                "name": "Provider Alias",
                "industry": "Provider Industry",
                "description": "Plain-language business description.",
            },
        }

    async def missing(symbol, refresh=False):
        return {"status": "NOT_CONFIGURED"}

    monkeypatch.setattr(service, "_sec", sec)
    monkeypatch.setattr(service, "_overview", overview)
    monkeypatch.setattr(service, "_news", missing)
    monkeypatch.setattr(service, "_social", missing)
    result = await service.analyze("AAPL", {"technical_score": 50.0})
    assert result["company"]["name"] == "SEC Registrant Name"
    assert result["company"]["industry"] == "SEC Industry"
    assert result["company"]["description"] == "Plain-language business description."


@pytest.mark.asyncio
async def test_security_search_prioritizes_exact_ticker_then_company(monkeypatch):
    service = ResearchFeedService()

    async def mapping():
        return {
            "AAPL": {"symbol": "AAPL", "name": "Apple Inc.", "cik": "0000320193"},
            "APLE": {
                "symbol": "APLE",
                "name": "Apple Hospitality REIT, Inc.",
                "cik": "0001418121",
            },
        }

    monkeypatch.setattr(service, "_ticker_map", mapping)
    result = await service.search("aapl")
    assert result["status"] == "AVAILABLE"
    assert result["results"][0]["symbol"] == "AAPL"
    company = await service.search("apple")
    assert company["results"][0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_security_search_reports_missing_sec_configuration(monkeypatch):
    service = ResearchFeedService()

    async def missing():
        raise LookupError("INTELLIDHAN_SEC_USER_AGENT is not configured")

    monkeypatch.setattr(service, "_ticker_map", missing)
    result = await service.search("nvda")
    assert result["status"] == "NOT_CONFIGURED"
    assert result["results"] == []


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "store", TerminalStore(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", OWNER)
    from intellidhan_gateway import app as app_module

    monkeypatch.setattr(app_module, "rate_limiter", type(app_module.rate_limiter)())
    monkeypatch.setattr(
        app_module,
        "claude_research_reviewer",
        ClaudeResearchReviewer(api_key=""),
    )
    with TestClient(app) as test_client:
        assert test_client.post("/api/auth/session", json={"token": OWNER}).status_code == 200
        yield test_client


def focus_row(symbol, score, eligible=True):
    return {
        "symbol": symbol,
        "price": 100.0,
        "technical_score": score,
        "as_of": "2026-07-17T20:00:00+00:00",
        "best_play": {
            "key": "MOMENTUM_LEADER" if eligible else "NO_SETUP",
            "label": "Momentum leader" if eligible else "No current setup",
            "eligible": eligible,
            "score": score if eligible else 0.0,
            "status": "READY_TO_RESEARCH" if eligible else "NO_SETUP",
            "evidence": [],
        },
    }


def test_focus_and_intelligence_endpoints_preserve_rank_boundary(client, monkeypatch):
    from intellidhan_gateway import app as app_module

    rows = [
        focus_row("NVDA", 91),
        focus_row("AAPL", 83),
        focus_row("MSFT", 79),
        focus_row("SPY", 99),
    ]

    async def scan(refresh=False):
        return {
            "rows": rows,
            "complete": True,
            "errors": {},
            "completed_session": "2026-07-17",
        }

    async def quote(symbol):
        return SimpleNamespace(
            last={"SPX": 6500, "SPY": 650, "QQQ": 600}[symbol],
            ts=datetime.now(timezone.utc),
            source="test",
        )

    async def intelligence(symbol, technical, refresh=False):
        return {
            "symbol": symbol,
            "overall": {"score": 82.0, "tier": "A", "status": "RESEARCH_ONLY"},
            "pillars": {},
        }

    monkeypatch.setattr(app_module.discovery, "universe_scan", scan)
    monkeypatch.setattr(app_module.discovery.provider, "get_quote", quote)
    monkeypatch.setattr(app_module.research_feed_service, "analyze", intelligence)

    focus = client.get("/api/focus").json()
    assert [item["symbol"] for item in focus["anchors"]] == ["SPX", "SPY", "QQQ"]
    assert {item["status"] for item in focus["anchors"]} == {"INDICATIVE"}
    assert [item["symbol"] for item in focus["focus"]] == ["NVDA", "AAPL", "MSFT"]
    assert "cannot change rank" in focus["ai_policy"]

    profile = client.get("/api/intelligence/NVDA").json()
    assert profile["overall"]["status"] == "RESEARCH_ONLY"
    assert profile["universe_scan_complete"] is True


def test_focus_rank_fails_closed_for_partial_universe(client, monkeypatch):
    from intellidhan_gateway import app as app_module

    async def partial_scan(refresh=False):
        return {
            "rows": [focus_row("NVDA", 91), focus_row("AAPL", 83)],
            "complete": False,
            "errors": {"MSFT": "provider failed"},
            "completed_session": "2026-07-17",
        }

    async def quote(symbol):
        return SimpleNamespace(
            last=100.0,
            ts=datetime.now(timezone.utc),
            source="test",
        )

    monkeypatch.setattr(app_module.discovery, "universe_scan", partial_scan)
    monkeypatch.setattr(app_module.discovery.provider, "get_quote", quote)
    result = client.get("/api/focus").json()
    assert result["complete"] is False
    assert result["focus"] == []
    assert result["curated_watchlist"] == []
    assert result["errors"] == {"MSFT": "provider failed"}


def test_search_endpoint_forwards_bounded_company_query(client, monkeypatch):
    from intellidhan_gateway import app as app_module

    async def search(query, limit=8):
        return {
            "query": query,
            "status": "AVAILABLE",
            "results": [{"symbol": "AAPL", "name": "Apple Inc."}],
        }

    monkeypatch.setattr(app_module.research_feed_service, "search", search)
    result = client.get("/api/search?q=apple&limit=5")
    assert result.status_code == 200
    assert result.json()["results"][0]["symbol"] == "AAPL"
    assert client.get("/api/search?q=apple&limit=20").status_code == 422


def test_signed_in_dossier_includes_current_research_evidence(client, monkeypatch):
    from intellidhan_gateway import app as app_module

    async def analyze(symbol, **kwargs):
        return {"symbol": symbol, "as_of": "2026-07-17", "daily_bars": 1000}

    async def scan(refresh=False):
        return {
            "rows": [focus_row("NVDA", 91)],
            "complete": True,
            "errors": {},
            "completed_session": "2026-07-17",
        }

    async def intelligence(symbol, technical, refresh=False):
        return {
            "symbol": symbol,
            "overall": {"score": 80, "tier": "A", "status": "RESEARCH_ONLY"},
            "pillars": {
                "fundamentals": {"status": "AVAILABLE", "score": 75},
                "news": {"status": "AVAILABLE", "score": 60},
                "social": {"status": "NOT_CONFIGURED", "score": None},
            },
            "filings": {"status": "AVAILABLE", "items": [{"form": "10-Q"}]},
        }

    async def claude_review(symbol, analysis, intelligence, consensus):
        assert symbol == "NVDA"
        assert consensus["status"] == "RESEARCH_ONLY"
        return {
            "provider": "Anthropic Claude",
            "status": "READY",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "summary": "More completed price and filed business evidence is needed.",
            "risks": [],
            "guardrails": {
                "changes_deterministic_posture": False,
                "execution_eligible": False,
                "broker_access": False,
            },
        }

    monkeypatch.setattr(app_module.stock_analyzer, "analyze", analyze)
    monkeypatch.setattr(app_module.discovery, "universe_scan", scan)
    monkeypatch.setattr(app_module.research_feed_service, "analyze", intelligence)
    monkeypatch.setattr(app_module.claude_research_reviewer, "review", claude_review)
    result = client.get("/api/dossier/NVDA").json()
    assert result["intelligence"]["overall"]["status"] == "RESEARCH_ONLY"
    assert result["coverage"]["fundamentals"] == "AVAILABLE"
    assert result["coverage"]["events"] == "FILING_CONTEXT_AVAILABLE"
    assert result["coverage"]["news"] == "AVAILABLE"
    assert result["intelligence"]["multi_brain"]["status"] == "RESEARCH_ONLY"
    claude = result["intelligence"]["multi_brain"]["claude_review"]
    assert claude["status"] == "READY"
    assert claude["provider"] == "Anthropic Claude"
    assert claude["guardrails"]["execution_eligible"] is False


def test_claude_failure_does_not_fail_deterministic_dossier(client, monkeypatch):
    from intellidhan_gateway import app as app_module

    async def analyze(symbol, **kwargs):
        return {
            "symbol": symbol,
            "as_of": "2026-07-17T20:00:00Z",
            "price": 150.0,
            "consensus": {"label": "UPTREND", "net_vote": 2},
            "methods": {},
            "key_levels": {},
            "risk": {},
            "forecast": {"strategy_context_status": "UNCONFIRMED", "horizons": {}},
        }

    async def intelligence(symbol, discovery_context):
        return {
            "symbol": symbol,
            "status": "AVAILABLE",
            "company": {},
            "fundamentals": {"status": "UNAVAILABLE", "metrics": []},
            "filings": {"status": "UNAVAILABLE", "items": []},
            "news": {"status": "UNAVAILABLE"},
            "social": {"status": "UNAVAILABLE"},
            "pillars": {},
        }

    async def broken_review(*args, **kwargs):
        raise AttributeError("malformed provider payload")

    monkeypatch.setattr(app_module.stock_analyzer, "analyze", analyze)
    monkeypatch.setattr(app_module.research_feed_service, "analyze", intelligence)
    monkeypatch.setattr(app_module.claude_research_reviewer, "review", broken_review)

    response = client.get("/api/dossier/AAPL")
    assert response.status_code == 200
    multi_brain = response.json()["intelligence"]["multi_brain"]
    assert multi_brain["posture"] == "INSUFFICIENT_EVIDENCE"
    assert multi_brain["claude_review"]["status"] == "UNAVAILABLE"
    assert multi_brain["claude_review"]["guardrails"]["execution_eligible"] is False


def test_landing_page_exposes_welcome_search_levels_and_multibrain_cards():
    source = open("web/index.html", encoding="utf-8").read()
    assert 'id="deskSummary"' in source
    assert 'id="homeStockSearchForm"' in source
    assert 'id="homeSearchSuggestions" role="listbox"' in source
    assert 'role="combobox"' in source
    assert 'aria-expanded="false"' in source
    assert 'event.key==="ArrowDown"' in source
    assert "async function resolveHomeSearch" in source
    assert 'fetchJSON(`/api/search?q=${encodeURIComponent(query)}&limit=7`)' in source
    assert 'id="homeKeyLevels"' in source
    assert 'id="analysisKeyLevels"' in source
    assert "research posture" in source
    assert "Claude independent research review" in source
    assert ".claude-review summary::before" in source
    assert "--lavender" not in source
    assert "ANTHROPIC_API_KEY" not in source
    assert "Forward edge remains unconfirmed" in source
    assert "currency unavailable" in source
