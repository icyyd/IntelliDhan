"""Small, cached macro headline feed for the command-center homepage.

News is context only. It never changes a signal, rank, or execution intent. The
adapter is deliberately best-effort: a feed outage returns an explicit empty
state instead of stale headlines being presented as current.
"""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx


_MACRO_TERMS = {
    "cpi", "inflation", "fomc", "fed", "rate", "rates", "yield", "treasury",
    "jobs", "payroll", "employment", "gdp", "economy", "recession", "tariff",
    "trade", "oil", "crude", "dollar", "vix", "nasdaq", "s&p",
}
_HIGH_IMPACT_TERMS = {"fomc", "fed", "cpi", "payroll", "jobs", "inflation", "tariff"}
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(value: str | None, limit: int = 240) -> str:
    text = _TAG_RE.sub(" ", unescape(value or ""))
    return " ".join(text.split())[:limit]


def _node_text(node: ET.Element | None) -> str:
    return "".join(node.itertext()) if node is not None else ""


def _published_epoch(value: str) -> float:
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _safe_link(value: str) -> str:
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def parse_rss(payload: str, source: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0 or Atom items into the stable UI contract."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    items: list[dict[str, Any]] = []
    for node in root.findall(".//item"):
        title = _clean(_node_text(node.find("title")), 160)
        link = _safe_link(_clean(_node_text(node.find("link")), 500))
        if not title or not link:
            continue
        published = _clean(_node_text(node.find("pubDate")) or _node_text(node.find("published")), 80)
        description = _clean(_node_text(node.find("description")) or _node_text(node.find("summary")), 240)
        items.append({
            "title": title,
            "link": link,
            "source": source,
            "published_at": published or None,
            "summary": description,
        })
    # A few current feeds use Atom instead of RSS.
    if items:
        return items
    for node in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = _clean(node.findtext("{http://www.w3.org/2005/Atom}title"), 160)
        link_node = node.find("{http://www.w3.org/2005/Atom}link")
        link = _safe_link(_clean(link_node.attrib.get("href") if link_node is not None else "", 500))
        if not title or not link:
            continue
        published = _clean(
            node.findtext("{http://www.w3.org/2005/Atom}published")
            or node.findtext("{http://www.w3.org/2005/Atom}updated"),
            80,
        )
        summary = _clean(node.findtext("{http://www.w3.org/2005/Atom}summary"), 240)
        items.append({
            "title": title,
            "link": link,
            "source": source,
            "published_at": published or None,
            "summary": summary,
        })
    return items


def _relevance(item: dict[str, Any]) -> int:
    haystack = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return sum(2 if term in _HIGH_IMPACT_TERMS else 1 for term in _MACRO_TERMS if term in haystack)


@dataclass
class MacroNewsService:
    """Fetch and cache a short, relevant macro feed without blocking the loop."""

    feeds: tuple[tuple[str, str], ...] = (
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("MarketWatch Top Stories", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    )
    cache_seconds: int = 300
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, limit: int = 8) -> dict[str, Any]:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self.cache_seconds:
            return {**self._cached, "items": self._cached["items"][:limit]}
        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < self.cache_seconds:
                return {**self._cached, "items": self._cached["items"][:limit]}
            headers = {"User-Agent": "IntelliDhan/0.2 macro-context/1.0"}
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
                results = await asyncio.gather(
                    *(self._fetch(client, name, url, headers) for name, url in self.feeds),
                    return_exceptions=True,
                )
            merged: list[dict[str, Any]] = []
            for result in results:
                if isinstance(result, list):
                    merged.extend(result)
            deduped: dict[str, dict[str, Any]] = {}
            for item in merged:
                deduped.setdefault(item["link"] or item["title"], item)
            items = list(deduped.values())
            for item in items:
                score = _relevance(item)
                item["impact"] = "HIGH" if score >= 2 else "WATCH" if score else "MARKET"
                item["relevance"] = score
                item["_published_epoch"] = _published_epoch(item.get("published_at"))
            relevant = [item for item in items if item["relevance"] > 0]
            if len(relevant) >= 3:
                items = relevant
            items.sort(key=lambda item: (item["relevance"], item.get("_published_epoch", 0)), reverse=True)
            for item in items:
                item.pop("_published_epoch", None)
            self._cached = {
                "status": "fresh" if items else "unavailable",
                "as_of": datetime.now(timezone.utc).isoformat(),
                "items": items[: max(limit, 12)],
            }
            self._cached_at = time.monotonic()
            return {**self._cached, "items": self._cached["items"][:limit]}

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        source: str,
        url: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return parse_rss(response.text, source)
        except (httpx.HTTPError, ValueError):
            return []
