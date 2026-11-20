"""Private read-only research adapter tests; none of these quotes are real."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trading.feed import ET, FeedError, OpenDFeed  # noqa: E402
from paper_trading.late_option_feed import LateOptionFeed  # noqa: E402


def test_research_reserves_shared_quota_without_altering_provider_state(monkeypatch):
    raw = {"remain": 17, "option_remain_quota": 14}
    calls = []
    def fake(self, op, *args, **kwargs):
        calls.append(op)
        return raw
    monkeypatch.setattr(OpenDFeed, "_call", fake)
    feed = LateOptionFeed(sdk=SimpleNamespace(SubType=SimpleNamespace(ORDER_BOOK="BOOK")))
    assert feed._call("query_subscription") == {"remain": 5, "option_remain_quota": 6}
    feed._owned_subscriptions.update((str(i), "BOOK") for i in range(11))
    assert feed._call("query_subscription")["option_remain_quota"] == 1
    with pytest.raises(FeedError):
        feed._call("unsubscribe_all")
    assert calls == ["query_subscription", "query_subscription"]
    assert raw == {"remain": 17, "option_remain_quota": 14}
    raw["option_remain_quota"] = None
    with pytest.raises(FeedError):
        feed._call("query_subscription")


def test_complete_bars_cached_only_for_current_closed_bucket(monkeypatch):
    morning = datetime(2026, 11, 20, 9, 30, tzinfo=ET)
    rows = [{"started_at": (morning + timedelta(minutes=5*i)).isoformat()} for i in range(12)]
    calls = []
    def bars(self, symbol, now):
        calls.append(now)
        return list(rows)
    monkeypatch.setattr(OpenDFeed, "bars", bars)
    feed = LateOptionFeed()
    now = morning + timedelta(hours=1)
    feed.bars("SPY", now)
    feed.bars("SPY", now + timedelta(seconds=5))
    assert len(calls) == 1
    # At the next boundary a missing newly closed bar must not freeze a gap.
    feed.bars("SPY", now + timedelta(minutes=5))
    rows.append({"started_at": now.isoformat()})
    assert len(feed.bars("SPY", now + timedelta(minutes=5, seconds=5))) == 13
    feed.bars("SPY", now + timedelta(minutes=5, seconds=10))
    assert len(calls) == 3
    feed.close()
    feed.bars("SPY", now + timedelta(minutes=5, seconds=15))
    assert len(calls) == 4


def test_optional_greeks_cannot_create_or_reprice_a_verified_book(monkeypatch):
    monkeypatch.setattr(OpenDFeed, "_quote", lambda *args: {"ask": 2, "bid": 1.97})
    got = LateOptionFeed()._quote({}, {"option_implied_volatility": 17.3, "option_gamma": float("nan"),
                                      "option_theta": -0.32, "option_vega": False})
    assert got == {"ask": 2, "bid": 1.97, "contract_size": 100, "provider_implied_volatility": 17.3,
                   "provider_gamma": None, "provider_theta": -0.32, "provider_vega": None}
