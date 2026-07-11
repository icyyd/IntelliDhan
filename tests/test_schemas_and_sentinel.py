from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from intellidhan_ingestor.sentinel import check_bars, check_quote
from intellidhan_schemas import Bar, DataQuality, OptionQuote, OptionType, Quote, Timeframe

UTC = timezone.utc
T0 = datetime(2026, 7, 10, 14, 0, tzinfo=UTC)


def mk_bar(i: int, tf=Timeframe.M5, **kw) -> Bar:
    d = dict(
        symbol="QQQ", timeframe=tf, ts_close=T0 + timedelta(seconds=tf.seconds * i),
        open=100.0, high=101.0, low=99.0, close=100.5, volume=1000.0, source="test",
    )
    d.update(kw)
    return Bar(**d)


def test_bar_rejects_bad_ohlc_and_naive_ts():
    with pytest.raises(ValidationError):
        mk_bar(0, high=98.0)  # high < low
    with pytest.raises(ValidationError):
        Bar(symbol="X", timeframe=Timeframe.M5, ts_close=datetime(2026, 7, 10, 14, 0),
            open=1, high=2, low=0.5, close=1, volume=1, source="t")


def test_bar_is_immutable():
    b = mk_bar(0)
    with pytest.raises(ValidationError):
        b.close = 999  # frozen schema — payloads are facts, not mutable state


def test_sentinel_flags_gap_and_duplicates():
    bars = [mk_bar(0), mk_bar(1), mk_bar(10), mk_bar(10)]  # 45-min hole + dupe
    report = check_bars("QQQ", bars)
    assert report.quality == DataQuality.DEGRADED
    assert any("gap" in i for i in report.issues)
    assert any("duplicate" in i for i in report.issues)


def test_sentinel_ok_on_contiguous_bars():
    report = check_bars("QQQ", [mk_bar(i) for i in range(10)])
    assert report.quality == DataQuality.OK and report.issues == []


def test_sentinel_quote_checks():
    now = T0
    crossed = Quote(symbol="QQQ", ts=now, bid=101.0, ask=100.0, last=100.5, source="t")
    assert any("crossed" in i for i in check_quote(crossed, now).issues)
    stale = Quote(symbol="QQQ", ts=now - timedelta(seconds=45), last=100.5, source="t")
    assert any("stale" in i for i in check_quote(stale, now).issues)
    fresh = Quote(symbol="QQQ", ts=now, bid=100.0, ask=100.1, last=100.05, source="t")
    assert check_quote(fresh, now).quality == DataQuality.OK


def test_option_quote_liquidity_math():
    oq = OptionQuote(
        occ_symbol="QQQ260710C00560000", underlying="QQQ", option_type=OptionType.CALL,
        strike=560.0, expiry=T0, ts=T0, bid=2.40, ask=2.50, volume=500, open_interest=1200,
        source="t",
    )
    assert oq.mid == pytest.approx(2.45)
    assert oq.spread_pct_of_mid == pytest.approx(0.10 / 2.45)  # RULE-T8 gate input
