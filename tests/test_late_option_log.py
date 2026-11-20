"""Real SQLite + synthetic, provider-shaped observations. No OpenD or primary DB."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trading.late_option_log import LateOptionLog, VERSION  # noqa: E402
from paper_trading.ledger import DEFAULT_POLICY, ET  # noqa: E402

DAY = "2026-11-20"
OPEN = datetime(2026, 11, 20, 10, 30, tzinfo=ET)
D = timedelta


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def signal(at=OPEN, symbol="SPY", **kwargs):
    p = sha(symbol + str(at))
    return {
        "signal_id": sha(VERSION + "|" + p),
        "reason": "Synthetic retest.",
        "symbol": symbol,
        "created_at": at.isoformat(),
        "expires_at": (at + D(minutes=5)).isoformat(),
        "direction": "CALL",
        "trigger": 600,
        "stop": 599,
        "target": 602,
        "evidence": {
            "strategy": VERSION,
            "technical_strategy": "ORB_RETEST_SNIPER_V2",
            "research_only": True,
            "live_eligible": False,
            "parent_signal_id": p,
            "reset_bar_at": (at - D(minutes=15)).isoformat(),
            "breakout_at": (at - D(minutes=10)).isoformat(),
            "retest_at": (at - D(minutes=5)).isoformat(),
        },
        **kwargs,
    }


def spot(at, symbol="SPY", price=601):
    return {
        "symbol": symbol,
        "observed_at": at.isoformat(),
        "source": "MOOMOO_OPEND",
        "price": price,
    }


def option(at, symbol="SPY", bid=1.95, ask=2.0, **extra):
    iso = at.isoformat()
    return {
        "option_id": "US." + symbol + "TEST",
        "symbol": symbol,
        "option_type": "call",
        "expiration_date": DAY,
        "strike": 600,
        "bid": bid,
        "ask": ask,
        "bid_size": 10,
        "ask_size": 8,
        "delta": 0.68,
        "volume": 500,
        "open_interest": 1000,
        "observed_at": iso,
        "ask_observed_at": iso,
        "bid_observed_at": iso,
        "metadata_observed_at": iso,
        "source": "MOOMOO_OPEND",
        "timestamp_basis": "PROVIDER_ORDER_BOOK_AND_SNAPSHOT",
        "contract_size": 100,
        "provider_gamma": None,
        "provider_implied_volatility": 21.3,
        **extra,
    }


def baseline(now, positions=()):
    realized = sum(t["realized_pnl_cents"] for t in positions)
    spent = sum(t["entry_debit_cents"] - min(0, t["exit_credit_cents"]) for t in positions)
    return {
        "mode": "SIMULATION",
        "validation": "RESEARCH_ONLY",
        "session_date": DAY,
        "as_of": now.isoformat(),
        "now": now.isoformat(),
        "agent": {"heartbeat_at": now.isoformat()},
        "policy": DEFAULT_POLICY,
        "open_position": None,
        "trades": list(positions),
        "account": {
            "currency": "USD",
            "simulated": True,
            "initial_capital": 1000,
            "entries_today": len(positions),
            "settled_cash": (100000 - spent) / 100,
            "cash": (100000 + realized) / 100,
            "today_realized_pnl": realized / 100,
        },
    }


def early(key, debit, credit):
    return {
        "trade_id": key,
        "status": "CLOSED",
        "session_date": DAY,
        "simulated": True,
        "quantity": 1,
        "strategy": DEFAULT_POLICY["strategy"],
        "symbol": "QQQ",
        "entry_debit_cents": debit,
        "exit_credit_cents": credit,
        "realized_pnl_cents": credit - debit,
        "entered_at": (OPEN - D(minutes=25)).isoformat(),
        "exited_at": (OPEN - D(minutes=20)).isoformat(),
    }


@pytest.fixture
def path(tmp_path):
    folder = tmp_path / ("research-late-options-" + DAY)
    folder.mkdir(mode=0o700)
    return folder / "late-options.sqlite"


def armed(log, now=OPEN, symbol="SPY"):
    s = signal(now, symbol)
    log.signal(s, now)
    log.trigger(s["signal_id"], spot(now, symbol), now)
    return s


def entry(log, at=OPEN, *, base=None, symbol="SPY", bid=1.95, ask=2.0):
    return log.enter(
        armed(log, at, symbol),
        option(at, symbol, bid, ask),
        spot(at, symbol),
        at,
        base or baseline(at),
    )


def close(log, trade, at, bid, ask):
    log.request_exit(
        trade["trade_id"], "Synthetic target or stop.", at, evidence={"spot": spot(at)}
    )
    return log.exit(trade["trade_id"], option(at, trade["symbol"], bid, ask), None, at)


def test_durable_three_stage_lifecycle_costs_restart_and_duplicate_safety(path):
    log = LateOptionLog(path, DAY)
    assert log.report()["win_rate_pct"] is None
    s = armed(log)
    primary = baseline(OPEN)
    unchanged = json.dumps(primary, sort_keys=True)
    a = log.enter(s, option(OPEN), spot(OPEN), OPEN, primary)
    assert (
        a["entry_debit_cents"] == 20165
        and a["entry_option_quote"]["provider_implied_volatility"] == 21.3
    )
    later = OPEN + D(minutes=1)
    assert log.enter(s, None, None, later, {}) == a  # replay does not book or charge twice
    assert log.report()["unresolved"] == 1 and log.report()["net_closed_pnl_usd"] is None
    with pytest.raises(ValueError, match="open or unresolved"):
        log.gate(baseline(later), later)
    rejected = LateOptionLog(path, DAY)
    assert rejected.state()["open_position"] == a
    with pytest.raises(ValueError, match="durably"):
        rejected.exit(a["trade_id"], option(later), None, later)
    b = close(rejected, a, later, 2.2, 2.24)
    assert (
        b["entry_option_quote"]["provider_gamma"] is None
        and b["exit_request_evidence"]["spot"]["price"] == 601
    )
    assert b["realized_pnl_cents"] == 1670
    assert rejected.exit(a["trade_id"], None, None, later) == b
    assert rejected.request_exit(a["trade_id"], "second reason", later) == b
    report = LateOptionLog(path, DAY, readonly=True).report()
    assert (
        report["unique_signals"]
        == report["unique_triggered"]
        == report["modeled_entries"]
        == report["closed"]
        == 1
    )
    assert report["closed_gross_bid_ask_pnl_usd"] == 20 and report["closed_commissions_usd"] == 1.3
    assert (
        report["closed_slippage_and_rounding_usd"] == 2
        and report["closed_spread_vs_mid_usd"] == 4.5
    )
    assert report["net_closed_pnl_usd"] == report["resolved_observations_pnl_usd"] == 16.7
    assert (
        report["win_rate_pct"] == 100
        and report["win_rate_denominator"] == 1
        and not report["unresolved"]
    )
    assert report["event_counts"] == {
        "SIGNAL": 1,
        "TRIGGER": 1,
        "ENTRY": 1,
        "EXIT_REQUESTED": 1,
        "EXIT": 1,
    }
    assert json.dumps(primary, sort_keys=True) == unchanged


def test_unfilled_triggers_armed_only_terminal_reasons_do_not_invent_trades(path):
    log = LateOptionLog(path, DAY)
    s1 = signal()
    log.signal(s1, OPEN)
    log.signal(s1, OPEN)
    with pytest.raises(ValueError, match="before any modeled"):
        log.enter(s1, option(OPEN), spot(OPEN), OPEN, baseline(OPEN))
    for n in (599.9, 600, 602, 603):
        with pytest.raises(ValueError, match="fresh underlying crossing"):
            log.trigger(s1["signal_id"], spot(OPEN, price=n), OPEN)
    log.trigger(s1["signal_id"], spot(OPEN), OPEN)
    log.trigger(s1["signal_id"], spot(OPEN + D(seconds=1)), OPEN + D(seconds=1))
    for _ in range(3):
        log.reject(s1["signal_id"], "No fresh quotes", OPEN)
        log.note("DATA_BLOCKED", "No fresh book", OPEN, symbol="SPY")
        log.note("SIGNAL_CHECK", "No new breakout", OPEN, symbol="QQQ", key="10:30")
    log.reject(s1["signal_id"], "Expired without option", OPEN + D(minutes=5), final=True)
    s2 = signal(OPEN + D(minutes=5), "QQQ")
    log.signal(s2, OPEN + D(minutes=5))
    log.invalidate(s2["signal_id"], "Stop crossed before entry", OPEN + D(minutes=5))
    with pytest.raises(ValueError, match="consumed"):
        log.trigger(s2["signal_id"], spot(OPEN + D(minutes=5), "QQQ"), OPEN + D(minutes=5))
    r = log.report()
    assert (
        r["unique_signals"],
        r["unique_triggered"],
        r["triggered_without_entry"],
        r["armed_without_observed_trigger"],
    ) == (2, 1, 1, 1)
    assert (
        r["event_counts"]["SKIPPED"]
        == r["event_counts"]["DATA_BLOCKED"]
        == r["event_counts"]["SIGNAL_CHECK"]
        == 1
    )
    assert r["event_counts"]["REJECTED"] == r["event_counts"]["SIGNAL_INVALIDATED"] == 1
    assert (
        r["net_closed_pnl_usd"] is r["resolved_observations_pnl_usd"] is r["win_rate_pct"] is None
    )


def test_baseline_and_observer_reinvest_neither_gains_nor_unsettled_proceeds(path):
    log = LateOptionLog(path, DAY)
    # After a $200 baseline loss, only $300 remains at risk, even after a separate baseline profit.
    ts = [early("a", 27065, 7065), early("b", 20065, 21065)]
    gate = log.gate(baseline(OPEN, ts), OPEN)
    assert (gate["remaining_risk_cents"], gate["available_settled_cents"], gate["max_premium"]) == (
        30000,
        52870,
        298.70,
    )
    with pytest.raises(ValueError, match="ceiling"):
        entry(log, base=baseline(OPEN, ts), ask=3, bid=2.98)
    t = entry(log, base=baseline(OPEN, ts), ask=2.5, bid=2.45)
    when = OPEN + D(minutes=2)
    close(log, t, when, 3.5, 3.52)
    assert log.gate(baseline(when, ts), when)["remaining_risk_cents"] == 30000
    assert log.gate(baseline(when, ts), when)["available_settled_cents"] == 52870 - 25165
    assert log.gate(baseline(when, ts), when)["max_premium"] == 275.75
    s = armed(log, OPEN + D(minutes=5), "QQQ")
    with pytest.raises(ValueError, match="post-exit reset"):
        log.enter(
            s,
            option(OPEN + D(minutes=5), "QQQ"),
            spot(OPEN + D(minutes=5), "QQQ"),
            OPEN + D(minutes=5),
            baseline(OPEN + D(minutes=5), ts),
        )
    t2 = entry(
        log,
        OPEN + D(minutes=20),
        base=baseline(OPEN + D(minutes=20), ts),
        symbol="QQQ",
        ask=2,
        bid=1.95,
    )
    close(log, t2, OPEN + D(minutes=21), 0.5, 0.55)
    gate = log.gate(baseline(OPEN + D(minutes=22), ts), OPEN + D(minutes=22))
    assert gate["remaining_risk_cents"] == 14670 and gate["max_premium"] == 74.1


@pytest.mark.parametrize(
    "change",
    [
        {"policy": {**DEFAULT_POLICY, "max_premium": 600}},
        {"mode": "LIVE"},
        {"session_date": "2026-11-19"},
        {"open_position": {"status": "OPEN"}},
        {"agent": {"heartbeat_at": (OPEN - D(seconds=31)).isoformat()}},
        {"as_of": (OPEN + D(seconds=1)).isoformat()},
        {"account": {"initial_capital": 1000}},
        {"trades": [early("r", 10065, 20000)]},
    ],
)
def test_unverifiable_primary_snapshot_never_grants_risk(path, change):
    log = LateOptionLog(path, DAY)
    with pytest.raises(ValueError):
        entry(log, base={**baseline(OPEN), **change})
    assert log.report()["modeled_entries"] == 0


@pytest.mark.parametrize("field", ["cash", "today_realized_pnl"])
def test_settled_cash_alone_cannot_authenticate_positive_receipt_proceeds(path, field):
    log = LateOptionLog(path, DAY)
    b = baseline(OPEN, [early("prior", 20065, 18065)])
    # Trade count, debits, settled cash, and internal receipt P/L still agree.
    b["account"][field] += 80
    with pytest.raises(ValueError, match="cash or daily realized"):
        log.gate(b, OPEN)


@pytest.mark.parametrize(
    "over",
    [
        {"bid": 0},
        {"contract_size": 10},
        {"ask_observed_at": (OPEN + D(seconds=1)).isoformat()},
        {"bid_observed_at": (OPEN - D(seconds=1)).isoformat()},
        {"source": "LAST_TRADE"},
        {"delta": 0.2},
        {"ask_size": 0},
        {"expiration_date": "2026-11-21"},
        {"bid": 1},
        {"metadata_observed_at": None},
    ],
)
def test_nonstandard_stale_or_unaffordable_entry_rejected(path, over):
    log = LateOptionLog(path, DAY)
    with pytest.raises(ValueError):
        log.enter(armed(log), {**option(OPEN), **over}, spot(OPEN), OPEN, baseline(OPEN))
    assert log.report()["triggered_without_entry"] == 1


def test_exit_never_backfills_wrong_expired_missing_or_after_deadline_quotes(path):
    log = LateOptionLog(path, DAY)
    tr = entry(log)
    request = OPEN + D(minutes=60)
    log.request_exit(tr["trade_id"], "11:30 flatten", request)
    for quote, now in (
        (option(request - D(seconds=1)), request),
        (option(request, "QQQ"), request),
        (option(request, bid=0), request),
        (option(request + D(minutes=5)), request + D(minutes=5)),
        (option(request + D(days=1)), request + D(days=1)),
    ):
        with pytest.raises(ValueError):
            log.exit(tr["trade_id"], quote, None, now)
    r = LateOptionLog(path, DAY, readonly=True).report()
    assert r["open_position"]["status"] == "EXIT_PENDING" and r["unresolved"] == 1
    assert r["resolved_observations_pnl_usd"] is None and r["closed"] == 0


def test_recent_cached_book_before_first_cross_is_not_an_observable_post_cross_entry(path):
    log = LateOptionLog(path, DAY)
    s = signal()
    log.signal(s, OPEN)
    crossed = OPEN + D(seconds=3)
    checked = OPEN + D(seconds=5)
    before = OPEN + D(seconds=1)
    log.trigger(s["signal_id"], spot(crossed), crossed)
    with pytest.raises(ValueError, match="pre-cross books"):
        log.enter(s, option(before), spot(checked), checked, baseline(checked))
    with pytest.raises(ValueError, match="freshly triggered"):
        log.enter(s, option(checked), spot(before), checked, baseline(checked))
    # Quote metadata may be just older than the cross; the actual market on
    # both sides must arrive after the crossing and all fields must be fresh.
    q = option(checked, metadata_observed_at=before.isoformat(), observed_at=before.isoformat())
    assert log.enter(s, q, spot(checked), checked, baseline(checked))["status"] == "OPEN"


def test_namespace_window_file_boundaries_and_readonly_mode(path):
    with pytest.raises(FileNotFoundError):
        LateOptionLog(path, DAY, readonly=True)
    assert not path.exists()
    with pytest.raises(ValueError):
        LateOptionLog(path.with_name("journal.sqlite"), DAY)
    log = LateOptionLog(path, DAY)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        log.signal({**signal(), "signal_id": "primary-signal"}, OPEN)
    with pytest.raises(ValueError):
        log.signal(signal(OPEN + D(minutes=45)), OPEN + D(minutes=45))
    with pytest.raises(ValueError):
        log.signal(signal(), OPEN + D(minutes=5))
    with pytest.raises(ValueError):
        LateOptionLog(path, DAY, readonly=True).note("SKIPPED", "read only", OPEN)
    with sqlite3.connect(path) as db:
        log.note("SKIPPED", "original", OPEN)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM late_events")
    os.link(path, path.with_name("other.db"))
    with pytest.raises(ValueError, match="links"):
        LateOptionLog(path, DAY, readonly=True)


def test_only_one_atomic_open_under_overlapping_workers(path):
    a = LateOptionLog(path, DAY)
    b = LateOptionLog(path, DAY)
    ids = [armed(a, OPEN, sym) for sym in ("SPY", "QQQ")]

    def attempt(item):
        journal, s = item
        try:
            journal.enter(
                s, option(OPEN, s["symbol"]), spot(OPEN, s["symbol"]), OPEN, baseline(OPEN)
            )
        except ValueError as exc:
            assert "open or unresolved" in str(exc)
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [(a, ids[0]), (b, ids[1])])) == [False, True]
    assert a.report()["modeled_entries"] == 1
