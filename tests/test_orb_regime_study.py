"""Replay OHLC assumptions and holdout comparisons without market connections."""
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from orb_regime_study import ET, FIVE, fill_path, metrics, paired_interval, portfolio, read_opend, sniper_paths  # noqa: E402

NOW = datetime(2026, 11, 19, 10, tzinfo=ET)
CALL = {"direction": "CALL", "symbol": "SPY", "trigger": 100, "stop": 99, "target": 102, "evidence": {"ema9": 100}}


def bar(n=0, op=100, hi=100.5, lo=99.5, cl=100.2):
    return {"started_at": NOW+n*FIVE, "open": op, "high": hi, "low": lo, "close": cl}


def test_entry_stop_and_target_same_bar_uses_adverse_stop():
    trade = fill_path(CALL, [bar(op=100, hi=104, lo=98)])
    assert trade["reason"] == "STOP"
    assert trade["net_bps"] == pytest.approx(-102)


def test_open_gap_uses_worse_price_and_cannot_chase_or_resurrect():
    assert fill_path(CALL, [bar(op=102, hi=103)]) is None
    assert fill_path(CALL, [bar(op=98, hi=103, lo=97)]) is None
    assert fill_path(CALL, [bar(hi=100)]) is None  # strict crossing
    trade = fill_path(CALL, [bar(op=100.8, hi=103, lo=100)])
    assert trade["net_bps"] == pytest.approx(10000*(102-100.8)/100.8-2)


def test_a_later_gap_through_stop_is_not_filled_at_stop():
    trade = fill_path(CALL, [bar(), bar(1, op=97, lo=95, hi=99)])
    assert trade["net_bps"] == pytest.approx(-302)


def test_put_and_ema_break_waits_for_next_executable_open():
    put = {**CALL, "direction": "PUT", "stop": 101, "target": 98}
    trade = fill_path(put, [bar(cl=100.4), bar(1, op=100.8)])
    assert trade["reason"] == "EMA9"
    assert trade["net_bps"] == pytest.approx(-82)


def test_interrupted_history_does_not_fake_close():
    with pytest.raises(ValueError, match="Missing exit coverage"):
        fill_path(CALL, [bar()])


def test_context_filtered_before_reserving_one_position():
    spy = dict(symbol="SPY", entry=NOW, exit=NOW+FIVE, combined="UNKNOWN")
    qqq = {**spy, "symbol": "QQQ", "combined": "PASS"}
    assert portfolio([qqq, spy], "baseline") == [spy]
    assert portfolio([qqq, spy], "combined") == [qqq]
    assert portfolio([spy, {**qqq, "entry": NOW+FIVE}], "baseline") == [spy]


def test_daily_metrics_and_clustered_bootstrap_include_no_trade_days():
    v = {"symbol": "SPY", "reason": "STOP", "net_bps": -8}
    period = [[v], [], [], [{**v, "net_bps": 2}]]
    assert metrics(period)["mean_net_bps_per_session"] == -1.5
    assert metrics(period)["max_drawdown_daily_additive_bps"] == 8
    assert paired_interval(period*3, period*3)["paired_95pct_block5_bps"] == [0, 0]


def test_right_labeled_history_normalizes_dst_and_refuses_start_labels(tmp_path):
    csv = tmp_path/"bars.csv"
    header = "time_key,open,high,low,close,volume\n"
    csv.write_text(header + "2026-03-06 09:35:00,98,102,97,100,100\n2026-03-09 09:35:00,99,101,98,99,120\n")
    rows = list(read_opend(csv).values())
    assert rows[0][0]["started_at"].hour == 9 and rows[0][0]["started_at"].minute == 30
    assert rows[0][0]["started_at"].utcoffset() == -timedelta(hours=5)
    assert rows[1][0]["started_at"].utcoffset() == -timedelta(hours=4)
    csv.write_text(header + "2026-03-09 09:30:00,99,101,98,99,120\n")
    with pytest.raises(ValueError, match="end-labeled"):
        read_opend(csv)


def test_repeating_portfolios_use_their_own_flat_since_timestamp_and_no_count_cap():
    # Structural test: injected approvals exercise the portfolio walker; the
    # production signal evaluator separately enforces a fresh post-exit reset.
    bars = [bar(i, hi=100 if i < 3 else 103, lo=99.6, cl=100 if i < 3 else 101) for i in range(25)]
    inputs = {s: ([], bars) for s in ("SPY", "QQQ")}
    status = {s: {k: ("FAIL" if s == "SPY" else "PASS") for k in ("rvol", "vix", "combined")} for s in inputs}
    observed = []

    def evaluator(symbol, history, quote, now, *, last_exit_at):
        assert history[-1]["started_at"] < now
        observed.append((symbol, now, last_exit_at))
        return {"status": "ARMED", "signal": {**CALL, "symbol": symbol}}

    result = sniper_paths(inputs, status, evaluator=evaluator)
    assert len(result["combined"]) > 2
    assert all(t["symbol"] == "QQQ" for t in result["combined"])
    assert all(t["symbol"] == "SPY" for t in result["baseline"])
    for variant in ("baseline", "combined"):
        trades = result[variant]
        for prior, actual in zip(trades, trades[1:]):
            assert actual["entry"] > prior["exit"]
            assert (actual["symbol"], actual["entry"], prior["exit"]) in observed
