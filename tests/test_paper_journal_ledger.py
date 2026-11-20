"""Synthetic accounting tests; these fixtures are not market evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trading.ledger import Ledger, fill_cents  # noqa: E402


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 20, 10, 0, 5, tzinfo=ET)


def signal(identity="a", now=NOW):
    return {"signal_id": identity, "symbol": "SPY", "direction": "CALL",
            "trigger": 600, "stop": 599, "target": 602,
            "created_at": (now - timedelta(seconds=5)).isoformat(),
            "expires_at": (now + timedelta(minutes=4)).isoformat(),
            "reason": "Synthetic completed retest followed by trigger break.", "evidence": {"synthetic": True}}


def underlying(now=NOW, price=600.1):
    return {"symbol": "SPY", "price": price, "observed_at": now.isoformat(), "source": "SYNTHETIC_TEST_ONLY"}


def option(now=NOW, bid=1.96, ask=2.0, **overrides):
    return {"option_id": "SPY_SYNTHETIC_CALL", "symbol": "SPY", "option_type": "call",
            "expiration_date": now.date().isoformat(), "strike": 600, "bid": bid, "ask": ask,
            "delta": .6, "volume": 500, "open_interest": 1000,
            "observed_at": now.isoformat(), "source": "SYNTHETIC_TEST_ONLY", **overrides}


def close(ledger, trade, now=NOW + timedelta(minutes=1), bid=2.2, ask=2.22):
    ledger.request_exit(trade["trade_id"], "Synthetic target observation", now)
    return ledger.exit(trade["trade_id"], option(now, bid, ask), underlying(now), now)


def test_capital_persists_and_restart_never_resets_closed_losses(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = Ledger(path)
    trade = ledger.enter(signal(), option(), underlying(), NOW)
    closed = close(ledger, trade, bid=1.5, ask=1.52)
    assert closed["realized_pnl_cents"] == -5330
    snapshot = Ledger(path).snapshot(NOW + timedelta(minutes=2))
    assert snapshot["account"]["initial_capital"] == 1000
    assert snapshot["account"]["cash"] == 946.7
    with pytest.raises(ValueError, match="persisted capital/policy differs"):
        Ledger(path, initial_capital=2000)


def test_pre_entry_invalidation_is_durable_and_cannot_be_revived(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = Ledger(path)
    ledger.record_event("SIGNAL_INVALIDATED", "Stop crossed before entry", NOW,
                        details={"signal_id": "a"}, event_id="a:invalidated")
    with pytest.raises(ValueError, match="invalidated before entry"):
        Ledger(path).enter(signal(), option(), underlying(), NOW)
    assert Ledger(path).snapshot(NOW)["trades"] == []


def test_bid_ask_slippage_fees_and_unsettled_cash(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    trade = ledger.enter(signal(), option(), underlying(), NOW)
    assert trade["entry_price"] == 2.01
    assert trade["entry_debit_cents"] == 20165
    before = ledger.snapshot(NOW)
    assert before["account"]["cash"] == 798.35
    assert before["account"]["equity"] is None
    ledger.mark(option(), NOW)
    assert ledger.snapshot(NOW)["account"]["unrealized_pnl"] == -7.3
    closed = close(ledger, trade)
    assert closed["exit_price"] == 2.19
    assert closed["realized_pnl_cents"] == 1670
    after = ledger.snapshot(NOW + timedelta(minutes=1))
    assert after["account"]["cash"] == 1016.7
    assert after["account"]["settled_cash"] == 798.35
    assert after["account"]["unsettled_cash"] == 218.35
    assert closed["settles_on"] == "2026-11-23"
    monday = ledger.snapshot(datetime(2026, 11, 23, 9, 0, tzinfo=ET))
    assert monday["account"]["settled_cash"] == 1016.7


def test_stale_marks_are_missing_not_flat_or_green(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    ledger.enter(signal(), option(), underlying(), NOW)
    ledger.mark(option(), NOW)
    current = ledger.snapshot(NOW)
    assert current["account"]["equity"] == 992.7
    stale = ledger.snapshot(NOW + timedelta(seconds=11))
    assert stale["account"]["equity"] is None
    assert stale["account"]["unrealized_pnl"] is None
    assert stale["account"]["today_pnl"] is None
    assert stale["account"]["valuation_status"] == "UNRESOLVED_QUOTE"


def test_concurrent_entry_and_exit_are_idempotent(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = Ledger(path)
    with ThreadPoolExecutor(max_workers=3) as executor:
        trades = list(executor.map(lambda _: ledger.enter(signal(), option(), underlying(), NOW), range(3)))
    assert len({trade["trade_id"] for trade in trades}) == 1
    trade = trades[0]
    later = NOW + timedelta(minutes=1)
    ledger.request_exit(trade["trade_id"], "Test exit", later)
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: ledger.exit(trade["trade_id"], option(later), underlying(later), later), range(3)))
    snapshot = ledger.snapshot(later)
    assert len(snapshot["trades"]) == 1
    assert [event["kind"] for event in snapshot["events"]].count("ENTRY") == 1
    assert [event["kind"] for event in snapshot["events"]].count("EXIT") == 1


@pytest.mark.parametrize("change", [
    {"bid": 0}, {"ask": float("nan")}, {"delta": .9}, {"delta": -.6},
    {"delta": True}, {"volume": 99}, {"open_interest": 99}, {"bid": 1, "ask": 2},
    {"symbol": "QQQ"}, {"expiration_date": "2026-11-23"}, {"source": ""},
    {"observed_at": "2026-11-20T10:00:05"},
    {"observed_at": (NOW - timedelta(seconds=11)).isoformat()},
    {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
])
def test_invalid_quotes_fail_closed(tmp_path, change):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    with pytest.raises(ValueError):
        ledger.enter(signal(), option(**change), underlying(), NOW)
    assert ledger.snapshot(NOW)["trades"] == []


def test_full_premium_plus_both_fees_must_fit_daily_risk(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    with pytest.raises(ValueError, match="remaining daily loss"):
        ledger.enter(signal(), option(bid=4.95, ask=4.98), underlying(), NOW)
    trade = ledger.enter(signal(), option(bid=4.94, ask=4.97), underlying(), NOW)
    assert trade["entry_debit_cents"] == 49865


def test_losses_reduce_remaining_risk_and_no_affordable_trade_is_valid(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    trade = ledger.enter(signal(), option(bid=3.95, ask=4), underlying(), NOW)
    close(ledger, trade, bid=1, ask=1.01)
    next_time = NOW + timedelta(minutes=2)
    with pytest.raises(ValueError, match="remaining daily loss"):
        ledger.enter(signal("b", next_time), option(next_time), underlying(next_time), next_time)


def test_same_day_proceeds_cannot_be_reused(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3", initial_capital=300)
    first = ledger.enter(signal(), option(), underlying(), NOW)
    close(ledger, first, bid=3, ask=3.01)
    next_time = NOW + timedelta(minutes=2)
    with pytest.raises(ValueError, match="settled paper cash"):
        ledger.enter(signal("b", next_time), option(next_time), underlying(next_time), next_time)


def test_one_position_and_two_entries_per_day(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    first = ledger.enter(signal(), option(), underlying(), NOW)
    with pytest.raises(ValueError, match="already exists"):
        ledger.enter(signal("b"), option(), underlying(), NOW)
    close(ledger, first)
    next_time = NOW + timedelta(minutes=2)
    second = ledger.enter(signal("b", next_time), option(next_time), underlying(next_time), next_time)
    close(ledger, second, now=next_time + timedelta(minutes=1))
    last = next_time + timedelta(minutes=2)
    with pytest.raises(ValueError, match="daily entry limit"):
        ledger.enter(signal("c", last), option(last), underlying(last), last)


def test_exit_request_survives_failure_and_restart_and_never_uses_next_day_quote(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = Ledger(path)
    first = ledger.enter(signal(), option(), underlying(), NOW)
    later = NOW + timedelta(minutes=2)
    ledger.request_exit(first["trade_id"], "Missing feed; exit required", later)
    with pytest.raises(ValueError, match="stale"):
        ledger.exit(first["trade_id"], option(), None, later)
    ledger = Ledger(path)
    assert ledger.snapshot(later)["open_position"]["status"] == "EXIT_PENDING"
    assert ledger.snapshot(later)["account"]["today_pnl"] is None
    monday = datetime(2026, 11, 23, 10, 0, tzinfo=ET)
    with pytest.raises(ValueError, match="expired or retroactive"):
        ledger.exit(first["trade_id"], option(monday), None, monday)
    assert ledger.snapshot(monday)["open_position"] is not None


def test_exit_quote_cannot_predate_decision_even_when_fresh(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    first = ledger.enter(signal(), option(), underlying(), NOW)
    later = NOW + timedelta(seconds=5)
    ledger.request_exit(first["trade_id"], "Test stop", later)
    with pytest.raises(ValueError, match="predates"):
        ledger.exit(first["trade_id"], option(), None, later)


def test_fresh_option_exit_can_proceed_without_underlying_quote(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    first = ledger.enter(signal(), option(), underlying(), NOW)
    later = NOW + timedelta(minutes=1)
    ledger.request_exit(first["trade_id"], "Underlying feed failed", later)
    closed = ledger.exit(first["trade_id"], option(later), None, later)
    assert closed["status"] == "CLOSED"
    assert closed["exit_underlying_quote"] is None


def test_audit_events_cannot_be_modified_or_deleted(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = Ledger(path)
    ledger.record_event("PREFLIGHT", "Synthetic audit event", NOW)
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM events")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE events SET reason='Changed'")


def test_fill_rounding_is_conservative_at_subpenny_boundaries():
    assert fill_cents("2.005", ".01", entry=True) == 202
    assert fill_cents("1.995", ".01", entry=False) == 198
    assert fill_cents(0, ".01", entry=False) == 0


def test_private_file_and_full_event_export(tmp_path):
    path = tmp_path / "paper.sqlite3"
    ledger = Ledger(path)
    assert path.stat().st_mode & 0o777 == 0o600
    for i in range(205):
        ledger.record_event("TEST", f"Synthetic event {i}", NOW)
    assert len(ledger.snapshot(NOW)["events"]) == 200
    assert len(ledger.snapshot(NOW, full_history=True)["events"]) == 205
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        Ledger(path)


def test_zero_bid_exit_records_loss_and_fee_not_false_missing_data(tmp_path):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    first = ledger.enter(signal(), option(), underlying(), NOW)
    later = NOW + timedelta(minutes=1)
    ledger.request_exit(first["trade_id"], "Synthetic zero bid exit", later)
    closed = ledger.exit(first["trade_id"], option(later, 0, 0), None, later)
    assert closed["realized_pnl_cents"] == -20230
    snapshot = ledger.snapshot(later)
    assert snapshot["account"]["unsettled_cash"] == 0
    assert snapshot["account"]["cash"] == snapshot["account"]["settled_cash"]


@pytest.mark.parametrize("now", [
    datetime(2026, 11, 20, 9, 44, tzinfo=ET), datetime(2026, 11, 20, 10, 30, tzinfo=ET),
    datetime(2026, 11, 26, 10, 0, tzinfo=ET),
])
def test_time_and_exchange_session_gates(tmp_path, now):
    ledger = Ledger(tmp_path / "paper.sqlite3")
    with pytest.raises(ValueError):
        ledger.enter(signal(now=now), option(now), underlying(now), now)
