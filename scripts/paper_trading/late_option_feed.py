"""Quote-only OpenD adapter for the optional late-window research process."""
from datetime import datetime, timedelta
from math import isfinite

from .feed import ET, FeedError, OpenDFeed, _number


class LateOptionFeed(OpenDFeed):
    # Reserve capacity for the primary observer, including a new candidate scan.
    # These are local limits, not representations of the user's paid entitlements.
    GENERAL_RESERVE = 12
    OPTION_RESERVE = 8
    OWN_BOOK_CAP = 12
    ALLOWED = frozenset({"get_global_state", "query_subscription", "subscribe", "get_cur_kline",
                         "get_market_snapshot", "get_option_chain", "get_order_book"})

    def __init__(self, *, sdk=None):
        super().__init__(sdk=sdk)
        self._bar_cache = {}

    def _call(self, operation, *args, **kwargs):
        if operation not in self.ALLOWED:
            raise FeedError("Late-window research supports market-data observations only.")
        data = super()._call(operation, *args, **kwargs)
        if operation != "query_subscription":
            return data
        if not isinstance(data, dict):
            raise FeedError("Cannot verify available market-data subscriptions.")
        def slots(key):
            n = _number(data.get(key), "subscription quota")
            if not n.is_integer():
                raise FeedError("Invalid subscription quota.")
            return int(n)
        owned_books = sum(subtype == self._sdk.SubType.ORDER_BOOK
                          for _, subtype in self._owned_subscriptions)
        return {**data, "remain": max(0, slots("remain") - self.GENERAL_RESERVE),
                "option_remain_quota": max(0, min(slots("option_remain_quota") - self.OPTION_RESERVE,
                                                  self.OWN_BOOK_CAP - owned_books))}

    def bars(self, symbol, now):
        # Reuse only completed candles within the same five-minute bucket.
        # An opening gap, a delayed new candle or a provider error is not cached.
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The observation clock must have a timezone")
        end = now.astimezone(ET).replace(second=0, microsecond=0)
        end -= timedelta(minutes=end.minute % 5)
        cached = self._bar_cache.get(symbol)
        if cached and cached[0] == end:
            return cached[1]
        rows = super().bars(symbol, now)
        expected = end.replace(hour=9, minute=30)
        today = [datetime.fromisoformat(row["started_at"]) for row in rows
                 if row["started_at"][:10] == end.date().isoformat()]
        if today and today == [expected + timedelta(minutes=5*i)
                               for i in range(int((end-expected).total_seconds() // 300))]:
            self._bar_cache[symbol] = (end, rows)
        return rows

    def _quote(self, contract, row):
        # The shared adapter verifies contract identity and multiplier before
        # returning a book. Optional model inputs are never fill prices.
        quote = super()._quote(contract, row)
        for name in ("implied_volatility", "gamma", "theta", "vega"):
            value = row.get("option_" + name)
            try:
                n = float(value)
                valid = not isinstance(value, bool) and isfinite(n) and (name == "theta" or n >= 0)
            except (TypeError, ValueError, OverflowError):
                valid = False
            quote["provider_" + name] = n if valid else None
        quote["contract_size"] = 100
        return quote

    def close(self):
        self._bar_cache.clear()
        super().close()
