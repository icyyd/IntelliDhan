"""Gateway snapshot shape tests — the /api/state payload the dashboard reads.

Constructs a fresh LiveLoop (no boot(), no network) and drives synthetic bars
through the runner directly, then asserts the snapshot exposes what the
module views need: D1/H4 indicator detail and the daily_bars candle array.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from intellidhan_gateway.live import LiveLoop
from intellidhan_schemas import Bar, Timeframe

ET = ZoneInfo("America/New_York")


def mk5(ts, px, sym="QQQ"):
    return Bar(symbol=sym, timeframe=Timeframe.M5, ts_close=ts, open=px,
               high=px + 0.6, low=px - 0.6, close=px + 0.2, volume=1e6, source="fx")


def mkd(ts, px, sym="QQQ"):
    return Bar(symbol=sym, timeframe=Timeframe.D1, ts_close=ts, open=px,
               high=px + 2, low=px - 2, close=px + 1, volume=5e7, source="fx")


def test_snapshot_exposes_d1_h4_indicators_and_daily_bars():
    loop = LiveLoop()
    sym = "QQQ"

    # seed 60 weekday daily bars -> D1 indicators warm
    daily, d = [], datetime(2026, 3, 2, 16, 0, tzinfo=ET)
    while len(daily) < 60:
        if d.weekday() < 5:
            daily.append(mkd(d, 480.0 + len(daily) * 0.5))
        d += timedelta(days=1)
    loop.runner.seed_daily(sym, daily)

    # two full 5m sessions -> H4 buckets close, plus one live D1 close
    day = daily[-1].ts_close + timedelta(days=3)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    for session in range(2):
        start = day.replace(hour=9, minute=35)
        for i in range(78):
            loop.runner.on_bar_5m(mk5(start + timedelta(minutes=5 * i),
                                      510.0 + session + i * 0.02))
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)

    snap = loop.snapshot()
    qqq = snap["symbols"][sym]

    # module views need D1/H4 EMA/RSI/ATR detail, previously only M5/M15/H1
    assert "D" in qqq["indicators"], "D1 indicator snapshot missing"
    assert "4H" in qqq["indicators"], "H4 indicator snapshot missing"
    assert qqq["indicators"]["D"]["ema21"] is not None
    assert qqq["indicators"]["D"]["atr14"] is not None

    # swing view daily candles: bounded, present after seed, and JSON-shaped
    assert "daily_bars" in qqq
    assert 0 < len(qqq["daily_bars"]) <= 150
    first = qqq["daily_bars"][0]
    assert set(first) == {"ts_close", "open", "high", "low", "close", "volume"}
    # the live D1 close from session 1 must be the newest entry
    assert qqq["daily_bars"][-1]["ts_close"] > daily[-1].ts_close.isoformat()


def test_snapshot_suppressed_entries_carry_module():
    loop = LiveLoop()
    # drive one symbol far enough that at least warmup suppressions exist
    start = datetime(2026, 6, 8, 9, 35, tzinfo=ET)
    for i in range(78):
        loop.runner.on_bar_5m(mk5(start + timedelta(minutes=5 * i), 500 + i * 0.3))
    snap = loop.snapshot()
    for s in snap["suppressed"]:
        assert s["module"] in ("0DTE", "SWING", "LEAPS", "HODL")
