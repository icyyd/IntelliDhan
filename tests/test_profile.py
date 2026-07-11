"""Market Profile builder tests: synthetic geometry + golden-fixture sanity."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from intellidhan_analytics.profile import (
    OneTimeframing,
    OpenType,
    ProfileBuilder,
    Shape,
)
from intellidhan_ingestor.backfill import read_recording
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_schemas import Bar, Timeframe

ET = ZoneInfo("America/New_York")
FIXTURE = Path(__file__).parent.parent / "fixtures/golden-sessions/qqq-complex-5m.jsonl"


def bars_from(prices, day=10, vol=10_000.0, sym="T"):
    """prices: list of (o,h,l,c) starting at 9:35 ET in 5m steps."""
    t0 = datetime(2026, 7, day, 9, 35, tzinfo=ET)
    return [
        Bar(symbol=sym, timeframe=Timeframe.M5, ts_close=t0 + timedelta(minutes=5 * i),
            open=o, high=h, low=lo, close=c, volume=vol, source="fx")
        for i, (o, h, lo, c) in enumerate(prices)
    ]


def drive(builder, bars):
    clock = MarketClock()
    state = None
    for b in bars:
        state = builder.update(b, clock.session_id(b.ts_close))
    return state


def test_open_drive_up_detection():
    # opens at the low of period 1 and never looks back; period 2 extends higher
    prices = [(100 + i * 0.5, 100.7 + i * 0.5, 99.9 + i * 0.5, 100.6 + i * 0.5)
              for i in range(14)]  # 70 minutes = periods 1-3, one-way up
    st = drive(ProfileBuilder("T"), bars_from(prices))
    assert st.open_type == OpenType.OPEN_DRIVE
    assert st.one_timeframing == OneTimeframing.UP
    assert st.range_ext_up and not st.range_ext_down
    assert st.trend_day_probability > 0.5


def test_open_auction_rotation():
    # rotates around the open both directions repeatedly
    seq = []
    for i in range(14):
        up = i % 2 == 0
        seq.append((100.0, 100.9, 99.1, 100.6 if up else 99.4))
    st = drive(ProfileBuilder("T"), bars_from(seq))
    assert st.open_type in (OpenType.OPEN_AUCTION, OpenType.OPEN_REJECTION_REVERSE)
    assert st.one_timeframing == OneTimeframing.NONE
    assert st.trend_day_probability < 0.5
    assert st.shape in (Shape.BALANCED, Shape.P_SHAPE, Shape.B_SHAPE)


def test_value_area_and_poc_geometry():
    # heavy trade at 100, thin wings -> POC ~100, VA containing 100
    seq = ([(100.0, 100.2, 99.8, 100.0)] * 8          # dense middle
           + [(100.0, 101.5, 99.9, 101.2)] * 2        # brief probe up
           + [(101.0, 101.2, 99.9, 100.1)] * 4)       # back to value
    st = drive(ProfileBuilder("T"), bars_from(seq))
    assert st.poc is not None and abs(st.poc - 100.0) < 0.4
    assert st.va_low <= st.poc <= st.va_high
    assert st.va_high - st.va_low < 1.6                # 70% VA excludes the thin probe


def test_ib_and_session_reset():
    b = ProfileBuilder("T")
    day1 = bars_from([(100, 101, 99, 100.5)] * 8, day=9)
    st1 = drive(b, day1)
    assert st1.ib_high == 101 and st1.ib_low == 99
    day2 = bars_from([(200, 201, 199, 200.5)] * 3, day=10)
    st2 = drive(b, day2)
    assert st2.session != st1.session
    assert st2.poc is not None and st2.poc > 190      # no bleed from day 1


def test_fixture_full_sessions_classify():
    bars = [b for b in read_recording(FIXTURE) if b.symbol == "QQQ"]
    builder = ProfileBuilder("QQQ")
    clock = MarketClock()
    by_session = {}
    for b in bars:
        st = builder.update(b, clock.session_id(b.ts_close))
        by_session[st.session] = st
    assert len(by_session) >= 3
    for sess, st in by_session.items():
        assert st.poc is not None and st.va_low < st.poc < st.va_high + 1e9
        assert st.ib_high is not None and st.ib_high > st.ib_low
        assert st.open_type != OpenType.UNKNOWN
        assert 0.0 <= st.trend_day_probability <= 1.0
