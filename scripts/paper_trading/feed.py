"""Read-only OpenD observations for the isolated local paper journal.

This module does not create a broker trading context or submit broker orders.
Option bid/ask freshness comes only from the provider's order-book timestamps;
a successful request or a recent last-trade snapshot is not a fresh quote.
"""

from __future__ import annotations

import importlib
import logging
import math
import time
from collections import deque
from datetime import date, datetime, time as day_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SOURCE = "MOOMOO_OPEND"
SYMBOLS = frozenset({"SPY", "QQQ"})


class FeedError(RuntimeError):
    """A sanitized, actionable data failure; never contains raw provider payloads."""


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise FeedError(f"OpenD is missing a valid {field}.") from None
    if isinstance(value, bool) or not math.isfinite(result) or result < 0:
        raise FeedError(f"OpenD returned an invalid {field}.")
    if positive and result <= 0:
        raise FeedError(f"OpenD returned a non-positive {field}.")
    return result


def _signed_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise FeedError(f"OpenD is missing a valid {field}.") from None
    if isinstance(value, bool) or not math.isfinite(result):
        raise FeedError(f"OpenD returned an invalid {field}.")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    # SDK US-market formatted timestamps use US Eastern time. Never replace a
    # missing/zero source timestamp with the local receipt/request time.
    if not isinstance(value, (str, datetime)) or not value:
        raise FeedError(f"OpenD has no authoritative {field}; paper fills are blocked.")
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except ValueError:
        raise FeedError(f"OpenD has no authoritative {field}; paper fills are blocked.") from None
    if result.year < 2000:
        raise FeedError(f"OpenD has no authoritative {field}; paper fills are blocked.")
    return result.replace(tzinfo=ET) if result.tzinfo is None else result


def _records(data: Any) -> list[dict]:
    if hasattr(data, "to_dict"):
        return data.to_dict("records")
    if isinstance(data, list) and all(isinstance(row, dict) for row in data):
        return data
    raise FeedError("OpenD returned an unexpected market-data format.")


def _symbol(symbol: str) -> str:
    if symbol not in SYMBOLS:
        raise FeedError("The paper feed only supports SPY and QQQ.")
    return f"US.{symbol}"


class OpenDFeed:
    """Bounded SPY/QQQ quote access; SDK injection keeps unit tests offline."""

    def __init__(self, host: str = "127.0.0.1", port: int = 11111, *, sdk=None):
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("OpenD must use a loopback address.")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Invalid OpenD port.")
        self.host, self.port = host, port
        self._sdk = sdk
        self._ctx = None
        self._owned_subscriptions: set[tuple[str, str]] = set()
        self._requests: deque[float] = deque()
        self._contracts: dict[str, dict] = {}
        self._chains: dict[tuple, tuple[float, list[dict]]] = {}
        self.last_scan: dict = {}

    def connect(self) -> None:
        if self._ctx is not None:
            return
        try:
            if self._sdk is None:
                self._sdk = importlib.import_module("moomoo")
                self._sdk.SysConfig.enable_console_log(False)
                # The SDK creates its log handler at import. The launcher must
                # isolate HOME before import; disable payload logging thereafter.
                logging.getLogger("FTFileLog").disabled = True
            self._ctx = self._sdk.OpenQuoteContext(host=self.host, port=self.port, ai_type=1)
        except (Exception, SystemExit):
            self._ctx = None
            raise FeedError(
                "Cannot connect to OpenD. Check that OpenD is running and moomoo-api "
                "is installed in this Python environment."
            ) from None

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            finally:
                self._ctx = None
                self._owned_subscriptions.clear()
                self._chains.clear()
        # Closing this quote context releases only this connection's leases.
        # Never unsubscribe_all: other applications may share the same OpenD.

    def _call(self, operation: str, *args, **kwargs):
        if self._ctx is None:
            raise FeedError("OpenD is not connected.")
        now = time.monotonic()
        while self._requests and now - self._requests[0] >= 30.1:
            self._requests.popleft()
        if len(self._requests) >= 50:
            raise FeedError("OpenD request budget reached; wait for the next polling cycle.")
        self._requests.append(now)
        try:
            result = getattr(self._ctx, operation)(*args, **kwargs)
        except Exception:
            raise FeedError(f"OpenD {operation} failed; check the local data connection.") from None
        if not isinstance(result, tuple) or len(result) != 2:
            raise FeedError(f"OpenD {operation} returned an unexpected response.")
        ret, data = result
        if ret != self._sdk.RET_OK:
            # Provider errors can contain identifiers. Classify, do not echo.
            message = str(data).lower()
            if any(word in message for word in ("permission", "right", "entitle")):
                reason = "Market-data permission is unavailable; verify US stock and options access."
            elif "login" in message or "log in" in message:
                reason = "OpenD quote login is required; sign in through the OpenD app."
            elif "quota" in message or "frequen" in message or "limit" in message:
                reason = "OpenD data quota or request limit was reached."
            else:
                reason = f"OpenD {operation} is unavailable; inspect OpenD locally."
            raise FeedError(reason)
        return data

    def health(self) -> dict:
        try:
            data = self._call("get_global_state")
            if not isinstance(data, dict):
                raise FeedError("OpenD returned an invalid connection state.")
            logged_in = data.get("qot_logined") is True
            return {
                "status": "CONNECTED" if logged_in else "BLOCKED",
                "quote_logged_in": logged_in,
                "source": SOURCE,
                "reason": (
                    "OpenD quote login connected; fresh option quotes must still pass validation."
                    if logged_in else "Sign in to market data in the OpenD app."
                ),
            }
        except FeedError as exc:
            return {"status": "BLOCKED", "quote_logged_in": False,
                    "source": SOURCE, "reason": str(exc)}

    def _subscribe(self, codes: list[str], subtype: str, *, rth: bool = False) -> None:
        missing = [code for code in codes if (code, subtype) not in self._owned_subscriptions]
        if not missing:
            return
        if len(self._owned_subscriptions) + len(missing) > 96:
            raise FeedError("Paper observer subscription cap reached; no other subscriptions changed.")
        quota = self._call("query_subscription", is_all_conn=True)
        if not isinstance(quota, dict) or _number(quota.get("remain"), "remaining quota") < len(missing):
            raise FeedError("OpenD subscription quota is insufficient for the paper observer.")
        options = [code for code in missing if code not in {"US.SPY", "US.QQQ"}]
        if options and _number(quota.get("option_remain_quota"), "option quota") < len(options):
            raise FeedError("OpenD option subscription quota is insufficient.")
        kwargs = {"subscribe_push": False}
        if rth:
            kwargs["session"] = self._sdk.Session.RTH
        self._call("subscribe", missing, [subtype], **kwargs)
        self._owned_subscriptions.update((code, subtype) for code in missing)

    def _snapshot(self, codes: list[str]) -> dict[str, dict]:
        if not codes or len(codes) > 60:
            raise FeedError("Paper snapshot batch must contain 1 to 60 instruments.")
        rows = _records(self._call("get_market_snapshot", codes))
        result = {row.get("code"): row for row in rows}
        if set(result) != set(codes) or len(rows) != len(codes):
            raise FeedError("OpenD snapshot coverage is incomplete or mismatched.")
        return result

    def underlying(self, symbol: str) -> dict:
        code = _symbol(symbol)
        row = self._snapshot([code])[code]
        if row.get("suspension") not in (False, 0) or row.get("sec_status") != "NORMAL":
            raise FeedError("The underlying is halted or its tradable state is unknown.")
        return {
            "symbol": symbol, "price": _number(row.get("last_price"), "underlying price", positive=True),
            "observed_at": _timestamp(row.get("update_time"), "underlying timestamp").isoformat(),
            "source": SOURCE,
        }

    def bars(self, symbol: str, now: datetime) -> list[dict]:
        code = _symbol(symbol)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        self._subscribe([code], self._sdk.SubType.K_5M, rth=True)
        rows = _records(self._call(
            "get_cur_kline", code, num=1000, ktype=self._sdk.KLType.K_5M,
            autype=self._sdk.AuType.QFQ,
        ))
        result, seen = [], set()
        for row in rows:
            if row.get("code") != code:
                raise FeedError("OpenD returned bars for a different underlying.")
            started = _timestamp(row.get("time_key"), "bar timestamp").astimezone(ET)
            if not day_time(9, 30) <= started.time() < day_time(16, 0):
                continue
            if started + timedelta(minutes=5) > now:
                continue
            if started.minute % 5 or started.second or started.microsecond or started in seen:
                raise FeedError("OpenD returned duplicate or misaligned five-minute bars.")
            seen.add(started)
            values = {key: _number(row.get(key), f"bar {key}", positive=True)
                      for key in ("open", "high", "low", "close")}
            if values["low"] > min(values["open"], values["close"]) or values["high"] < max(
                values["open"], values["close"]
            ):
                raise FeedError("OpenD returned inconsistent bar prices.")
            result.append({"started_at": started.isoformat(), **values,
                           "volume": _number(row.get("volume"), "bar volume")})
        return sorted(result, key=lambda row: row["started_at"])

    def _chain(self, symbol: str, direction: str, session_date: str) -> list[dict]:
        code = _symbol(symbol)
        if direction not in {"CALL", "PUT"}:
            raise FeedError("Option direction must be CALL or PUT.")
        try:
            expiry = date.fromisoformat(str(session_date)).isoformat()
        except ValueError:
            raise FeedError("Invalid option expiration date.") from None
        key = (symbol, direction, expiry)
        cached = self._chains.get(key)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        rows = _records(self._call(
            "get_option_chain", code, start=expiry, end=expiry,
            option_type=getattr(self._sdk.OptionType, direction),
        ))
        valid = []
        for row in rows:
            if (row.get("stock_owner") != code or row.get("option_type") != direction
                    or str(row.get("strike_time"))[:10] != expiry
                    or row.get("option_standard_type") != "STANDARD"
                    or row.get("suspension") not in (False, 0)):
                continue
            option_id = row.get("code")
            if not isinstance(option_id, str) or not option_id.startswith(f"US.{symbol}"):
                continue
            contract = {
                "option_id": option_id, "symbol": symbol, "option_type": direction,
                "expiration_date": expiry,
                "strike": _number(row.get("strike_price"), "strike price", positive=True),
            }
            valid.append(contract)
            self._contracts[option_id] = contract
        self._chains[key] = (time.monotonic(), valid)
        return valid

    def _quote(self, contract: dict, row: dict) -> dict:
        code = contract["option_id"]
        if (row.get("option_valid") not in (True, 1)
                or row.get("stock_owner") != _symbol(contract["symbol"])
                or row.get("option_type") != contract["option_type"]
                or str(row.get("strike_time"))[:10] != contract["expiration_date"]
                or _number(row.get("option_contract_size"), "contract size") != 100
                or _number(row.get("option_strike_price"), "strike price") != contract["strike"]
                or row.get("suspension") not in (False, 0)
                or row.get("sec_status") != "NORMAL"):
            raise FeedError("Option identity or standard-contract status could not be verified.")
        self._subscribe([code], self._sdk.SubType.ORDER_BOOK)
        book = self._call("get_order_book", code, num=1)
        if not isinstance(book, dict) or book.get("code") != code:
            raise FeedError("OpenD returned a mismatched option order book.")
        bid_at = _timestamp(book.get("svr_recv_time_bid"), "option bid timestamp")
        ask_at = _timestamp(book.get("svr_recv_time_ask"), "option ask timestamp")
        try:
            bid = _number(book["Bid"][0][0], "option bid", positive=True)
            ask = _number(book["Ask"][0][0], "option ask", positive=True)
            bid_size = _number(book["Bid"][0][1], "option bid size", positive=True)
            ask_size = _number(book["Ask"][0][1], "option ask size", positive=True)
        except (KeyError, IndexError, TypeError):
            raise FeedError("OpenD option order book is missing one side.") from None
        if bid > ask:
            raise FeedError("OpenD returned a crossed option market.")
        delta = _signed_number(row.get("option_delta"), "option delta")
        if not -1 <= delta <= 1 or (contract["option_type"] == "CALL" and delta < 0) or (
            contract["option_type"] == "PUT" and delta > 0
        ):
            raise FeedError("OpenD returned an invalid option delta.")
        snapshot_at = _timestamp(row.get("update_time"), "option metadata timestamp")
        return {
            **contract, "option_type": contract["option_type"].lower(),
            "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size,
            "delta": delta, "volume": _number(row.get("volume"), "option volume"),
            "open_interest": _number(row.get("option_open_interest"), "open interest"),
            # Conservative: the oldest required source observation controls age.
            "observed_at": min(bid_at, ask_at, snapshot_at).isoformat(),
            "bid_observed_at": bid_at.isoformat(), "ask_observed_at": ask_at.isoformat(),
            "metadata_observed_at": snapshot_at.isoformat(),
            "timestamp_basis": "PROVIDER_ORDER_BOOK_AND_SNAPSHOT", "source": SOURCE,
        }

    def candidates(self, symbol: str, direction: str, session_date: str,
                   underlying_price: float, max_premium: float = 500) -> list[dict]:
        price = _number(underlying_price, "underlying price", positive=True)
        budget = min(500.0, _number(max_premium, "paper premium budget"))
        if budget == 0:
            self.last_scan = {"scope": "NEAREST_30_STANDARD_STRIKES", "symbol": symbol,
                              "direction": direction, "quoted_contracts": 0,
                              "reason": "No paper premium budget remains."}
            return []
        chain = self._chain(symbol, direction, session_date)
        selected = sorted(chain, key=lambda row: (abs(row["strike"] - price), row["strike"]))[:30]
        self.last_scan = {
            "scope": "NEAREST_30_STANDARD_STRIKES", "symbol": symbol, "direction": direction,
            "chain_contracts": len(chain), "scanned_contracts": len(selected),
            "omitted_contracts": max(0, len(chain) - len(selected)),
            "quoted_contracts": 0, "rejections": {},
            "book_scope": "UP_TO_8_METADATA_FEASIBLE_CONTRACTS",
            "reference_filters": {"minimum_absolute_delta": .5, "maximum_absolute_delta": .8,
                                  "minimum_volume": 100, "minimum_open_interest": 100,
                                  "maximum_ask_premium": budget},
        }
        if not selected:
            return []
        snapshots = self._snapshot([row["option_id"] for row in selected])
        # Snapshot prices only narrow the subscription universe. They never
        # authorize a fill; the actual book and ledger recheck price/age/risk.
        feasible = []
        for contract in selected:
            row = snapshots[contract["option_id"]]
            try:
                delta = _signed_number(row.get("option_delta"), "option delta")
                if not .5 <= abs(delta) <= .8:
                    continue
                if (direction == "CALL") != (delta > 0):
                    continue
                if (_number(row.get("volume"), "option volume") < 100
                        or _number(row.get("option_open_interest"), "open interest") < 100
                        or _number(row.get("ask_price"), "reference ask", positive=True) * 100 > budget):
                    continue
                feasible.append(contract)
            except FeedError:
                continue
        feasible.sort(key=lambda row: (
            -abs(float(snapshots[row["option_id"]]["option_delta"])),
            abs(row["strike"] - price), row["strike"],
        ))
        quota = self._call("query_subscription", is_all_conn=True)
        option_remaining = int(_number(quota.get("option_remain_quota"), "option quota"))
        general_remaining = int(_number(quota.get("remain"), "remaining quota"))
        owned_options = {code for code, subtype in self._owned_subscriptions
                         if subtype == self._sdk.SubType.ORDER_BOOK}
        available = min(option_remaining, general_remaining, max(0, 20 - len(owned_options)))
        books = []
        for contract in feasible:
            if len(books) >= 8:
                break
            if contract["option_id"] not in owned_options:
                if available <= 0:
                    continue
                available -= 1
            books.append(contract)
        self.last_scan.update(metadata_eligible_contracts=len(feasible),
                              book_requested_contracts=len(books),
                              book_omitted_contracts=len(feasible) - len(books),
                              option_subscription_limit=20)
        if not books:
            self.last_scan["reason"] = (
                "No contract passed reference price, delta, volume and open-interest filters."
                if not feasible else "No option subscription capacity remains; no subscriptions changed."
            )
            return []
        self._subscribe([row["option_id"] for row in books], self._sdk.SubType.ORDER_BOOK)
        quotes = []
        for contract in books:
            try:
                quotes.append(self._quote(contract, snapshots[contract["option_id"]]))
            except FeedError as exc:
                reason = str(exc)
                self.last_scan["rejections"][reason] = self.last_scan["rejections"].get(reason, 0) + 1
        self.last_scan["quoted_contracts"] = len(quotes)
        return quotes

    def option_quote(self, position: dict) -> dict:
        code = position.get("option_id")
        if not code:
            raise FeedError("The paper position is missing its option identity.")
        contract = self._contracts.get(code)
        if contract is None:
            # After restart resolve against the provider's chain, not a fabricated code.
            self._chain(position["symbol"], position["option_type"].upper(),
                        position["expiration_date"])
            contract = self._contracts.get(code)
        if contract is None:
            raise FeedError("The saved contract is absent from the provider chain; exit unresolved.")
        row = self._snapshot([code])[code]
        return self._quote(contract, row)
