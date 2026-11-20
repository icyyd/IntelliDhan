"""Frozen ORB-retest rules against synthetic candles, not performance evidence."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trading.strategy import evaluate, exit_reason  # noqa: E402


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 20, 9, 55, 5, tzinfo=ET)


def bar(at, open_, high, low, close, volume=1000):
    return {"started_at": at.isoformat(), "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def candles():
    previous = datetime(2026, 11, 19, 14, 0, tzinfo=ET)
    rows = [bar(previous + timedelta(minutes=5 * i), 99, 99.3, 98.9, 99.1) for i in range(16)]
    start = datetime(2026, 11, 20, 9, 30, tzinfo=ET)
    rows.extend([
        bar(start, 99.2, 100, 99, 99.5),
        bar(start + timedelta(minutes=5), 99.5, 99.9, 99.1, 99.7),
        bar(start + timedelta(minutes=10), 99.7, 99.9, 99.3, 99.8),
        bar(start + timedelta(minutes=15), 99.8, 100.4, 99.7, 100.3),
        bar(start + timedelta(minutes=20), 100.3, 100.5, 99.99, 100.2),
    ])
    return rows


def quote(price=100.51, now=NOW):
    return {"symbol": "SPY", "price": price, "observed_at": now.isoformat(), "source": "SYNTHETIC_TEST_ONLY"}


def position(now=NOW):
    signal = evaluate("SPY", candles(), quote(), now)["signal"]
    return {**signal, "entered_at": now.isoformat(), "session_date": "2026-11-20", "exit_requested_at": None}


def test_confirmed_retest_next_candle_trigger_is_deterministic():
    result = evaluate("SPY", candles(), quote(), NOW)
    assert result["status"] == "ENTRY"
    signal = result["signal"]
    assert signal["direction"] == "CALL"
    assert signal["trigger"] == 100.5
    assert signal["stop"] < 99.99
    assert signal["target"] == signal["trigger"] + 2 * (signal["trigger"] - signal["stop"])
    assert signal["signal_id"] == evaluate("SPY", candles(), quote(), NOW)["signal"]["signal_id"]
    assert signal["expires_at"] == datetime(2026, 11, 20, 15, 0, tzinfo=ZoneInfo("UTC")).isoformat()


def test_unbroken_trigger_is_armed_not_entered():
    result = evaluate("SPY", candles(), quote(100.5), NOW)
    assert result["status"] == "ARMED"


def test_incomplete_retest_cannot_authorize_entry():
    early = NOW - timedelta(minutes=1)
    result = evaluate("SPY", candles(), quote(now=early), early)
    assert result["signal"] is None
    assert "Waiting for a retest" in result["reason"]


def test_missing_or_duplicate_today_candle_fails_closed():
    rows = candles()
    del rows[-4]
    assert evaluate("SPY", rows, quote(), NOW)["status"] == "BLOCKED"
    rows = candles()
    rows.append(rows[-1])
    assert evaluate("SPY", rows, quote(), NOW)["status"] == "BLOCKED"


def test_atr_warmup_and_volume_are_required():
    assert evaluate("SPY", candles()[-5:], quote(), NOW)["status"] == "BLOCKED"
    rows = candles()
    for row in rows:
        row["volume"] = 0
    result = evaluate("SPY", rows, quote(), NOW)
    assert result["signal"] is None


def test_close_inside_range_cancels_setup():
    rows = candles()
    rows[-1]["close"] = 99.99
    result = evaluate("SPY", rows, quote(), NOW)
    assert result["signal"] is None
    assert "canceled" in result["reason"]


def test_range_completion_and_preentry_cancellation_identity():
    active = evaluate("SPY", candles(), quote(), NOW)
    assert datetime.fromisoformat(active["evidence"]["range_completed_at"]).astimezone(ET).strftime("%H:%M") == "09:45"
    canceled = evaluate("SPY", candles(), quote(99), NOW)
    assert canceled["invalidated_signal_id"] == active["signal"]["signal_id"]


def test_put_is_exact_mirror_not_call_bias():
    rows = candles()
    for row in rows:
        row.update(open=200 - row["open"], high=200 - row["low"], low=200 - row["high"], close=200 - row["close"])
    result = evaluate("SPY", rows, quote(99.49), NOW)
    assert result["status"] == "ENTRY"
    assert result["signal"]["direction"] == "PUT"
    assert result["signal"]["stop"] > result["signal"]["trigger"] > result["signal"]["target"]


def test_late_entry_no_chase_and_stale_quote_gates():
    later = NOW + timedelta(minutes=5)
    rows = candles() + [bar(datetime(2026, 11, 20, 9, 55, tzinfo=ET), 100.2, 100.7, 100.1, 100.6)]
    result = evaluate("SPY", rows, quote(now=later), later)
    assert result["signal"] is None
    assert "expired" in result["reason"]
    assert evaluate("SPY", candles(), quote(110), NOW)["signal"] is None
    assert evaluate("SPY", candles(), quote(now=NOW - timedelta(seconds=11)), NOW)["status"] == "BLOCKED"
    cutoff = datetime(2026, 11, 20, 10, 30, tzinfo=ET)
    assert evaluate("SPY", [], quote(now=cutoff), cutoff)["signal"] is None


def test_retest_window_expires_after_three_bars():
    rows = candles()
    rows[-1].update(low=100.1, close=100.2)
    rows += [
        bar(datetime(2026, 11, 20, 9, 55, tzinfo=ET), 100.2, 100.6, 100.1, 100.4),
        bar(datetime(2026, 11, 20, 10, 0, tzinfo=ET), 100.4, 100.6, 100.2, 100.3),
    ]
    later = NOW + timedelta(minutes=10)
    result = evaluate("SPY", rows, quote(now=later), later)
    assert result["signal"] is None
    assert "expired after three" in result["reason"]


def test_stop_target_time_and_data_health_exit_reasons():
    trade = position()
    assert "stop" in exit_reason(trade, candles(), quote(99), NOW)
    assert "2R" in exit_reason(trade, candles(), quote(103), NOW)
    assert "Data-health" in exit_reason(trade, candles(), None, NOW)
    assert "60-minute" in exit_reason(trade, [], None, NOW + timedelta(minutes=60))
    cutoff = datetime(2026, 11, 20, 11, 30, tzinfo=ET)
    assert "flatten" in exit_reason(trade, [], None, cutoff)
    assert exit_reason(trade, candles(), quote(), NOW) is None


def test_persisted_exit_reason_cannot_disappear_on_recovery():
    trade = position()
    trade.update(exit_requested_at=NOW.isoformat(), exit_reason="Frozen stop decision")
    assert exit_reason(trade, [], None, NOW) == "Frozen stop decision"


def test_non_session_and_naive_candles_do_not_create_signal():
    holiday = datetime(2026, 11, 26, 10, 0, tzinfo=ET)
    assert evaluate("SPY", candles(), quote(now=holiday), holiday)["status"] == "BLOCKED"
    rows = candles()
    rows[-1]["started_at"] = "2026-11-20T09:50:00"
    assert evaluate("SPY", rows, quote(), NOW)["status"] == "BLOCKED"
