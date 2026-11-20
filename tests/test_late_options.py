"""Prospective-only observation paths against an offline feed and real research journal."""

from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_trading.late_option_log import LateOptionLog  # noqa: E402
from paper_trading.late_options import VERSION, observe_step, shared_quote, trigger_state  # noqa: E402
from paper_trading.ledger import DEFAULT_POLICY, ET  # noqa: E402

DAY = "2026-11-20"
FIVE = timedelta(minutes=5)


def at(clock):
    return datetime.fromisoformat(f"{DAY}T{clock}").replace(tzinfo=ET)


def bar(start, o=99.6, h=100, low=99.2, c=99.6):
    return {"started_at": start.isoformat(), "open": o, "high": h, "low": low, "close": c, "volume": 1000}


class Feed:
    def __init__(self, clock):
        self.clock, self.calls = clock, []
        self.spot = {"SPY": 100.65, "QQQ": 100.65}
        self.patterns = {"SPY": {13}, "QQQ": set()}  # index of trigger candle
        self.quoting = True
        self.price = 2.0

    def bars(self, symbol, now):
        self.calls.append(("bars", symbol))
        start = at("09:30:00")
        old = start - timedelta(days=1) + timedelta(hours=4)
        rows = [bar(old + i * FIVE) for i in range(16)]
        for i in range(int((now - start).total_seconds() // 300)):
            t = start + i * FIVE
            if i + 2 in self.patterns[symbol]:
                rows.append(bar(t, 99.9, 100.5, 99.8, 100.4))
            elif i + 1 in self.patterns[symbol]:
                rows.append(bar(t, 100.4, 100.6, 99.96, 100.3))
            else:
                rows.append(bar(t))
        return rows

    def underlying(self, symbol):
        self.calls.append(("spot", symbol))
        return {
            "symbol": symbol,
            "price": self.spot[symbol],
            "observed_at": self.clock().isoformat(),
            "source": "MOOMOO_OPEND",
        }

    def book(self, sym="SPY", strike=100, delta=0.68, ask=None):
        ask = self.price if ask is None else ask
        stamp = self.clock().isoformat()
        return {
            "option_id": f"US.{sym}261120C{strike}000",
            "symbol": sym,
            "option_type": "call",
            "expiration_date": DAY,
            "strike": strike,
            "bid": round(ask - 0.04, 2),
            "ask": ask,
            "bid_size": 1,
            "ask_size": 3,
            "delta": delta,
            "volume": 500,
            "open_interest": 600,
            "source": "MOOMOO_OPEND",
            "contract_size": 100,
            "timestamp_basis": "PROVIDER_ORDER_BOOK_AND_SNAPSHOT",
            "bid_observed_at": stamp,
            "ask_observed_at": stamp,
            "observed_at": stamp,
            "metadata_observed_at": stamp,
            "provider_gamma": 0.02,
        }

    def candidates(self, symbol, direction, day, spot, *, max_premium):
        assert day == DAY and 0 < max_premium <= 500 and direction == "CALL"
        self.calls.append(("scan", symbol))
        return [self.book(symbol, 102, 0.55), self.book(symbol, 99, 0.78, 6), self.book(symbol)]

    def option_quote(self, contract):
        self.calls.append(("quote", contract["option_id"]))
        if not self.quoting:
            raise RuntimeError("SIMULATED_PRIVATE_PROVIDER_FAILURE")
        return self.book(contract["symbol"], contract["strike"], 0.68 if contract["strike"] == 100 else 0.55)


@pytest.fixture
def pilot(tmp_path):
    folder = tmp_path / ("research-late-options-" + DAY)
    folder.mkdir(mode=0o700)
    journal = LateOptionLog(folder / "late-options.sqlite", DAY)
    moment = [at("10:35:01")]

    def clock():
        return moment[0]

    feed = Feed(clock)

    def source():
        stamp = clock().isoformat()
        return {
            "mode": "SIMULATION",
            "validation": "RESEARCH_ONLY",
            "session_date": DAY,
            "policy": DEFAULT_POLICY,
            "agent": {"heartbeat_at": stamp, "status": "SESSION_COMPLETE"},
            "as_of": stamp,
            "now": stamp,
            "account": {
                "simulated": True,
                "currency": "USD",
                "entries_today": 0,
                "settled_cash": 1000.0,
                "cash": 1000.0,
                "today_realized_pnl": 0.0,
                "initial_capital": 1000.0,
            },
            "open_position": None,
            "trades": [],
        }

    def step(reader=source):
        return observe_step(DAY, feed=feed, source=reader, journal=journal, clock=clock)

    feed.baseline = source
    return feed, journal, moment, step


def test_enter_by_feasible_delta_then_real_post_request_exit_and_rearm(pilot):
    feed, j, moment, step = pilot
    assert step() == "OPEN"
    pos = j.state()["open_position"]
    assert pos["strategy"] == VERSION and pos["strike"] == 100 and pos["entry_debit_cents"] == 20165
    assert [e["kind"] for e in j.events()] == ["SIGNAL", "TRIGGER", "ENTRY"]
    sig = j.state()["signals"][0]
    assert sig["evidence"]["parent_signal_id"] != sig["signal_id"]
    assert pos["entry_option_quote"]["provider_gamma"] == 0.02
    moment[0] = at("10:36:00")
    feed.spot["SPY"] = 98.0
    feed.quoting = False

    def outage():
        raise ValueError("source unavailable")

    assert step(outage) == "EXIT_PENDING"
    assert j.state()["open_position"]["exit_request_evidence"]["underlying_quote"]["price"] == 98.0
    assert "SIMULATED_PRIVATE" not in str(j.events())
    feed.quoting = True
    feed.price = 1.6
    moment[0] = at("10:36:03")
    assert step(outage) == "CLOSED"
    closed = j.state()["trades"][0]
    assert closed["exit_option_quote"]["bid"] == 1.56 and closed["exit_credit_cents"] == 15435
    assert closed["realized_pnl_cents"] == -4730 and "stop" in closed["exit_reason"]
    feed.spot["SPY"] = 100.7
    moment[0] = at("10:36:04")
    assert step() == "OBSERVING" and len(j.state()["trades"]) == 1
    feed.patterns["QQQ"] = {17}  # 10:55 opportunity rebuilt after the actual 10:36 exit
    moment[0] = at("10:55:00")
    assert step() == "OPEN"
    assert j.state()["open_position"]["symbol"] == "QQQ"
    assert j.state()["open_position"]["entry_budget"]["available_settled_cents"] == 79835


@pytest.mark.parametrize("clock", ["10:29:59", "11:15:00", "11:31:00"])
def test_flat_observer_never_queries_outside_incremental_window(pilot, clock):
    feed, j, moment, step = pilot
    moment[0] = at(clock)
    assert step() == "WAITING" and not feed.calls and not j.events()


@pytest.mark.parametrize("which", ["candidates", "option_quote"])
def test_failed_scan_records_trigger_and_consumes_stop_across_restart(pilot, which):
    feed, j, moment, step = pilot

    def broken(*args, **kwargs):
        feed.spot["SPY"] = 98.0
        raise RuntimeError("SIMULATED_PRIVATE_PROVIDER_FAILURE")

    setattr(feed, which, broken)
    assert step() == "PARTIAL" and not j.state()["trades"]
    kinds = [e["kind"] for e in j.events()]
    assert "TRIGGER" in kinds and "SKIPPED" in kinds and "SIGNAL_INVALIDATED" in kinds
    assert "SIMULATED_PRIVATE" not in str(j.events())
    feed.spot["SPY"] = 100.7
    assert step() == "OBSERVING"
    assert len(LateOptionLog(j.path, DAY).state()["invalidated_signal_ids"]) == 1


def test_only_one_technical_feed_available_is_partial_not_no_coverage(pilot):
    feed, j, _, step = pilot
    original = feed.bars

    def one(symbol, now):
        if symbol == "SPY":
            raise RuntimeError("mock feed outage")
        return original(symbol, now)

    feed.bars = one
    assert step() == "PARTIAL"
    assert {(e["kind"], e["symbol"]) for e in j.events()} == {("DATA_BLOCKED", "SPY"), ("SIGNAL_CHECK", "QQQ")}


@pytest.mark.parametrize(
    "eligible,requested,word,status",
    [
        (0, 0, "remaining budget", "OBSERVING"),
        (5, 0, "capacity", "PARTIAL"),
        (5, 5, "two-sided book", "PARTIAL"),
    ],
)
def test_no_book_access_distinct_from_no_affordable_candidate(pilot, eligible, requested, word, status):
    feed, j, _, step = pilot
    feed.candidates = lambda *a, **kw: []
    feed.last_scan = {
        "symbol": "SPY",
        "direction": "CALL",
        "metadata_eligible_contracts": eligible,
        "book_requested_contracts": requested,
        "quoted_contracts": 0,
    }
    assert step() == status
    assert any(e["kind"] == "SKIPPED" and word in e["reason"] for e in j.events())


def test_armed_not_crossed_is_not_a_trigger_or_fill(pilot):
    feed, j, _, step = pilot
    feed.spot["SPY"] = 100.6
    step()
    assert "SIGNAL" in [e["kind"] for e in j.events()]
    assert "TRIGGER" not in [e["kind"] for e in j.events()] and not j.state()["trades"]
    assert not any(op == "scan" for op, _ in feed.calls)


@pytest.mark.parametrize("mode", ["changed_contract", "stale", "crossed_cutoff", "failed_source"])
def test_scan_latency_recheck_never_backfills_changed_or_ineligible_price(pilot, mode):
    feed, j, moment, step = pilot
    # Only one initially eligible option.
    feed.candidates = lambda *a, **k: [feed.book()]
    orig = feed.option_quote

    def reprice(contract):
        q = orig(contract)
        if mode == "changed_contract":
            q["strike"] = 101
        if mode == "stale":
            moment[0] += timedelta(seconds=11)
        if mode == "crossed_cutoff":
            moment[0] = at("11:15:00")
        return q

    feed.option_quote = reprice
    if mode == "failed_source":
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) > 1:
                raise OSError("private diagnostic")
            return feed.baseline()

        step(flaky)
    else:
        step()
    assert not j.state()["trades"] and "TRIGGER" in [e["kind"] for e in j.events()]
    assert {"SKIPPED", "REJECTED"} & {e["kind"] for e in j.events()}


def test_1130_requires_an_actual_book_and_deadline_prevents_backfill(pilot):
    feed, j, moment, step = pilot
    assert step() == "OPEN"
    moment[0] = at("11:30:00")
    feed.quoting = False
    assert step() == "EXIT_PENDING" and "11:30" in j.state()["open_position"]["exit_reason"]
    assert not any(t["status"] == "CLOSED" for t in j.state()["trades"])
    feed.quoting = True
    moment[0] = at("11:34:59")
    orig = feed.option_quote

    def slow(contract):
        moment[0] = at("11:35:00")
        return orig(contract)

    feed.option_quote = slow
    assert step() == "EXIT_PENDING" and j.state()["open_position"]["status"] == "EXIT_PENDING"
    assert step() == "EXIT_PENDING" and "EXIT" not in [e["kind"] for e in j.events()]


@pytest.mark.parametrize(
    "field,value",
    [
        ("bid_observed_at", "2026-11-20T10:35:03-05:00"),
        ("ask_observed_at", None),
        ("contract_size", 10),
        ("bid_size", 0),
        ("bid", 4),
        ("source", "estimated"),
        ("delta", -0.67),
        ("option_type", "put"),
    ],
)
def test_unverified_books_fail_closed(pilot, field, value):
    feed, _, moment, _ = pilot
    with pytest.raises((ValueError, TypeError)):
        shared_quote({**feed.book(), field: value}, "SPY", "CALL", DAY, moment[0], entry=True)


def test_put_trigger_is_strict_and_existing_stop_or_target_consumes(pilot):
    feed, _, moment, _ = pilot
    sig = {
        "symbol": "SPY",
        "direction": "PUT",
        "created_at": at("10:35:00"),
        "expires_at": at("10:40:00"),
        "stop": 101,
        "trigger": 100,
        "target": 98,
    }
    q = feed.underlying("SPY")
    for p, expected in [
        (100, "ARMED"),
        (99.9, "TRIGGERED"),
        (98, "TARGET_BEFORE_ENTRY"),
        (101, "STOP_BEFORE_ENTRY"),
    ]:
        assert trigger_state(sig, {**q, "price": p}, moment[0], DAY) == expected
