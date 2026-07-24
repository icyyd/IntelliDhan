import httpx
import pytest
from fastapi.testclient import TestClient

import intellidhan_gateway.app as gateway
from intellidhan_gateway.macro_news import MacroNewsService, _relevance, parse_rss
from intellidhan_gateway.terminal_store import TerminalStore


def test_parse_rss_normalizes_headlines_and_strips_markup():
    payload = """
    <rss><channel><item>
      <title>Fed &amp; markets: <b>rates</b> in focus</title>
      <link>https://example.test/fed</link>
      <pubDate>Thu, 24 Jul 2026 09:00:00 GMT</pubDate>
      <description><![CDATA[<p>Yield moves matter.</p>]]></description>
    </item></channel></rss>
    """
    items = parse_rss(payload, "Test")
    assert items == [{
        "title": "Fed & markets: rates in focus",
        "link": "https://example.test/fed",
        "source": "Test",
        "published_at": "Thu, 24 Jul 2026 09:00:00 GMT",
        "summary": "Yield moves matter.",
    }]


def test_parse_rss_rejects_non_web_links():
    payload = "<rss><channel><item><title>Bad</title><link>javascript:alert(1)</link></item></channel></rss>"
    assert parse_rss(payload, "Test") == []


def test_relevance_uses_terms_not_substrings_or_overlapping_plural_hits():
    assert _relevance({"title": "Corporate earnings update", "summary": ""}) == 0
    assert _relevance({"title": "Rates move higher", "summary": ""}) == 1


@pytest.mark.asyncio
async def test_macro_news_returns_explicit_unavailable_state(monkeypatch):
    service = MacroNewsService(feeds=(("Test", "https://example.test/rss"),), cache_seconds=0)

    async def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "get", fail)
    result = await service.get()
    assert result["status"] == "unavailable"
    assert result["items"] == []


def test_macro_news_endpoint_is_account_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLIDHAN_OWNER_TOKEN", "owner-token-that-is-long-enough-123")
    store = TerminalStore(tmp_path / "news-api.sqlite3")
    store.init_schema()
    monkeypatch.setattr(gateway.loop, "store", store)
    client = TestClient(gateway.app)
    assert client.get("/api/news").status_code == 401
