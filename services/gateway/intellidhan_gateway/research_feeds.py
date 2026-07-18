"""Bounded, provenance-first research feeds for the decision terminal.

The output is a current research snapshot, not a backtest feature matrix or a
trade signal.  Missing providers and missing filing concepts remain explicit;
scores are renormalized across available pillars instead of receiving a
favorable placeholder.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_DATA_ROOT = "https://data.sec.gov"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
FINNHUB_SOCIAL_URL = "https://finnhub.io/api/v1/stock/social-sentiment"
_SAFE_DOCUMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_DISPLAY_CURRENCIES = {
    "AUD",
    "BRL",
    "CAD",
    "CHF",
    "CNY",
    "DKK",
    "EUR",
    "GBP",
    "HKD",
    "ILS",
    "INR",
    "JPY",
    "KRW",
    "MXN",
    "NOK",
    "SEK",
    "SGD",
    "TWD",
    "USD",
    "ZAR",
}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _linear_score(value: float, low: float, high: float) -> float:
    if high <= low:
        raise ValueError("score band must increase")
    return round(_clip((value - low) / (high - low) * 100.0), 1)


def score_tier(score: float | None) -> str:
    if score is None:
        return "UNRATED"
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _concept(facts: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any] | None:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for name in names:
        item = gaap.get(name)
        if isinstance(item, dict):
            return item
    return None


def _annual_values(concept: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not concept:
        return []
    rows = concept.get("units", {}).get("USD", [])
    by_end: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if row.get("form") not in {"10-K", "10-K/A"} or row.get("fp") != "FY":
            continue
        value = _finite(row.get("val"))
        start, end, filed = row.get("start"), row.get("end"), row.get("filed")
        if value is None or not all(isinstance(item, str) for item in (start, end, filed)):
            continue
        try:
            duration = (date.fromisoformat(end) - date.fromisoformat(start)).days
        except ValueError:
            continue
        if not 300 <= duration <= 430:
            continue
        clean = {"value": value, "start": start, "end": end, "filed": filed}
        if end not in by_end or filed > by_end[end]["filed"]:
            by_end[end] = clean
    return sorted(by_end.values(), key=lambda item: item["end"], reverse=True)


def _annual_at(concept: dict[str, Any] | None, end: str) -> float | None:
    return next(
        (item["value"] for item in _annual_values(concept) if item["end"] == end),
        None,
    )


def _instant_values(concept: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not concept:
        return []
    rows = concept.get("units", {}).get("USD", [])
    candidates: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if row.get("form") not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            continue
        value = _finite(row.get("val"))
        end, filed = row.get("end"), row.get("filed")
        if value is None or not isinstance(end, str) or not isinstance(filed, str):
            continue
        candidates.append({"value": value, "end": end, "filed": filed})
    by_end: dict[str, dict[str, Any]] = {}
    for item in candidates:
        if item["end"] not in by_end or item["filed"] > by_end[item["end"]]["filed"]:
            by_end[item["end"]] = item
    return sorted(by_end.values(), key=lambda item: (item["end"], item["filed"]), reverse=True)


def parse_sec_fundamentals(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a conservative filing-quality snapshot from standard XBRL facts."""
    revenue = _concept(
        payload,
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
    )
    revenue_rows = _annual_values(revenue)
    if not revenue_rows:
        return {
            "status": "UNAVAILABLE",
            "score": None,
            "tier": "UNRATED",
            "metrics": [],
            "limitation": "No comparable annual US-GAAP revenue fact was available.",
        }
    current = revenue_rows[0]
    prior = revenue_rows[1] if len(revenue_rows) > 1 else None
    end = current["end"]
    revenue_value = current["value"]
    if revenue_value <= 0:
        return {
            "status": "UNAVAILABLE",
            "score": None,
            "tier": "UNRATED",
            "metrics": [],
            "limitation": "The latest comparable annual revenue fact was not positive.",
        }
    concepts = {
        "gross_profit": _concept(payload, ("GrossProfit",)),
        "net_income": _concept(payload, ("NetIncomeLoss", "ProfitLoss")),
        "operating_cash": _concept(
            payload, ("NetCashProvidedByUsedInOperatingActivities",)
        ),
        "capex": _concept(
            payload,
            (
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "PaymentsForProceedsFromOtherPropertyPlantAndEquipment",
            ),
        ),
        "assets": _concept(payload, ("Assets",)),
        "liabilities": _concept(payload, ("Liabilities",)),
    }
    metrics: list[dict[str, Any]] = []

    def add_metric(key: str, label: str, value: float | None, score: float | None) -> None:
        if value is not None and score is not None:
            metrics.append(
                {"key": key, "label": label, "value": round(value, 2), "score": score}
            )

    growth = None
    if prior and prior["value"] > 0:
        growth = (revenue_value / prior["value"] - 1.0) * 100.0
    add_metric(
        "revenue_growth",
        "Annual revenue growth",
        growth,
        _linear_score(growth, -15.0, 25.0) if growth is not None else None,
    )
    gross_profit = _annual_at(concepts["gross_profit"], end)
    gross_margin = gross_profit / revenue_value * 100.0 if gross_profit is not None else None
    add_metric(
        "gross_margin",
        "Gross margin",
        gross_margin,
        _linear_score(gross_margin, 10.0, 70.0) if gross_margin is not None else None,
    )
    net_income = _annual_at(concepts["net_income"], end)
    net_margin = net_income / revenue_value * 100.0 if net_income is not None else None
    add_metric(
        "net_margin",
        "Net margin",
        net_margin,
        _linear_score(net_margin, -10.0, 25.0) if net_margin is not None else None,
    )
    operating_cash = _annual_at(concepts["operating_cash"], end)
    capex = _annual_at(concepts["capex"], end)
    free_cash_flow = (
        operating_cash - abs(capex)
        if operating_cash is not None and capex is not None
        else None
    )
    fcf_margin = free_cash_flow / revenue_value * 100.0 if free_cash_flow is not None else None
    add_metric(
        "fcf_margin",
        "Free-cash-flow margin",
        fcf_margin,
        _linear_score(fcf_margin, -10.0, 25.0) if fcf_margin is not None else None,
    )
    asset_rows = _instant_values(concepts["assets"])
    liabilities_by_end = {item["end"]: item for item in _instant_values(concepts["liabilities"])}
    liability_ratio = None
    for assets in asset_rows:
        liabilities = liabilities_by_end.get(assets["end"])
        if liabilities and assets["value"] > 0:
            liability_ratio = liabilities["value"] / assets["value"] * 100.0
            break
    add_metric(
        "liabilities_to_assets",
        "Liabilities to assets",
        liability_ratio,
        round(100.0 - _linear_score(liability_ratio, 20.0, 95.0), 1)
        if liability_ratio is not None
        else None,
    )
    score = round(sum(item["score"] for item in metrics) / len(metrics), 1) if metrics else None
    return {
        "status": "AVAILABLE" if len(metrics) == 5 else "PARTIAL",
        "score": score,
        "tier": score_tier(score),
        "metrics": metrics,
        "period_end": end,
        "filed_at": current["filed"],
        "coverage": len(metrics),
        "methodology": "filing-quality-v1",
        "limitation": (
            "Current filing-quality snapshot using absolute bands; it is not sector-relative, "
            "a return forecast, or a point-in-time backtest result."
        ),
    }


def parse_sec_filings(payload: dict[str, Any], cik: str) -> dict[str, Any]:
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])
    filed_dates = recent.get("filingDate", [])
    accepted = recent.get("acceptanceDateTime", [])
    items: list[dict[str, Any]] = []
    for index, form in enumerate(forms if isinstance(forms, list) else []):
        if form not in {"10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A"}:
            continue
        if not all(index < len(values) for values in (accessions, documents, filed_dates)):
            continue
        accession = str(accessions[index])
        document = str(documents[index])
        if not accession.replace("-", "").isdigit() or not _SAFE_DOCUMENT.fullmatch(document):
            continue
        link = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{document}"
        )
        items.append(
            {
                "form": form,
                "filed_at": str(filed_dates[index]),
                "accepted_at": str(accepted[index]) if index < len(accepted) else None,
                "accession": accession,
                "url": link,
            }
        )
        if len(items) == 6:
            break
    return {"status": "AVAILABLE" if items else "UNAVAILABLE", "items": items}


def parse_alpha_news(
    payload: dict[str, Any],
    symbol: str,
    *,
    now: datetime | None = None,
    max_age_days: int = 7,
) -> dict[str, Any]:
    reference = now or datetime.now(timezone.utc)
    feed = payload.get("feed", [])
    observations: list[tuple[float, float]] = []
    headlines: list[dict[str, Any]] = []
    for item in feed[:50] if isinstance(feed, list) else []:
        try:
            published = datetime.strptime(
                str(item.get("time_published", "")), "%Y%m%dT%H%M%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        age = reference - published
        if age.total_seconds() < -300 or age > timedelta(days=max_age_days):
            continue
        match = next(
            (
                entry
                for entry in item.get("ticker_sentiment", [])
                if str(entry.get("ticker", "")).upper() == symbol
            ),
            None,
        )
        score = _finite(match.get("ticker_sentiment_score")) if match else None
        relevance = _finite(match.get("relevance_score")) if match else None
        if score is None or relevance is None or relevance < 0.05:
            continue
        observations.append((max(0.0, min(1.0, relevance)), max(-1.0, min(1.0, score))))
        if len(headlines) < 5:
            headlines.append(
                {
                    "title": str(item.get("title", ""))[:240],
                    "source": str(item.get("source", ""))[:80],
                    "published_at": published.isoformat(),
                    "sentiment": round(score, 3),
                }
            )
    weight = sum(item[0] for item in observations)
    average = sum(relevance * score for relevance, score in observations) / weight if weight else None
    score = round(_clip(50.0 + average * 50.0), 1) if average is not None else None
    latest = max((item["published_at"] for item in headlines), default=None)
    return {
        "status": "AVAILABLE" if score is not None else "UNAVAILABLE",
        "score": score,
        "tier": score_tier(score),
        "article_count": len(observations),
        "content_as_of": latest,
        "headlines": headlines,
        "limitation": "Provider-assigned news tone; it can be noisy and is capped at 10% of rank.",
    }


def parse_alpha_overview(
    payload: dict[str, Any], expected_symbol: str | None = None
) -> dict[str, Any]:
    """Extract bounded company context without turning estimates into signal inputs."""
    symbol = str(payload.get("Symbol", "")).strip().upper()
    description = str(payload.get("Description", "")).strip()[:2_000]
    if not symbol or not description or (expected_symbol and symbol != expected_symbol.upper()):
        return {
            "status": "UNAVAILABLE",
            "profile": {},
            "reason": "A current company overview was not available.",
        }

    def text_field(key: str, limit: int = 160) -> str | None:
        value = str(payload.get(key, "")).strip()
        return value[:limit] or None

    def number_field(key: str) -> float | None:
        return _finite(payload.get(key))

    currency = str(payload.get("Currency", "")).strip().upper()
    profile = {
        "symbol": symbol,
        "name": text_field("Name"),
        "description": description,
        "sector": text_field("Sector"),
        "industry": text_field("Industry"),
        "exchange": text_field("Exchange", 40),
        "currency": currency if currency in _DISPLAY_CURRENCIES else None,
        "country": text_field("Country", 80),
        "fiscal_year_end": text_field("FiscalYearEnd", 32),
        "latest_quarter": text_field("LatestQuarter", 20),
        "market_cap": number_field("MarketCapitalization"),
        "ebitda": number_field("EBITDA"),
        "pe_ratio": number_field("PERatio"),
        "peg_ratio": number_field("PEGRatio"),
        "dividend_yield": number_field("DividendYield"),
        "profit_margin": number_field("ProfitMargin"),
        "operating_margin": number_field("OperatingMarginTTM"),
        "return_on_equity": number_field("ReturnOnEquityTTM"),
        "analyst_target_price": number_field("AnalystTargetPrice"),
        "source": "Alpha Vantage company overview",
    }
    return {
        "status": "AVAILABLE",
        "profile": {key: value for key, value in profile.items() if value is not None},
        "limitation": (
            "Current descriptive and valuation context only. Provider estimates and valuation "
            "fields are display-only and do not affect the research posture."
        ),
    }


def parse_finnhub_social(
    payload: dict[str, Any],
    expected_symbol: str | None = None,
    *,
    now: datetime | None = None,
    max_age_days: int = 7,
) -> dict[str, Any]:
    returned_symbol = str(payload.get("symbol", "")).strip().upper()
    if expected_symbol and returned_symbol != expected_symbol.upper():
        return {
            "status": "UNAVAILABLE",
            "score": None,
            "tier": "UNRATED",
            "mentions": 0,
            "platform_counts": {},
            "content_as_of": None,
            "limitation": "The provider returned social data for a different symbol.",
        }
    reference = now or datetime.now(timezone.utc)
    observations = []
    observed_times: list[datetime] = []
    platform_counts: dict[str, int] = {}
    for platform in ("reddit", "twitter"):
        rows = payload.get(platform, [])
        if not isinstance(rows, list):
            continue
        for row in rows[:200]:
            raw_time = str(row.get("atTime", "")).strip()
            try:
                observed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                observed = observed.astimezone(timezone.utc)
            except ValueError:
                continue
            age = reference - observed
            if age.total_seconds() < -300 or age > timedelta(days=max_age_days):
                continue
            positive = _finite(row.get("positiveMention")) or 0.0
            negative = _finite(row.get("negativeMention")) or 0.0
            mention = _finite(row.get("mention")) or positive + negative
            if mention <= 0:
                continue
            tone = (positive - negative) / max(positive + negative, 1.0)
            observations.append((mention, max(-1.0, min(1.0, tone))))
            observed_times.append(observed)
            platform_counts[platform] = platform_counts.get(platform, 0) + int(mention)
    weight = sum(item[0] for item in observations)
    average = sum(mention * tone for mention, tone in observations) / weight if weight else None
    score = round(_clip(50.0 + average * 50.0), 1) if average is not None else None
    return {
        "status": "AVAILABLE" if score is not None else "UNAVAILABLE",
        "score": score,
        "tier": score_tier(score),
        "mentions": int(weight),
        "platform_counts": platform_counts,
        "content_as_of": max(observed_times).isoformat() if observed_times else None,
        "limitation": (
            "Social mood is attention/risk context, not financial strength; it is capped at "
            "5% of rank and never triggers a trade."
        ),
    }


class ResearchFeedService:
    def __init__(self) -> None:
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._cache_limit = 128
        self._ticker_map_lock = asyncio.Lock()

    def _read_cache(self, key: str, ttl: int, refresh: bool) -> Any | None:
        cached = self._cache.get(key)
        if refresh or not cached or time.monotonic() - cached[0] >= ttl:
            return None
        self._cache.move_to_end(key)
        return cached[1]

    def _write_cache(self, key: str, value: Any) -> Any:
        self._cache[key] = (time.monotonic(), value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)
        return value

    async def _json(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int = 12_000_000,
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", url, params=params, headers=headers) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("research feed exceeded the response-size limit")
                    chunks.append(chunk)
        parsed = json.loads(b"".join(chunks))
        if not isinstance(parsed, dict):
            raise ValueError("research feed returned an unexpected payload")
        return parsed

    async def _ticker_map(self) -> dict[str, dict[str, str]]:
        cached = self._read_cache("sec:ticker-map", 86_400, False)
        if cached is not None:
            return cached
        async with self._ticker_map_lock:
            cached = self._read_cache("sec:ticker-map", 86_400, False)
            if cached is not None:
                return cached
            user_agent = os.getenv("INTELLIDHAN_SEC_USER_AGENT", "").strip()
            if not user_agent:
                raise LookupError("INTELLIDHAN_SEC_USER_AGENT is not configured")
            payload = await self._json(
                SEC_TICKERS_URL,
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                max_bytes=5_000_000,
            )
            mapping = {
                str(item.get("ticker", "")).upper(): {
                    "symbol": str(item.get("ticker", "")).upper(),
                    "cik": str(item.get("cik_str", "")).zfill(10),
                    "name": str(item.get("title", ""))[:160],
                }
                for item in payload.values()
                if isinstance(item, dict)
                and item.get("ticker")
                and item.get("cik_str") is not None
            }
            return self._write_cache("sec:ticker-map", mapping)

    async def search(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        """Search the authoritative SEC registrant index by ticker or company name."""
        clean = " ".join(query.strip().upper().split())
        if len(clean) < 1 or len(clean) > 80:
            raise ValueError("search query must be 1-80 characters")
        if not 1 <= limit <= 12:
            raise ValueError("search limit must be between 1 and 12")
        try:
            mapping = await self._ticker_map()
        except LookupError as exc:
            return {
                "query": clean,
                "status": "NOT_CONFIGURED",
                "results": [],
                "reason": str(exc),
                "source": "SEC company_tickers",
            }
        scored = []
        for item in mapping.values():
            symbol = item["symbol"]
            name = item["name"].upper()
            if symbol == clean:
                rank = 0
            elif symbol.startswith(clean):
                rank = 1
            elif name.startswith(clean):
                rank = 2
            elif clean in name:
                rank = 3
            else:
                continue
            scored.append((rank, len(name), len(symbol), symbol, item))
        results = [item for *_, item in sorted(scored)[:limit]]
        return {
            "query": clean,
            "status": "AVAILABLE",
            "results": results,
            "source": "SEC company_tickers",
            "limitation": "US SEC registrants only; funds, indices, and some foreign listings may be absent.",
        }

    async def _sec(self, symbol: str, *, refresh: bool) -> dict[str, Any]:
        user_agent = os.getenv("INTELLIDHAN_SEC_USER_AGENT", "").strip()
        if not user_agent:
            return {
                "status": "NOT_CONFIGURED",
                "source": "SEC EDGAR",
                "reason": "SEC filing context is not configured.",
            }
        try:
            # Registrant mappings change slowly and are shared across symbols.
            # A manual data refresh must not fan out duplicate ticker-map calls.
            mapping = await self._ticker_map()
            registrant = mapping.get(symbol)
            if not registrant:
                raise LookupError(f"No SEC registrant mapping was found for {symbol}.")
            cik = registrant["cik"]
            key = f"sec:{symbol}"
            cached = self._read_cache(key, 21_600, refresh)
            if cached is not None:
                return cached
            headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
            facts, submissions = await asyncio.gather(
                self._json(
                    f"{SEC_DATA_ROOT}/api/xbrl/companyfacts/CIK{cik}.json",
                    headers=headers,
                ),
                self._json(
                    f"{SEC_DATA_ROOT}/submissions/CIK{cik}.json",
                    headers=headers,
                    max_bytes=6_000_000,
                ),
            )
            return self._write_cache(
                key,
                {
                    "status": "AVAILABLE",
                    "source": "SEC EDGAR",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "cik": cik,
                    "company_name": str(submissions.get("name", ""))[:160],
                    "profile": {
                        "name": str(submissions.get("name", ""))[:160],
                        "sic": str(submissions.get("sic", ""))[:8] or None,
                        "industry": str(submissions.get("sicDescription", ""))[:160] or None,
                        "fiscal_year_end": str(submissions.get("fiscalYearEnd", ""))[:4] or None,
                        "exchanges": [str(item)[:40] for item in submissions.get("exchanges", [])[:8]],
                        "tickers": [str(item)[:20] for item in submissions.get("tickers", [])[:8]],
                        "website": str(submissions.get("website", ""))[:240] or None,
                        "investor_website": str(submissions.get("investorWebsite", ""))[:240] or None,
                        "description": (
                            "SEC registrant identity and filing profile. A plain-language business "
                            "description is not available from this source."
                        ),
                        "source": "SEC submissions",
                    },
                    "fundamentals": parse_sec_fundamentals(facts),
                    "filings": parse_sec_filings(submissions, cik),
                },
            )
        except LookupError as exc:
            return {"status": "UNAVAILABLE", "source": "SEC EDGAR", "reason": str(exc)[:240]}
        except Exception:
            return {
                "status": "UNAVAILABLE",
                "source": "SEC EDGAR",
                "reason": "SEC filing data could not be refreshed.",
            }

    async def _news(self, symbol: str, *, refresh: bool) -> dict[str, Any]:
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
        if not api_key:
            return {
                "status": "NOT_CONFIGURED",
                "source": "Alpha Vantage",
                "reason": "The current news feed is not configured.",
            }
        key = f"alpha-news:{symbol}"
        cached = self._read_cache(key, 900, refresh)
        if cached is not None:
            return cached
        try:
            observed_at = datetime.now(timezone.utc)
            payload = await self._json(
                ALPHA_VANTAGE_URL,
                params={
                    "function": "NEWS_SENTIMENT",
                    "tickers": symbol,
                    "sort": "LATEST",
                    "limit": "50",
                    "time_from": (observed_at - timedelta(days=7)).strftime("%Y%m%dT%H%M"),
                    "apikey": api_key,
                },
                max_bytes=4_000_000,
            )
            if payload.get("Information") or payload.get("Note"):
                raise LookupError(str(payload.get("Information") or payload.get("Note"))[:200])
            result = parse_alpha_news(payload, symbol, now=observed_at)
            result.update({"source": "Alpha Vantage", "observed_at": observed_at.isoformat()})
            return self._write_cache(key, result)
        except LookupError:
            return {
                "status": "UNAVAILABLE",
                "source": "Alpha Vantage",
                "reason": "The current news feed is unavailable or rate-limited.",
            }
        except Exception:
            # httpx exception strings may contain the request URL.  The Alpha
            # Vantage API key is a query parameter, so never reflect it to the
            # authenticated browser or write it to our research snapshot.
            return {
                "status": "UNAVAILABLE",
                "source": "Alpha Vantage",
                "reason": "The current news feed could not be refreshed.",
            }

    async def _overview(self, symbol: str, *, refresh: bool) -> dict[str, Any]:
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
        if not api_key:
            return {
                "status": "NOT_CONFIGURED",
                "source": "Alpha Vantage company overview",
                "profile": {},
                "reason": "The company overview feed is not configured.",
            }
        key = f"alpha-overview:{symbol}"
        cached = self._read_cache(key, 21_600, refresh)
        if cached is not None:
            return cached
        try:
            payload = await self._json(
                ALPHA_VANTAGE_URL,
                params={"function": "OVERVIEW", "symbol": symbol, "apikey": api_key},
                max_bytes=1_000_000,
            )
            if payload.get("Information") or payload.get("Note"):
                raise LookupError
            result = parse_alpha_overview(payload, symbol)
            result.update(
                {
                    "source": "Alpha Vantage company overview",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                }
            )
            return self._write_cache(key, result)
        except LookupError:
            return {
                "status": "UNAVAILABLE",
                "source": "Alpha Vantage company overview",
                "profile": {},
                "reason": "The company overview feed is unavailable or rate-limited.",
            }
        except Exception:
            return {
                "status": "UNAVAILABLE",
                "source": "Alpha Vantage company overview",
                "profile": {},
                "reason": "The company overview feed could not be refreshed.",
            }

    async def _social(self, symbol: str, *, refresh: bool) -> dict[str, Any]:
        api_key = os.getenv("FINNHUB_API_KEY", "").strip()
        if not api_key:
            return {
                "status": "NOT_CONFIGURED",
                "source": "Finnhub",
                "reason": "The social-attention feed is not configured.",
            }
        key = f"finnhub-social:{symbol}"
        cached = self._read_cache(key, 900, refresh)
        if cached is not None:
            return cached
        try:
            observed_at = datetime.now(timezone.utc)
            end = observed_at.date()
            payload = await self._json(
                FINNHUB_SOCIAL_URL,
                params={
                    "symbol": symbol,
                    "from": (end - timedelta(days=7)).isoformat(),
                    "to": end.isoformat(),
                },
                headers={"X-Finnhub-Token": api_key},
                max_bytes=2_000_000,
            )
            result = parse_finnhub_social(payload, symbol, now=observed_at)
            result.update({"source": "Finnhub", "as_of": observed_at.isoformat()})
            return self._write_cache(key, result)
        except Exception:
            return {
                "status": "UNAVAILABLE",
                "source": "Finnhub",
                "reason": "The social-attention feed could not be refreshed.",
            }

    async def analyze(
        self, symbol: str, technical: dict[str, Any], *, refresh: bool = False
    ) -> dict[str, Any]:
        sec, overview, news, social = await asyncio.gather(
            self._sec(symbol, refresh=refresh),
            self._overview(symbol, refresh=refresh),
            self._news(symbol, refresh=refresh),
            self._social(symbol, refresh=refresh),
        )
        fundamental = sec.get("fundamentals", {}) if sec.get("status") == "AVAILABLE" else {}
        fundamental_coverage = min(max(int(fundamental.get("coverage", 0)), 0), 5) / 5.0
        pillars = {
            "technical": {
                "status": "AVAILABLE",
                "score": _finite(technical.get("technical_score")),
                "tier": score_tier(_finite(technical.get("technical_score"))),
                "weight": 0.50,
                "effective_weight": 0.50,
                "source": "IntelliDhan completed daily scan",
            },
            "fundamentals": {
                "status": fundamental.get("status", sec.get("status", "UNAVAILABLE")),
                "score": _finite(fundamental.get("score")),
                "tier": fundamental.get("tier", "UNRATED"),
                "weight": 0.35,
                "effective_weight": round(0.35 * fundamental_coverage, 3),
                "metric_coverage": fundamental.get("coverage", 0),
                "source": "SEC EDGAR",
            },
            "news": {
                "status": news.get("status", "UNAVAILABLE"),
                "score": _finite(news.get("score")),
                "tier": news.get("tier", "UNRATED"),
                "weight": 0.10,
                "effective_weight": 0.10,
                "source": "Alpha Vantage",
            },
            "social": {
                "status": social.get("status", "UNAVAILABLE"),
                "score": _finite(social.get("score")),
                "tier": social.get("tier", "UNRATED"),
                "weight": 0.05,
                "effective_weight": 0.05,
                "source": "Finnhub",
            },
        }
        available = [item for item in pillars.values() if item["score"] is not None]
        used_weight = sum(item["effective_weight"] for item in available)
        overall = (
            round(
                sum(item["score"] * item["effective_weight"] for item in available)
                / used_weight,
                1,
            )
            if used_weight
            else None
        )
        sec_profile = sec.get("profile", {}) if sec.get("status") == "AVAILABLE" else {}
        overview_profile = (
            overview.get("profile", {}) if overview.get("status") == "AVAILABLE" else {}
        )
        company = dict(overview_profile)
        for key in (
            "name",
            "sic",
            "industry",
            "exchanges",
            "tickers",
            "website",
            "investor_website",
        ):
            if sec_profile.get(key):
                company[key] = sec_profile[key]
        if not company.get("description") and sec_profile.get("description"):
            company["description"] = sec_profile["description"]
        if sec_profile.get("fiscal_year_end"):
            company["fiscal_year_end_code"] = sec_profile["fiscal_year_end"]
        company["overview_status"] = overview.get("status")
        company["overview_limitation"] = overview.get("limitation") or overview.get("reason")
        return {
            "symbol": symbol,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "score_version": "research-rank-v1",
            "overall": {
                "score": overall,
                "tier": score_tier(overall),
                "coverage_weight": round(used_weight, 2),
                "status": "RESEARCH_ONLY",
            },
            "pillars": pillars,
            "technical": technical,
            "fundamentals": fundamental,
            "company": company,
            "filings": sec.get("filings", {"status": sec.get("status"), "items": []}),
            "news": news,
            "social": social,
            "sources": [
                {"name": "IntelliDhan scan", "status": "AVAILABLE", "as_of": technical.get("as_of")},
                {"name": "SEC EDGAR", "status": sec.get("status"), "as_of": sec.get("as_of")},
                {
                    "name": "Alpha Vantage",
                    "status": news.get("status"),
                    "as_of": news.get("content_as_of"),
                },
                {
                    "name": "Alpha Vantage company overview",
                    "status": overview.get("status"),
                    "as_of": overview.get("as_of"),
                },
                {"name": "Finnhub", "status": social.get("status"), "as_of": social.get("as_of")},
            ],
            "limitations": [
                "Overall is a transparent research rank, not a probability of profit or order signal.",
                "Unavailable pillars are excluded and weights are renormalized; coverage is shown.",
                "Filing metrics use current standard XBRL facts and are not yet sector-relative.",
                "Company overview fields are descriptive/display-only and do not affect posture.",
                "News and social tone are low-weight context because attention can reverse or be manipulated.",
            ],
        }
