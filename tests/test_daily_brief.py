"""Daily-brief adapter, durable fallback, API, and landing-page contract."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import intellidhan_gateway.app as gateway
from intellidhan_gateway.daily_brief import (
    DailyBriefService,
    DailyBriefUnavailable,
    parse_daily_brief,
)
from intellidhan_gateway.terminal_store import TerminalStore


REPORT = """# AI PREMARKET REPORT — IntelliDhan
### Tuesday, July 14, 2026 · Claude + Codex + Grok

## Summary

**Risk-on, but wait for the inflation release.** Trade smaller or stand down.

## Day Trading Watchlist

| Ticker | Catalyst | Levels | Plan (Trend Join) | Codex | Conv. |
|---|---|---|---|---|---|
| (none) | RVOL gate not cleared | n/a | Stand down | Do not force it | ⬜ |

## Swing Watchlist

| Ticker | Catalyst | Trend | Idea | Codex | Grok | Conv. |
|---|---|---|---|---|---|---|
| CRWD | Cyber spending shift | Above prior high | Hold the reclaim, do not chase | Confirm first | Extended | 🟡 |

## Market Trends

- Tech leads while rates ease.

## Technical Signals

- Breadth is healthy.

## Economic Data

| Time ET | Event | Forecast vs Previous |
|---|---|---|
| 8:30 AM | CPI | 3.8% vs 4.2% |

## Skips & Traps

- **SEZL** <script>alert(1)</script> bad-news pump. 🔴

## Where the three brains landed

- No name earned three-way agreement.
"""


PACKET = {
    "generated_at": "2026-07-14T08:25:00-04:00",
    "candidate_source": "live_screener",
    "trading_day_note": "Times are US/Eastern.",
    "data_sources": {
        "premarket_levels_and_rvol": "yfinance full-day stand-in (no real premarket feed)"
    },
    "market_snapshot": {
        "S&P 500": {"last": 7000.0, "change_pct": 0.4},
        "Nasdaq": {"last": 25000.0, "change_pct": 0.9},
        "VIX": {"last": 16.5, "change_pct": -3.8},
        "US 10Y": {"last": 4.59, "change_pct": -0.5},
    },
    "gappers": [
        {
            "ticker": "CRWD",
            "gap_pct": 12.14,
            "day_eligible": False,
            "swing_eligible": True,
            "rvol_used": 1.11,
            "rvol_source": "yfinance_fullday",
        }
    ],
}


def test_parser_builds_bounded_setup_contract(monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_DAILY_BRIEF_REPOSITORY", "icyyd/intellidhan-daily-brief")
    brief = parse_daily_brief(REPORT, PACKET)

    assert brief["report_date"] == "2026-07-14"
    assert brief["headline"].startswith("Risk-on")
    assert brief["setups"] == [
        {
            "symbol": "CRWD",
            "module": "SWING",
            "catalyst": "Cyber spending shift",
            "context": "Above prior high",
            "plan": "Hold the reclaim, do not chase",
            "analyst_notes": ["Confirm first", "Extended"],
            "conviction": "MEDIUM",
            "rule_eligible": True,
            "gap_pct": 12.14,
            "rvol": 1.11,
            "rvol_source": "yfinance_fullday",
        }
    ]
    assert brief["economic_events"][0]["event"] == "CPI"
    assert "<script>" not in brief["risks"][0]
    assert "full-day stand-in" in brief["data_quality"]["limitations"][0]


@pytest.mark.parametrize("eligibility", [False, "false", None, 0, 1])
def test_parser_fails_closed_on_non_boolean_eligibility(eligibility):
    packet = copy.deepcopy(PACKET)
    packet["gappers"][0]["swing_eligible"] = eligibility
    brief = parse_daily_brief(
        REPORT, packet, now=datetime.fromisoformat("2026-07-14T08:30:00-04:00")
    )
    assert brief["setups"] == []


def test_parser_filters_ai_only_rows_and_invalid_numbers():
    report = REPORT.replace(
        "| CRWD | Cyber spending shift",
        "| FAKE | AI-added name | Below trend | Chase it | Buy | Buy | 🟢 |\n"
        "| CRWD | Cyber spending shift",
    )
    packet = copy.deepcopy(PACKET)
    packet["gappers"][0]["gap_pct"] = float("nan")
    packet["gappers"][0]["rvol_used"] = None
    packet["market_snapshot"]["S&P 500"]["last"] = float("inf")
    packet["market_snapshot"]["S&P 500"]["change_pct"] = "0.4"
    brief = parse_daily_brief(
        report, packet, now=datetime.fromisoformat("2026-07-14T08:30:00-04:00")
    )
    assert [setup["symbol"] for setup in brief["setups"]] == ["CRWD"]
    assert brief["setups"][0]["gap_pct"] is None
    assert brief["setups"][0]["rvol"] is None
    assert brief["market"][0]["value"] is None
    assert brief["market"][0]["change_pct"] is None


def test_freshness_uses_market_session_and_rejects_future_clock_skew():
    current = parse_daily_brief(
        REPORT, PACKET, now=datetime.fromisoformat("2026-07-14T08:30:00-04:00")
    )
    assert (current["status"], current["freshness_reason"]) == (
        "CURRENT",
        "current_session",
    )

    after_close_packet = copy.deepcopy(PACKET)
    after_close_packet["generated_at"] = "2026-07-14T22:56:00-04:00"
    after_close = parse_daily_brief(
        REPORT,
        after_close_packet,
        now=datetime.fromisoformat("2026-07-14T23:00:00-04:00"),
    )
    assert (after_close["status"], after_close["freshness_label"]) == (
        "STALE",
        "After-hours report",
    )

    weekend_packet = copy.deepcopy(PACKET)
    weekend_packet["generated_at"] = "2026-07-11T08:25:00-04:00"
    weekend = parse_daily_brief(
        REPORT,
        weekend_packet,
        now=datetime.fromisoformat("2026-07-11T08:30:00-04:00"),
    )
    assert weekend["freshness_reason"] == "non_trading_day"

    with pytest.raises(DailyBriefUnavailable, match="materially in the future"):
        parse_daily_brief(
            REPORT, PACKET, now=datetime.fromisoformat("2026-07-14T08:10:00-04:00")
        )

    naive_packet = copy.deepcopy(PACKET)
    naive_packet["generated_at"] = "2026-07-14T08:25:00"
    with pytest.raises(DailyBriefUnavailable, match="must include a UTC offset"):
        parse_daily_brief(
            REPORT,
            naive_packet,
            now=datetime.fromisoformat("2026-07-14T08:30:00-04:00"),
        )


def test_cached_brief_recomputes_freshness_at_session_close():
    clock = [datetime.fromisoformat("2026-07-14T15:59:00-04:00")]
    service = DailyBriefService(wall_clock=lambda: clock[0])
    service._cached = parse_daily_brief(REPORT, PACKET, now=clock[0])
    service._cache_until = float("inf")

    assert asyncio.run(service.get(object()))["status"] == "CURRENT"
    clock[0] = datetime.fromisoformat("2026-07-14T16:01:00-04:00")
    refreshed = asyncio.run(service.get(object()))
    assert (refreshed["status"], refreshed["freshness_label"]) == (
        "STALE",
        "Session complete",
    )


def test_daily_brief_store_round_trip(tmp_path):
    store = TerminalStore(tmp_path / "brief.sqlite3")
    store.init_schema()
    payload = parse_daily_brief(REPORT, PACKET)
    store.put_daily_brief(payload)
    assert store.latest_daily_brief()["source"]["report_hash"] == payload["source"]["report_hash"]


def test_service_uses_last_good_brief_when_github_is_unavailable(tmp_path, monkeypatch):
    store = TerminalStore(tmp_path / "brief.sqlite3")
    store.init_schema()
    stored = parse_daily_brief(REPORT, PACKET)
    store.put_daily_brief(stored)
    service = DailyBriefService(cache_seconds=1)

    async def fail():
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr(service, "_fetch", fail)
    result = asyncio.run(service.get(store))
    assert result["status"] == "STALE"
    assert result["source_health"] == "stored_fallback"
    assert "last saved brief" in result["notice"]


def test_github_adapter_accepts_wrapped_base64_content():
    encoded = base64.b64encode(b"daily brief").decode()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"encoding": "base64", "content": encoded[:5] + "\n" + encoded[5:]}

    class Client:
        async def get(self, url, **kwargs):
            assert url.startswith("https://api.github.com/repos/")
            assert kwargs["headers"]["Authorization"] == "Bearer read-only-token"
            return Response()

    service = DailyBriefService()
    content = asyncio.run(
        service._github_file(
            Client(), "icyyd/intellidhan-daily-brief", "main", "REPORT.md", "read-only-token"
        )
    )
    assert content == b"daily brief"


def test_fetch_pins_report_and_packet_to_one_commit(monkeypatch):
    service = DailyBriefService()
    commit = "a" * 40
    observed_refs = []

    async def resolve(client, repository, ref, token):
        assert ref == "main"
        return commit

    async def read_file(client, repository, ref, path, token):
        observed_refs.append((ref, path))
        if path == "REPORT.md":
            return REPORT.encode()
        return json.dumps(PACKET).encode()

    monkeypatch.setattr(service, "_github_commit_sha", resolve)
    monkeypatch.setattr(service, "_github_file", read_file)
    payload = asyncio.run(service._fetch())
    assert observed_refs == [(commit, "REPORT.md"), (commit, "packet.json")]
    assert payload["source"]["commit_sha"] == commit
    assert f"/blob/{commit}/REPORT.md" in payload["source"]["url"]


def test_daily_brief_api_requires_auth_and_returns_normalized_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", "owner-token-that-is-long-enough-123")
    store = TerminalStore(tmp_path / "api.sqlite3")
    store.init_schema()
    monkeypatch.setattr(gateway.loop, "store", store)
    monkeypatch.setattr(gateway, "rate_limiter", type(gateway.rate_limiter)())
    payload = parse_daily_brief(REPORT, PACKET)

    class StubService:
        async def get(self, selected_store):
            assert selected_store is store
            return payload

    monkeypatch.setattr(gateway, "daily_brief_service", StubService())
    client = TestClient(gateway.app)
    assert client.get("/api/daily-brief").status_code == 401
    assert client.post("/api/auth/session", json={"token": "owner-token-that-is-long-enough-123"}).status_code == 200
    response = client.get("/api/daily-brief")
    assert response.status_code == 200
    assert response.json()["setups"][0]["symbol"] == "CRWD"


def test_landing_page_renders_daily_brief_as_research_not_signal():
    source = open("web/index.html", encoding="utf-8").read()
    assert 'id="dailySetup"' in source
    assert 'fetchJSON("/api/daily-brief")' in source
    assert "Research, not a live signal" in source
    assert 'data-daily-symbol="${esc(setup.symbol)}"' in source
    assert "renderDailyBrief" in source
    assert "function finiteNumber(value)" in source
    assert 'id="dailySetup" aria-labelledby="dailySetupTitle" hidden' in source
    assert 'role="status" aria-live="polite"' in source
    assert 'panel.setAttribute("aria-busy","true")' in source
