"""Offline quote-adapter tests: fixtures are not market observations or fills."""

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/paper_trading/feed.py"
SPEC = spec_from_file_location("paper_feed_test", MODULE)
feed_module = module_from_spec(SPEC)
SPEC.loader.exec_module(feed_module)
OpenDFeed, FeedError = feed_module.OpenDFeed, feed_module.FeedError
NOW = datetime.fromisoformat("2026-11-20T10:00:03-05:00")
CODE = "US.SPY261120C600000"


def option_snapshot(code=CODE, strike=600):
    return {
        "code": code, "stock_owner": "US.SPY", "option_type": "CALL",
        "strike_time": "2026-11-20", "option_valid": True,
        "option_contract_size": 100, "option_strike_price": strike,
        "suspension": False, "sec_status": "NORMAL", "option_delta": .65,
        "option_open_interest": 1000, "volume": 3000,
        "ask_price": .95, "bid_price": .90,
        "update_time": "2026-11-20 10:00:02",
    }


def chain_row(code=CODE, strike=600):
    return {
        "code": code, "stock_owner": "US.SPY", "option_type": "CALL",
        "strike_time": "2026-11-20", "option_standard_type": "STANDARD",
        "suspension": False, "strike_price": strike,
    }


def order_book(code=CODE):
    return {
        "code": code, "Bid": [(0.90, 20, 1, {})], "Ask": [(0.95, 30, 1, {})],
        "svr_recv_time_bid": "2026-11-20 10:00:00.500",
        "svr_recv_time_ask": "2026-11-20 10:00:01.500",
    }


class FakeContext:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.failure = None
        self.snapshots = {
            CODE: option_snapshot(),
            "US.SPY": {"code": "US.SPY", "last_price": 600.1,
                       "update_time": "2026-11-20 10:00:02", "suspension": False,
                       "sec_status": "NORMAL"},
        }
        self.chain = [chain_row()]
        self.books = {CODE: order_book()}
        self.quota = {"remain": 100, "option_remain_quota": 100}
        self.bar_rows = []

    def get_global_state(self):
        return 0, {"qot_logined": True, "user_id": "DO_NOT_RETURN", "trd_logined": True}

    def query_subscription(self, **kwargs):
        self.calls.append(("quota", kwargs))
        return 0, self.quota

    def subscribe(self, codes, types, **kwargs):
        self.calls.append(("subscribe", codes, types, kwargs))
        return 0, None

    def get_market_snapshot(self, codes):
        self.calls.append(("snapshot", codes))
        if self.failure:
            return -1, self.failure
        return 0, [self.snapshots[code] for code in codes]

    def get_option_chain(self, code, **kwargs):
        self.calls.append(("chain", code, kwargs))
        return 0, self.chain

    def get_order_book(self, code, **kwargs):
        self.calls.append(("book", code, kwargs))
        return 0, self.books[code]

    def get_cur_kline(self, code, **kwargs):
        self.calls.append(("bars", code, kwargs))
        return 0, self.bar_rows

    def close(self):
        self.closed = True


@pytest.fixture
def connected():
    ctx = FakeContext()
    sdk = SimpleNamespace(
        RET_OK=0, OpenQuoteContext=lambda **kwargs: ctx,
        Session=SimpleNamespace(RTH="RTH"),
        SubType=SimpleNamespace(K_5M="K_5M", ORDER_BOOK="ORDER_BOOK"),
        KLType=SimpleNamespace(K_5M="K_5M"), AuType=SimpleNamespace(QFQ="QFQ"),
        OptionType=SimpleNamespace(CALL="CALL", PUT="PUT"),
    )
    adapter = OpenDFeed(sdk=sdk)
    adapter.connect()
    return adapter, ctx


def test_health_allowlist_never_exposes_account_state(connected):
    adapter, _ = connected
    state = adapter.health()
    assert state["quote_logged_in"] is True
    assert state["status"] == "CONNECTED"
    assert "user_id" not in state and "trd_logined" not in state
    assert "DO_NOT_RETURN" not in str(state)


def test_underlying_uses_provider_time_not_request_time(connected):
    adapter, _ = connected
    quote = adapter.underlying("SPY")
    assert quote == {"symbol": "SPY", "price": 600.1,
                     "observed_at": "2026-11-20T10:00:02-05:00", "source": "MOOMOO_OPEND"}


@pytest.mark.parametrize("bad", [None, "", "0", "1970-01-01 00:00:00"])
def test_underlying_unknown_timestamp_blocks(connected, bad):
    adapter, ctx = connected
    ctx.snapshots["US.SPY"]["update_time"] = bad
    with pytest.raises(FeedError, match="authoritative"):
        adapter.underlying("SPY")


def test_option_uses_oldest_bid_ask_and_metadata_timestamp(connected):
    adapter, _ = connected
    quotes = adapter.candidates("SPY", "CALL", "2026-11-20", 600)
    assert len(quotes) == 1
    assert quotes[0]["observed_at"] == "2026-11-20T10:00:00.500000-05:00"
    assert quotes[0]["delta"] == .65
    assert quotes[0]["option_type"] == "call"
    assert quotes[0]["bid"] == .9 and quotes[0]["ask"] == .95
    assert adapter.last_scan["scope"] == "NEAREST_30_STANDARD_STRIKES"


def test_missing_book_timestamp_never_uses_recent_snapshot(connected):
    adapter, ctx = connected
    ctx.books[CODE]["svr_recv_time_bid"] = ""
    assert adapter.candidates("SPY", "CALL", "2026-11-20", 600) == []
    assert adapter.last_scan["quoted_contracts"] == 0
    assert any("bid timestamp" in reason for reason in adapter.last_scan["rejections"])


@pytest.mark.parametrize("field,value", [
    ("option_contract_size", 10), ("stock_owner", "US.QQQ"),
    ("option_type", "PUT"), ("strike_time", "2026-11-23"),
    ("option_strike_price", 601), ("option_valid", False),
    ("option_delta", float("nan")), ("option_open_interest", None),
    ("suspension", True), ("sec_status", "N/A"),
])
def test_invalid_option_metadata_cannot_be_used(connected, field, value):
    adapter, ctx = connected
    ctx.snapshots[CODE][field] = value
    assert adapter.candidates("SPY", "CALL", "2026-11-20", 600) == []


def test_crossed_or_empty_books_fail_closed(connected):
    adapter, ctx = connected
    ctx.books[CODE]["Bid"] = [(1.0, 20)]
    assert not adapter.candidates("SPY", "CALL", "2026-11-20", 600)
    ctx.books[CODE]["Bid"] = []
    assert not adapter.candidates("SPY", "CALL", "2026-11-20", 600)


def test_chain_bounded_scan_is_disclosed_and_subscribed_as_batch(connected):
    adapter, ctx = connected
    ctx.chain = []
    for strike in range(580, 620):
        code = f"US.SPY261120C{strike}000"
        ctx.chain.append(chain_row(code, strike))
        ctx.snapshots[code] = option_snapshot(code, strike)
        ctx.books[code] = order_book(code)
    result = adapter.candidates("SPY", "CALL", "2026-11-20", 600)
    assert len(result) == 8
    assert adapter.last_scan["omitted_contracts"] == 10
    assert adapter.last_scan["book_omitted_contracts"] == 22
    subscriptions = [call for call in ctx.calls if call[0] == "subscribe"]
    assert len(subscriptions) == 1 and len(subscriptions[0][1]) == 8


def test_insufficient_option_quota_does_not_subscribe(connected):
    adapter, ctx = connected
    ctx.quota["option_remain_quota"] = 0
    assert adapter.candidates("SPY", "CALL", "2026-11-20", 600) == []
    assert "subscription capacity" in adapter.last_scan["reason"]
    assert not any(call[0] == "subscribe" for call in ctx.calls)


def test_twenty_option_quota_allows_eight_reference_feasible_books(connected):
    adapter, ctx = connected
    ctx.quota["option_remain_quota"] = 20
    ctx.chain = []
    for strike in range(590, 620):
        code = f"US.SPY261120C{strike}000"
        ctx.chain.append(chain_row(code, strike))
        ctx.snapshots[code] = option_snapshot(code, strike)
        ctx.books[code] = order_book(code)
    ctx.snapshots["US.SPY261120C600000"]["ask_price"] = 5.50
    result = adapter.candidates("SPY", "CALL", "2026-11-20", 600)
    assert len(result) == 8
    assert CODE not in [row["option_id"] for row in result]
    assert adapter.last_scan["metadata_eligible_contracts"] == 29
    assert adapter.last_scan["reference_filters"]["maximum_ask_premium"] == 500


def test_owned_option_limit_preserves_existing_subscriptions(connected):
    adapter, ctx = connected
    adapter._owned_subscriptions = {(f"US.OTHER{i}", "ORDER_BOOK") for i in range(20)}
    assert adapter.candidates("SPY", "CALL", "2026-11-20", 600) == []
    assert len(adapter._owned_subscriptions) == 20
    assert not any(call[0] == "subscribe" for call in ctx.calls)


def test_zero_budget_never_fetches_or_subscribes(connected):
    adapter, ctx = connected
    assert adapter.candidates("SPY", "CALL", "2026-11-20", 600, max_premium=0) == []
    assert ctx.calls == []


def test_restart_resolves_saved_contract_from_chain(connected):
    adapter, ctx = connected
    position = {"option_id": CODE, "symbol": "SPY", "option_type": "call",
                "expiration_date": "2026-11-20"}
    quote = adapter.option_quote(position)
    assert quote["option_id"] == CODE
    assert any(call[0] == "chain" for call in ctx.calls)


def test_missing_saved_contract_is_unresolved_not_reconstructed(connected):
    adapter, ctx = connected
    ctx.chain = []
    with pytest.raises(FeedError, match="exit unresolved"):
        adapter.option_quote({"option_id": CODE, "symbol": "SPY", "option_type": "CALL",
                              "expiration_date": "2026-11-20"})


def test_bars_exclude_open_bar_and_extended_hours(connected):
    adapter, ctx = connected
    ctx.bar_rows = [
        {"code": "US.SPY", "time_key": stamp, "open": 600, "high": 601,
         "low": 599, "close": 600.5, "volume": 1000}
        for stamp in ("2026-11-19 15:55:00", "2026-11-20 09:25:00",
                      "2026-11-20 09:55:00", "2026-11-20 10:00:00")
    ]
    result = adapter.bars("SPY", NOW)
    assert [row["started_at"] for row in result] == [
        "2026-11-19T15:55:00-05:00", "2026-11-20T09:55:00-05:00",
    ]
    subscription = next(call for call in ctx.calls if call[0] == "subscribe")
    assert subscription[3]["session"] == "RTH"
    assert next(call for call in ctx.calls if call[0] == "bars")[2]["autype"] == "QFQ"


def test_provider_error_is_sanitized(connected):
    adapter, ctx = connected
    ctx.failure = "permission denied for account SECRET_ACCOUNT secret_token=abcdef"
    with pytest.raises(FeedError, match="permission") as exc:
        adapter.underlying("SPY")
    assert "SECRET_ACCOUNT" not in str(exc.value) and "abcdef" not in str(exc.value)


def test_no_non_loopback_or_unsupported_symbol():
    with pytest.raises(ValueError, match="loopback"):
        OpenDFeed(host="example.com")
    adapter = OpenDFeed(sdk=object())
    with pytest.raises(FeedError, match="SPY and QQQ"):
        adapter.underlying("SPX")


def test_close_releases_only_own_context(connected):
    adapter, ctx = connected
    adapter.close()
    assert ctx.closed and adapter._ctx is None
    assert not any("unsubscribe" in call[0] for call in ctx.calls)
