"""Offline checks for the matched-receipt forward comparison; no order paths."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paper_regime_shadow as shadow

DAY = "2026-11-20"


def moment(clock):
    return datetime.fromisoformat(f"{DAY}T{clock}-05:00")


def prior_csvs(folder):
    import exchange_calendars as xcals

    prior = [x.date().isoformat() for x in xcals.get_calendar("XNYS").schedule.loc[:DAY].index][-21:-1]
    for symbol in shadow.SYMBOLS:
        rows = ["time_key,volume"]
        for day in [*prior, DAY, "2026-11-23"]:
            rows.extend([f"{day} 09:35:00,100", f"{day} 09:40:00,100",
                         f"{day} 09:45:00,100", f"{day} 09:50:00,99999"])
        (folder / f"{symbol}_2023-10-19_2026-11-19.csv").write_text("\n".join(rows))
    (folder / "VIX_History.csv").write_text("DATE,CLOSE\n11/18/2026,14\n11/19/2026,22\n11/20/2026,99\n")


def prior(folder):
    prior_csvs(folder)
    return shadow.prepare_prior(DAY, moment("08:00"), folder)


class Bars:
    def __init__(self):
        self.calls = []

    def connect(self):
        pass

    def bars(self, symbol, now):
        self.calls.append(symbol)
        return [{"started_at": moment(t).isoformat(), "volume": 150} for t in ["09:30", "09:35", "09:40"]]


def event(kind, at, *, sym="SPY", ident=None, pnl=None):
    details = {"symbol": sym}
    if ident:
        details.update(simulated=True, trade_id=ident, session_date=DAY,
                       strategy="ORB_RETEST_PAPER_V1", quantity=1, entered_at=moment("09:57").isoformat())
    if pnl is not None:
        details.update(status="CLOSED", entry_debit_cents=20165, exit_credit_cents=20165 + pnl,
                       realized_pnl_cents=pnl)
    return {"event_id": (ident or sym) + kind, "kind": kind, "recorded_at": moment(at).isoformat(),
            "reason": kind, "details": details}


def snapshot(events, *, at="11:00", heartbeat="10:59:58"):
    return {"mode": "SIMULATION", "validation": "RESEARCH_ONLY", "session_date": DAY,
            "policy": {"live_eligible": False, "strategy":"ORB_RETEST_PAPER_V1"}, "agent": {"heartbeat_at": moment(heartbeat).isoformat()},
            "now": moment(at).isoformat(), "events": events}


def test_prior_is_exact_session_actual_receipt_and_no_today_or_future(tmp_path):
    data = prior(tmp_path)
    assert data["vix"]["close"] == "22"
    assert data["vix"]["available_at"] == moment("08:00").isoformat()
    rows = data["openings"]["SPY"]
    assert len(rows) == 60
    assert {datetime.fromisoformat(b["started_at"]).minute for b in rows} == {30, 35, 40}
    assert max(b["started_at"] for b in rows).startswith("2026-11-19")


def test_stale_or_duplicate_vix_does_not_fall_back(tmp_path):
    prior_csvs(tmp_path)
    for body in ["11/18/2026,25\n", "11/19/2026,20\n11/19/2026,21\n"]:
        (tmp_path / "VIX_History.csv").write_text("DATE,CLOSE\n" + body)
        assert shadow.prepare_prior(DAY, moment("08:00"), tmp_path)["vix"] is None


def test_context_frozen_once_from_only_completed_opening_with_no_gex(tmp_path):
    data = prior(tmp_path)
    bars = Bars()
    with closing(shadow.database(tmp_path)) as db:
        shadow.capture_contexts(db, DAY, data, bars, clock=lambda: moment("09:45:06"))
        data["vix"]["close"] = 90
        shadow.capture_contexts(db, DAY, data, bars, clock=lambda: moment("09:46"))
        ctx = shadow.report(db)["contexts"]["QQQ"]
        assert bars.calls == ["SPY", "QQQ"] and ctx["combined"] == "PASS"
        assert ctx["rvol"]["value"] == 1.5 and ctx["rvol"]["prior_sessions"] == 20
        assert ctx["vix"]["value"] == 22 and ctx["gex"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("at", ["09:44:59", "09:45:01", "09:55:00", "11:30:00"])
def test_never_freezes_from_after_outcome_or_before_last_close(tmp_path, at):
    with closing(shadow.database(tmp_path)) as db:
        bars = Bars()
        shadow.capture_contexts(db, DAY, {}, bars, clock=lambda: moment(at))
        assert not bars.calls and not shadow.report(db)["contexts"]


def test_crossing_capture_cutoff_cannot_be_backdated(tmp_path):
    data = prior(tmp_path)
    with closing(shadow.database(tmp_path)) as db:
        ticks = iter([moment("09:54:59"), moment("09:54:59"), moment("09:55:01")])
        shadow.capture_contexts(db, DAY, data, Bars(), clock=lambda: next(ticks))
        assert not shadow.report(db)["contexts"]


def test_matched_realized_net_only_from_source_fills_and_no_double_count(tmp_path):
    data = prior(tmp_path)
    with closing(shadow.database(tmp_path)) as db:
        shadow.capture_contexts(db, DAY, data, Bars(), clock=lambda: moment("09:45:06"))
        s = snapshot([event("EXIT", "10:30", ident="paper_one", pnl=-570),
                      event("ENTRY", "09:57", ident="paper_one"), event("SKIPPED", "09:58", sym="QQQ"),
                      event("EXIT", "10:31")])
        for _ in range(2):
            shadow.observe(db, s, DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")
        out = shadow.report(db)
        assert out["baseline_entries"] == out["matched_closed"] == 1
        assert out["matched_realized_cents"] == out["baseline_realized_cents"] == -570
        assert out["passed_but_no_fill_symbols"] == ["QQQ"]
        assert out["live_eligible"] is False
        assert all(arm["entries"] == 1 and arm["net_realized_cents"] == -570 for arm in out["arms"].values())


def test_missing_preentry_context_is_not_reconstructed_at_exit(tmp_path):
    with closing(shadow.database(tmp_path)) as db:
        db.execute("INSERT INTO contexts VALUES (?,?,?)", ("SPY", moment("10:00").isoformat(), '{"combined":"PASS"}'))
        shadow.observe(db, snapshot([event("ENTRY", "09:57", ident="t"), event("EXIT", "10:30", ident="t", pnl=100)]),
                       DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")
        out = shadow.report(db)
        assert out["unknown_entries"] == 1 and out["matched_entries"] == 0
        assert out["matched_realized_cents"] is None and out["baseline_realized_cents"] == 100


def test_stale_or_future_source_cannot_create_results(tmp_path):
    with closing(shadow.database(tmp_path)) as db:
        for heartbeat in ["10:59:29", "11:01:00"]:
            with pytest.raises(ValueError, match="stale"):
                shadow.observe(db, snapshot([event("ENTRY", "09:57", ident="t")], heartbeat=heartbeat),
                               DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")
        assert shadow.report(db)["baseline_entries"] == 0


def test_open_paper_trade_never_turns_into_realized_zero(tmp_path):
    with closing(shadow.database(tmp_path)) as db:
        shadow.observe(db, snapshot([event("ENTRY", "09:57", ident="t")]), DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")
        assert shadow.report(db)["baseline_realized_cents"] is None


def test_wrong_contract_or_pnl_is_rejected_atomically(tmp_path):
    with closing(shadow.database(tmp_path)) as db:
        bad = event("EXIT", "10:30", ident="t", pnl=125)
        bad["details"]["realized_pnl_cents"] = 1200
        with pytest.raises(ValueError, match="reconcile"):
            shadow.observe(db, snapshot([event("ENTRY", "09:57", ident="t"), bad]), DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")
        assert not shadow.report(db)["counts"]
        with pytest.raises(ValueError):
            shadow.observe(db, {**snapshot([]), "mode": "LIVE"}, DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")


def test_gamma_requires_private_file_and_never_influences_pass(tmp_path):
    path = tmp_path / "gamma-observations.json"
    path.write_text(json.dumps({"SPY": {"complete": False}}))
    path.chmod(0o644)
    assert shadow.optional_gamma(tmp_path)[0] == {}
    path.chmod(0o600)
    gamma, _ = shadow.optional_gamma(tmp_path)
    with closing(shadow.database(tmp_path)) as db:
        shadow.capture_contexts(db, DAY, prior(tmp_path), Bars(), gamma=gamma, clock=lambda: moment("09:45:09"))
        ctx = shadow.report(db)["contexts"]["SPY"]
        assert ctx["combined"] == "PASS" and ctx["gex"]["status"] == "UNKNOWN"


def test_invalid_private_path_not_followed(tmp_path):
    tmp_path.chmod(0o700)
    real = tmp_path / "other"
    real.write_text("outside")
    (tmp_path / "comparison.sqlite").symlink_to(real)
    with pytest.raises(ValueError):
        shadow.database(tmp_path)
    assert real.read_text() == "outside"


def test_broken_spy_feed_does_not_suppress_qqq(tmp_path):
    class Broken(Bars):
        def bars(self, symbol, now):
            if symbol == "SPY":
                raise RuntimeError("offline")
            return super().bars(symbol, now)

    with closing(shadow.database(tmp_path)) as db:
        errors = shadow.capture_contexts(db, DAY, prior(tmp_path), Broken(), clock=lambda: moment("09:45:07"))
        assert errors.keys() == {"SPY"}
        assert shadow.report(db)["contexts"].keys() == {"QQQ"}


def test_arms_for_single_factor_pass_and_matched_exit_request(tmp_path):
    data = prior(tmp_path)
    data["vix"]["close"] = "40"
    with closing(shadow.database(tmp_path)) as db:
        shadow.capture_contexts(db, DAY, data, Bars(), clock=lambda: moment("09:45:06"))
        req = event("EXIT_REQUESTED", "10:29")
        req["details"] = {"trade_id": "t"}
        state = snapshot([event("ENTRY", "09:57", ident="t"), req, event("EXIT", "10:30", ident="t", pnl=90)])
        state["trades"] = [{"symbol":"SPY", "trade_id":"t", "simulated":True, "session_date":DAY}]
        shadow.observe(db, state, DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")
        out = shadow.report(db)
        assert out["counts"]["EXIT_REQUESTED"] == 1
        assert out["arms"]["rvol_only"]["net_realized_cents"] == 90
        assert out["arms"]["vix_only"]["entries"] == out["arms"]["combined"]["entries"] == 0
        assert out["arms"]["combined"]["net_realized_cents"] is None


def test_worker_heartbeat_stale_not_live():
    now = moment("09:56:00")
    for at in (None, "bad", moment("09:55:28").isoformat(), moment("09:57:00").isoformat()):
        assert shadow.worker_status({"status": "OBSERVING", "at": at}, now)["worker_health"] == "STALE_OR_UNAVAILABLE"
    assert shadow.worker_status({"status": "FINISHED"}, now)["worker_health"] == "TERMINAL_HISTORY"
    assert shadow.worker_status({"status": "OBSERVING", "at": now.isoformat()}, now)["worker_health"] == "FRESH"


def test_version_pin_allows_more_than_two_v2_receipts_but_never_mixes_v1(tmp_path):
    events = [event("ENTRY", "09:57", ident=f"t{i}") for i in range(3)]
    state = snapshot(events)
    state["policy"]["strategy"] = "ORB_RETEST_SNIPER_V2"
    with closing(shadow.database(tmp_path)) as db:
        with pytest.raises(ValueError, match="source"):
            shadow.observe(db, state, DAY, moment("11:00"), baseline_strategy="ORB_RETEST_PAPER_V1")
        with pytest.raises(ValueError, match="receipt"):
            shadow.observe(db, state, DAY, moment("11:00"), baseline_strategy="ORB_RETEST_SNIPER_V2")
        for receipt in events:
            receipt["details"]["strategy"] = "ORB_RETEST_SNIPER_V2"
        shadow.observe(db, state, DAY, moment("11:00"), baseline_strategy="ORB_RETEST_SNIPER_V2")
        assert shadow.report(db)["baseline_entries"] == 3
