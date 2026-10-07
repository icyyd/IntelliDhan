"""Beginner desk integration stays read-only and skips paid review on refresh."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import intellidhan_gateway.app as gateway
from intellidhan_gateway.claude_research import ClaudeResearchReviewer
from intellidhan_gateway.terminal_store import TerminalStore

NOW = datetime(2026, 9, 24, 15, 2, tzinfo=timezone.utc)
OWNER = "desk-test-owner-credential-only-0123456789"


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def desk_api(tmp_path, monkeypatch):
    # TestClient is intentionally not entered: no lifespan or ingestion task runs.
    store = TerminalStore(tmp_path / "desk.sqlite3")
    store.init_schema()
    baseline = {
        "session": "RTH", "symbols": {}, "suppressed": [], "performance": {},
        "profiles": {}, "briefing": {}, "autotrade": {"effective_mode": "SIMULATION"},
        "last_poll": NOW.isoformat(),
        "alerts": [{
            "alert_id": "research-spy-1", "symbol": "SPY", "module": "0DTE",
            "strategy": "EMA9_MTF_0DTE", "vehicle": "EQUITY", "action": "EQUITY_BUY",
            "created_at": "2026-09-24T15:00:00Z", "valid_until": "2026-09-24T15:15:00Z",
            "status": "SHADOW", "research_only": True, "confidence": 0.74,
            "entry_zone": [599, 601], "stop_underlying": 597,
            "trend_matrix": {"5m": "UP", "15m": "UP", "1H": "UP"},
        }],
        "readiness": {
            "ok": True, "symbols": {"SPY": {"actionable": True, "status": "OK",
                                               "last_good_bar_at": "2026-09-24T15:00:00Z"}},
        },
    }
    operations = []

    def no_execution(*args, **kwargs):
        operations.append((args, kwargs))
        pytest.fail("A read-only desk request reached execution state")

    loop = SimpleNamespace(
        store=store,
        autotrade=SimpleNamespace(local_only=False, on_alert=no_execution,
                                 claim=no_execution, update_policy=no_execution),
        runner=SimpleNamespace(calibration={"EMA9_MTF_0DTE": SimpleNamespace(
            meta={"live_eligible": False, "evidence_status": "HISTORICAL_RESEARCH"}, buckets={},
        )}),
        snapshot=lambda: deepcopy(baseline),
    )
    monkeypatch.setattr(gateway, "loop", loop)
    monkeypatch.setattr(gateway, "datetime", FixedDateTime)
    monkeypatch.setattr(gateway, "rate_limiter", type(gateway.rate_limiter)())
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", OWNER)
    monkeypatch.setenv("INTELLIDHAN_AUTH_SECURE_COOKIES", "false")
    client = TestClient(gateway.app)
    yield SimpleNamespace(client=client, store=store, baseline=baseline, operations=operations)
    client.close()


def sign_in(client):
    response = client.post("/api/auth/session", json={"token": OWNER})
    assert response.status_code == 200


@pytest.fixture
def research(monkeypatch):
    calls = {"analysis": 0, "intelligence": 0, "claude": 0}
    raw_analysis = {
        "symbol": "SPY", "as_of": "2026-09-23T20:00:00Z", "price": 600,
        "consensus": {"label": "STRONG_UPTREND", "net_vote": 4},
        "risk": {"realized_volatility_20d_annualized_pct": 20},
        "forecast": {"strategy_context_status": "VALIDATED_CONTEXT", "horizons": {
            "one_month": {"trading_days": 21, "label": "FAVORABLE", "confidence": "MODERATE",
                          "matched_state_samples": 30, "walk_forward_validation": {
                              "status": "OUTPERFORMS_BASE", "brier_skill_pct": 4}},
        }},
        "key_levels": {"last_close": 600},
    }
    raw_intelligence = {
        "symbol": "SPY", "status": "AVAILABLE", "generated_at": NOW.isoformat(),
        "fundamentals": {"score": 75, "coverage": 3, "metrics": [
            {"key": "revenue_growth", "value": 10}, {"key": "gross_margin", "value": 50},
            {"key": "net_margin", "value": 20},
        ]},
        "news": {"score": 60, "article_count": 4},
        "social": {"score": 50, "mentions": 10},
        "pillars": {"fundamentals": {"status": "AVAILABLE"}},
        "filings": {"items": []},
    }

    async def analyze(symbol, **kwargs):
        calls["analysis"] += 1
        result = deepcopy(raw_analysis)
        result["symbol"] = symbol
        return result

    async def enrich(symbol, technical):
        calls["intelligence"] += 1
        result = deepcopy(raw_intelligence)
        result["symbol"] = symbol
        return result

    async def review(symbol, analysis, intelligence, consensus):
        calls["claude"] += 1
        assert consensus["status"] == "RESEARCH_ONLY"
        assert "autotrade" not in analysis
        return {"status": "READY", "verdict": "SUPPORTS", "summary": "Test research only."}

    reviewer = ClaudeResearchReviewer(api_key="")
    monkeypatch.setattr(reviewer, "review", review)
    monkeypatch.setattr(gateway, "claude_research_reviewer", reviewer)
    monkeypatch.setattr(gateway.stock_analyzer, "analyze", analyze)
    monkeypatch.setattr(gateway.research_feed_service, "analyze", enrich)
    return SimpleNamespace(calls=calls, analysis=raw_analysis, intelligence=raw_intelligence)


def test_default_route_serves_beginner_desk_and_advanced_route_is_retained(desk_api):
    landing = desk_api.client.get("/")
    advanced = desk_api.client.get("/advanced")
    assert landing.status_code == advanced.status_code == 200
    assert 'id="deskMain"' in landing.text
    assert 'href="/advanced"' in landing.text
    assert "/assets/desk.js" in landing.text
    assert landing.headers["cache-control"] == "no-cache"
    assert 'id="stockAnalysisForm"' in advanced.text
    assert 'id="deskMain"' not in advanced.text
    assert not desk_api.operations


def test_playbook_catalog_is_public_readonly_without_a_session(desk_api):
    response = desk_api.client.get("/api/playbooks")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "RESEARCH_ONLY"
    assert payload["execution_authorized"] is False
    assert len(payload["horizons"]) == 3
    assert all(item["execution_authorized"] is False for item in payload["playbooks"])
    assert desk_api.client.post("/api/playbooks", json={"execution_authorized": True}).status_code == 405
    assert desk_api.store.list_autotrade_events() == []
    assert not desk_api.operations


def test_state_stays_authenticated_and_adds_non_mutating_decisions(desk_api):
    before = deepcopy(desk_api.baseline)
    assert desk_api.client.get("/api/state").status_code == 401
    sign_in(desk_api.client)
    response = desk_api.client.get("/api/state")
    assert response.status_code == 200
    payload = response.json()
    assert {key: payload[key] for key in before} == before
    summary = payload["signal_desk"]["signals"][0]
    assert summary["source_alert_id"] == before["alerts"][0]["alert_id"]
    assert summary["research_view"] == "WAIT"
    assert summary["next_step"] == "WAIT"
    assert summary["execution_authorized"] is False
    assert desk_api.baseline == before
    assert desk_api.store.list_autotrade_events() == []
    assert not desk_api.operations


def test_refresh_can_skip_claude_while_retaining_deterministic_research(desk_api, research):
    sign_in(desk_api.client)
    response = desk_api.client.get("/api/dossier/SPY?include_review=false")
    assert response.status_code == 200
    payload = response.json()
    assert research.calls == {"analysis": 1, "intelligence": 1, "claude": 0}
    multi = payload["intelligence"]["multi_brain"]
    assert multi["posture"] == "BUY"
    assert len(multi["specialists"]) == 3
    assert multi["claude_review"]["status"] == "NOT_REQUESTED"
    assert payload["decision"]["research_view"] == "BUY"
    assert payload["decision"]["next_step"] == "TRACK_ONLY"
    assert payload["decision"]["execution_authorized"] is False
    assert not desk_api.operations


@pytest.mark.parametrize("query", ["", "?include_review=true"])
def test_default_and_explicit_review_preserve_the_optional_claude_path(desk_api, research, query):
    sign_in(desk_api.client)
    response = desk_api.client.get(f"/api/dossier/SPY{query}")
    assert response.status_code == 200
    payload = response.json()
    assert research.calls == {"analysis": 1, "intelligence": 1, "claude": 1}
    assert payload["intelligence"]["multi_brain"]["claude_review"]["status"] == "READY"
    assert payload["decision"]["research_view"] == "BUY"
    assert payload["decision"]["execution_authorized"] is False
    assert desk_api.store.list_autotrade_events() == []


def test_public_dossier_never_calls_private_enrichment_or_claude(desk_api, research):
    response = desk_api.client.get("/api/dossier/SPY")
    assert response.status_code == 200
    payload = response.json()
    assert research.calls == {"analysis": 1, "intelligence": 0, "claude": 0}
    assert payload["intelligence"] is None
    assert payload["decision"]["research_view"] == "WAIT"
    assert payload["watchlists"] == []
    assert OWNER not in response.text
