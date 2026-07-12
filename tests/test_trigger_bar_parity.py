"""Regression test for the trigger-bar identity bug (docs/18-enhancement-review.md §5.1).

Root cause: strategies whose trigger_tf != M5 (e.g. PULLBACK_CONTINUATION's H1)
must evaluate touch/entry geometry against the TRUE completed bar for that
timeframe (full-hour OHLC), not `state.last_bar` (the raw 5m bar that merely
triggered the rollup). Using the wrong bar silently narrows the price range
checked for zone touches to the final 5 minutes of the hour instead of the
whole hour — exactly the kind of bug that quietly breaks research/production
parity without ever raising an exception.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from intellidhan_engine.state import SymbolState
from intellidhan_schemas import Bar, Timeframe

ET = ZoneInfo("America/New_York")


def bar5(t, o, h, lo, c, v=1000.0, sym="T") -> Bar:
    return Bar(symbol=sym, timeframe=Timeframe.M5, ts_close=t, open=o, high=h, low=lo,
               close=c, volume=v, source="fx")


def test_trigger_bar_is_the_true_h1_bar_not_the_final_5m_bar():
    """The H1 low must reflect the whole hour, even when the dip happens
    early and the final 5m bar never revisits it — the exact scenario the
    pre-fix bug would have silently missed a valid pullback touch on."""
    st = SymbolState("T")
    t0 = datetime(2026, 7, 13, 9, 35, tzinfo=ET)
    # 12 five-minute bars spanning one H1 bucket (9:35 .. 10:30).
    # The deep low (98.0) happens in bar #2; the final bar's low is 102.0 —
    # a strategy reading only the final 5m bar would never see the 98.0 dip.
    seq = [
        (100.0, 100.5, 99.8, 100.2),
        (100.2, 100.4, 98.0, 98.5),   # true hour low
        (98.5, 101.0, 98.3, 100.8),
        (100.8, 101.5, 100.5, 101.2),
        (101.2, 101.8, 101.0, 101.5),
        (101.5, 102.0, 101.3, 101.8),
        (101.8, 102.2, 101.6, 102.0),
        (102.0, 102.4, 101.9, 102.2),
        (102.2, 102.6, 102.0, 102.4),
        (102.4, 102.8, 102.2, 102.6),
        (102.6, 103.0, 102.4, 102.8),
        (102.8, 103.2, 102.6, 103.0),  # final 5m bar of the hour — low=102.6, well above 98.0
        (103.0, 103.3, 102.9, 103.1),  # first bar of the NEXT hour — forces prior bucket to emit
    ]
    for i, (o, h, lo, c) in enumerate(seq):
        st.on_bar_5m(bar5(t0 + timedelta(minutes=5 * i), o, h, lo, c))

    h1_bar = st.trigger_bar(Timeframe.H1)
    assert h1_bar is not None, "H1 bucket should have rolled up by the 12th 5m bar"
    assert h1_bar.low == 98.0, "trigger_bar(H1) must carry the TRUE full-hour low"
    assert h1_bar.high == 103.2
    assert h1_bar.open == 100.0    # first sub-bar's open
    assert h1_bar.close == 103.0   # last sub-bar's close

    # The bug this test guards against: state.last_bar is the raw 5m bar that
    # triggered the rollup emission (the first bar of the NEXT hour here),
    # not the hour itself — its low is far above the true hour low and would
    # silently hide the pullback touch.
    assert st.last_bar.low == 102.9
    assert st.last_bar.low != h1_bar.low, (
        "last_bar and trigger_bar(H1) must diverge here — this IS the bug scenario"
    )


def test_trigger_bar_none_before_first_rollup():
    st = SymbolState("T")
    st.on_bar_5m(bar5(datetime(2026, 7, 13, 9, 35, tzinfo=ET), 100, 101, 99, 100.5))
    assert st.trigger_bar(Timeframe.H1) is None  # not yet warmed — must not fall back silently


def test_trigger_bar_daily_matches_session_ohlc():
    st = SymbolState("T")
    t0 = datetime(2026, 7, 13, 9, 35, tzinfo=ET)
    day1 = [(100 + i * 0.1, 101 + i * 0.1, 99.5 + i * 0.1, 100.5 + i * 0.1) for i in range(80)]
    for i, (o, h, lo, c) in enumerate(day1):
        st.on_bar_5m(bar5(t0 + timedelta(minutes=5 * i), o, h, lo, c))
    t1 = datetime(2026, 7, 14, 9, 35, tzinfo=ET)
    st.on_bar_5m(bar5(t1, 200, 201, 199, 200.5))  # first bar of next session -> flushes D1
    d1_bar = st.trigger_bar(Timeframe.D1)
    assert d1_bar is not None
    assert d1_bar.open == day1[0][0]
    assert d1_bar.high == max(h for _, h, _, _ in day1)
    assert d1_bar.low == min(lo for _, _, lo, _ in day1)
    assert d1_bar.close == day1[-1][3]
