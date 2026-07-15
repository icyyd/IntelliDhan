"""Safe, freshness-aware adapter for the external IntelliDhan daily brief.

The source repository is private, so GitHub access happens only in the gateway.
Upstream Markdown is parsed into a small JSON contract and is never rendered as
HTML in the browser.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_schemas import SessionState

EASTERN = ZoneInfo("America/New_York")
DEFAULT_REPOSITORY = "icyyd/intellidhan-daily-brief"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_MARKDOWN_LINK_RE = re.compile(r"\[([^]]+)]\([^)]+\)")
_EMOJI_RE = re.compile(
    "[\U0001F1E0-\U0001FAFF\U00002600-\U000027BF\U0000FE0F]"
)


class DailyBriefUnavailable(RuntimeError):
    """Raised when the configured source cannot produce a usable brief."""


def _plain(value: str) -> str:
    value = value.replace("🟢", " green ").replace("🟡", " yellow ").replace("🔴", " red ")
    value = value.replace("⬜", " none ")
    value = _MARKDOWN_LINK_RE.sub(r"\1", value)
    value = re.sub(r"<[^>]*>", "", value)
    value = _EMOJI_RE.sub("", value)
    value = re.sub(r"[*_`~]", "", value)
    value = re.sub(r"^\s*>\s?", "", value)
    return re.sub(r"\s+", " ", value).strip(" -|")


def _section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _plain(value).lower()).strip()


def _sections(markdown: str) -> tuple[list[str], dict[str, list[str]]]:
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for raw in markdown.replace("\r\n", "\n").splitlines():
        if raw.startswith("## "):
            current = sections.setdefault(_section_key(raw[3:]), [])
        elif raw.startswith("# ") or raw.startswith("### "):
            continue
        elif current is None:
            preamble.append(raw)
        else:
            current.append(raw)
    return preamble, sections


def _find_section(sections: dict[str, list[str]], *needles: str) -> list[str]:
    for key, lines in sections.items():
        if any(needle in key for needle in needles):
            return lines
    return []


def _paragraphs(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line == "---" or line.startswith("|"):
            if buffer:
                paragraphs.append(_plain(" ".join(buffer)))
                buffer = []
            continue
        if line.startswith("- "):
            if buffer:
                paragraphs.append(_plain(" ".join(buffer)))
                buffer = []
            paragraphs.append(_plain(line[2:]))
        else:
            buffer.append(line)
    if buffer:
        paragraphs.append(_plain(" ".join(buffer)))
    return [item for item in paragraphs if item]


def _table(lines: list[str]) -> list[dict[str, str]]:
    rows = [line.strip() for line in lines if line.strip().startswith("|")]
    if len(rows) < 2:
        return []

    def cells(row: str) -> list[str]:
        return [_plain(cell) for cell in row.strip("|").split("|")]

    headers = [_section_key(cell).replace(" ", "_") for cell in cells(rows[0])]
    output: list[dict[str, str]] = []
    for raw in rows[2:]:
        values = cells(raw)
        if len(values) != len(headers):
            continue
        output.append(dict(zip(headers, values, strict=True)))
    return output


def _conviction(value: str) -> str:
    upper = value.upper()
    if "GREEN" in upper or "HIGH" in upper:
        return "HIGH"
    if "YELLOW" in upper or "MED" in upper:
        return "MEDIUM"
    if "RED" in upper or "LOW" in upper:
        return "LOW"
    return "NONE"


def _finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _setup_rows(
    rows: list[dict[str, str]], module: str, eligibility: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("ticker") or row.get("symbol") or "").upper()
        if not _SYMBOL_RE.fullmatch(symbol) or symbol == "NONE":
            continue
        source = eligibility.get(symbol, {})
        eligible_key = "day_eligible" if module == "DAY" else "swing_eligible"
        # Only deterministic, strict-boolean eligibility may produce a setup card.
        if source.get(eligible_key) is not True:
            continue
        plan = row.get("plan_trend_join") or row.get("plan") or row.get("idea") or ""
        context = row.get("levels") or row.get("trend") or row.get("trend_context") or ""
        analyst_notes = [
            note
            for key, note in row.items()
            if ("codex" in key or "grok" in key or "claude" in key) and note
        ]
        output.append(
            {
                "symbol": symbol,
                "module": module,
                "catalyst": row.get("catalyst", ""),
                "context": context,
                "plan": plan,
                "analyst_notes": analyst_notes[:3],
                "conviction": _conviction(row.get("conv", row.get("conviction", ""))),
                "rule_eligible": True,
                "gap_pct": _finite_number(source.get("gap_pct")),
                "rvol": _finite_number(source.get("rvol_used")),
                "rvol_source": source.get("rvol_source"),
            }
        )
    return output


def _freshness(generated_at: datetime, now: datetime) -> tuple[str, str, str]:
    """Return status, machine reason, and user-facing label for a target session."""
    if now.tzinfo is None:
        raise ValueError("daily brief freshness requires a timezone-aware clock")
    local_now = now.astimezone(EASTERN)
    if generated_at > local_now + timedelta(minutes=5):
        raise DailyBriefUnavailable("daily packet timestamp is materially in the future")
    clock = MarketClock()
    report_date = generated_at.date()
    try:
        if not clock.is_trading_day(report_date):
            return "STALE", "non_trading_day", "Non-session report"
        generated_session = clock.session_state(generated_at)
        if generated_session not in {SessionState.PRE, SessionState.RTH}:
            return "STALE", "generated_outside_session", "After-hours report"
        if report_date != local_now.date():
            return "STALE", "previous_session", "Previous session"
        if local_now.time() >= clock.rth_close(local_now.date()):
            return "STALE", "session_complete", "Session complete"
    except ValueError:
        return "STALE", "calendar_unavailable", "Calendar check unavailable"
    return "CURRENT", "current_session", "Current brief"


def _parse_generated_at(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise DailyBriefUnavailable("daily packet has no valid generated_at timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyBriefUnavailable("daily packet generated_at must include a UTC offset")
    return parsed.astimezone(EASTERN)


def _refresh_payload_freshness(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Re-evaluate time-sensitive labels even while artifact content is cached."""
    if payload.get("source_health") != "live":
        return payload
    try:
        status, reason, label = _freshness(
            _parse_generated_at(payload.get("generated_at")), now
        )
    except DailyBriefUnavailable:
        status, reason, label = "STALE", "invalid_timestamp", "Timestamp unavailable"
    return {
        **payload,
        "status": status,
        "freshness_reason": reason,
        "freshness_label": label,
    }


def parse_daily_brief(
    report_markdown: str,
    packet: dict[str, Any],
    *,
    now: datetime | None = None,
    source_ref: str | None = None,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Convert the upstream artifacts into the bounded landing-page contract."""
    if not report_markdown.strip() or len(report_markdown) > 256_000:
        raise DailyBriefUnavailable("daily report is empty or exceeds the size limit")
    generated_at = _parse_generated_at(packet.get("generated_at"))

    _, sections = _sections(report_markdown)
    summary = _paragraphs(_find_section(sections, "summary"))
    if not summary:
        raise DailyBriefUnavailable("daily report has no summary section")

    gapper_rows = packet.get("gappers") if isinstance(packet.get("gappers"), list) else []
    eligibility = {
        str(row.get("ticker") or row.get("symbol") or "").upper(): row
        for row in gapper_rows
        if isinstance(row, dict)
    }
    day_rows = _table(_find_section(sections, "day trading watchlist"))
    swing_rows = _table(_find_section(sections, "swing watchlist"))
    setups = _setup_rows(day_rows, "DAY", eligibility) + _setup_rows(
        swing_rows, "SWING", eligibility
    )

    snapshot = packet.get("market_snapshot")
    market: list[dict[str, Any]] = []
    if isinstance(snapshot, dict):
        for name in ("S&P 500", "Nasdaq", "VIX", "US 10Y"):
            item = snapshot.get(name)
            if isinstance(item, dict):
                market.append(
                    {
                        "name": name,
                        "value": _finite_number(item.get("last")),
                        "change_pct": _finite_number(item.get("change_pct")),
                    }
                )

    data_sources = packet.get("data_sources") if isinstance(packet.get("data_sources"), dict) else {}
    rvol_description = str(data_sources.get("premarket_levels_and_rvol") or "not reported")
    limitations: list[str] = []
    if "stand-in" in rvol_description.lower() or "full-day" in rvol_description.lower():
        limitations.append("Relative volume is a full-day stand-in, not true premarket volume.")
    trading_note = _plain(str(packet.get("trading_day_note") or ""))
    if trading_note:
        limitations.append(trading_note)

    report_date = generated_at.date()
    status, freshness_reason, freshness_label = _freshness(
        generated_at, now or datetime.now(EASTERN)
    )
    report_hash = hashlib.sha256(report_markdown.encode()).hexdigest()[:16]
    repo = os.getenv("INTELLIDHAN_DAILY_BRIEF_REPOSITORY", DEFAULT_REPOSITORY)
    ref = source_ref or os.getenv("INTELLIDHAN_DAILY_BRIEF_REF", "main")
    blob_ref = source_commit or ref
    return {
        "status": status,
        "freshness_reason": freshness_reason,
        "freshness_label": freshness_label,
        "source_health": "live",
        "report_date": report_date.isoformat(),
        "generated_at": generated_at.isoformat(),
        "headline": summary[0],
        "summary": summary[:3],
        "setups": setups,
        "market": market,
        "market_trends": _paragraphs(_find_section(sections, "market trends"))[:5],
        "technical_signals": _paragraphs(_find_section(sections, "technical signals"))[:5],
        "economic_events": _table(_find_section(sections, "economic data"))[:6],
        "risks": _paragraphs(_find_section(sections, "skips", "traps"))[:6],
        "analyst_reconciliation": _paragraphs(
            _find_section(sections, "brains landed", "two brains", "three brains")
        )[:5],
        "data_quality": {
            "candidate_source": packet.get("candidate_source"),
            "rvol_source": rvol_description,
            "limitations": limitations,
        },
        "source": {
            "repository": repo,
            "ref": ref,
            "commit_sha": source_commit,
            "report_hash": report_hash,
            "url": f"https://github.com/{repo}/blob/{blob_ref}/REPORT.md",
        },
        "disclaimer": "Research brief only. A watchlist idea is not a live signal or an order.",
    }


class DailyBriefService:
    """Fetch and cache the private GitHub report with a durable fallback."""

    def __init__(
        self,
        *,
        cache_seconds: int = 300,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache_seconds = cache_seconds
        self._wall_clock = wall_clock or (lambda: datetime.now(EASTERN))
        self._cached: dict[str, Any] | None = None
        self._cache_until = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _config() -> tuple[str, str, str | None]:
        repository = os.getenv("INTELLIDHAN_DAILY_BRIEF_REPOSITORY", DEFAULT_REPOSITORY)
        if not _REPOSITORY_RE.fullmatch(repository):
            raise DailyBriefUnavailable("daily brief repository configuration is invalid")
        ref = os.getenv("INTELLIDHAN_DAILY_BRIEF_REF", "main").strip()
        if not ref or len(ref) > 100 or any(char.isspace() for char in ref):
            raise DailyBriefUnavailable("daily brief ref configuration is invalid")
        return repository, ref, os.getenv("INTELLIDHAN_DAILY_BRIEF_GITHUB_TOKEN")

    async def _github_file(
        self, client: httpx.AsyncClient, repository: str, ref: str, path: str, token: str | None
    ) -> bytes:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = await client.get(
            f"https://api.github.com/repos/{repository}/contents/{path}",
            params={"ref": ref},
            headers=headers,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("encoding") != "base64" or not isinstance(body.get("content"), str):
            raise DailyBriefUnavailable(f"GitHub returned an unsupported {path} payload")
        try:
            # GitHub's Contents API wraps base64 output across multiple lines.
            encoded = re.sub(r"\s+", "", body["content"])
            return base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise DailyBriefUnavailable(f"GitHub returned invalid content for {path}") from exc

    async def _github_commit_sha(
        self, client: httpx.AsyncClient, repository: str, ref: str, token: str | None
    ) -> str:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = await client.get(
            f"https://api.github.com/repos/{repository}/commits/{quote(ref, safe='')}",
            headers=headers,
        )
        response.raise_for_status()
        sha = response.json().get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise DailyBriefUnavailable("GitHub returned an invalid daily-brief commit")
        return sha.lower()

    async def _fetch(self) -> dict[str, Any]:
        repository, ref, token = self._config()
        timeout = httpx.Timeout(8.0, connect=4.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            commit_sha = await self._github_commit_sha(client, repository, ref, token)
            report_raw, packet_raw = await asyncio.gather(
                self._github_file(client, repository, commit_sha, "REPORT.md", token),
                self._github_file(client, repository, commit_sha, "packet.json", token),
            )
        if len(packet_raw) > 1_000_000:
            raise DailyBriefUnavailable("daily packet exceeds the size limit")
        try:
            report = report_raw.decode("utf-8")
            packet = json.loads(packet_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DailyBriefUnavailable("daily artifacts are malformed") from exc
        if not isinstance(packet, dict):
            raise DailyBriefUnavailable("daily packet must be a JSON object")
        return parse_daily_brief(
            report,
            packet,
            now=self._wall_clock(),
            source_ref=ref,
            source_commit=commit_sha,
        )

    async def get(self, store: Any) -> dict[str, Any]:
        now = time.monotonic()
        if self._cached is not None and now < self._cache_until:
            self._cached = _refresh_payload_freshness(self._cached, self._wall_clock())
            return self._cached
        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and now < self._cache_until:
                self._cached = _refresh_payload_freshness(self._cached, self._wall_clock())
                return self._cached
            try:
                payload = await self._fetch()
                store.put_daily_brief(payload)
            except Exception:
                fallback = store.latest_daily_brief()
                if not fallback:
                    return {
                        "status": "UNAVAILABLE",
                        "freshness_reason": "source_unavailable",
                        "freshness_label": "Brief unavailable",
                        "source_health": "unavailable",
                        "headline": "Today's setup is not available yet.",
                        "summary": [],
                        "setups": [],
                        "market": [],
                        "risks": [],
                        "notice": "The private daily-brief source could not be reached. Check the server-side GitHub configuration.",
                        "disclaimer": "Research brief only. A watchlist idea is not a live signal or an order.",
                    }
                payload = dict(fallback)
                payload["status"] = "STALE"
                payload["freshness_reason"] = "stored_fallback"
                payload["freshness_label"] = "Stored fallback"
                payload["source_health"] = "stored_fallback"
                payload["notice"] = "Showing the last saved brief because the daily source is unavailable."
            self._cached = payload
            self._cache_until = time.monotonic() + self.cache_seconds
            return payload
