"""All clocks, market data and process identities are test fixtures. Never starts a worker."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import paper_late_options as run  # noqa: E402

DAY = run.PILOT_DAY


def when(t):
    return datetime.fromisoformat(DAY+"T"+t).replace(tzinfo=run.ET)


class Clock:
    def __init__(self,t):
        self.now = when(t)

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class Journal:
    def __init__(self, pos=None):
        self.pos = pos
        self.notes = []

    def state(self):
        return {"open_position":self.pos}

    def note(self,*args):
        self.notes.append(args)


def primary():
    at = when("10:31")
    return {"mode":"SIMULATION","session_date":DAY,"validation":"RESEARCH_ONLY","as_of":at,"now":at,
            "agent":{"heartbeat_at":at,"status":"SESSION_COMPLETE"},
            "account":{"initial_capital":1000,"cash":1000,"settled_cash":1000,"today_realized_pnl":0,
                       "currency":"USD","simulated":True},
            "events":[],"trades":[],"open_position":None,"policy":dict(run.DEFAULT_POLICY)}


@pytest.mark.parametrize("mutate", [
    lambda p:p.update(session_date="2026-11-19"),
    lambda p:p.update(mode="LIVE"), lambda p:p["policy"].update(max_daily_loss=1000),
    lambda p:p.update(validation=None), lambda p:p["policy"].update(strategy="ORB_RETEST_PAPER_V1"),
    lambda p:p["account"].update(initial_capital=1001), lambda p:p["account"].update(settled_cash=float("nan")),
    lambda p:p["agent"].update(status="ERROR"), lambda p:p["agent"].update(heartbeat_at=when("10:30:29")),
    lambda p:p.update(now=when("10:32")), lambda p:p.update(as_of=when("10:30:29")),
    lambda p:p.pop("open_position"),
])
def test_primary_snapshot_fails_closed(mutate):
    p = primary()
    assert run.validate_snapshot(p,DAY,when("10:31")) == p
    mutate(p)
    with pytest.raises(ValueError):
        run.validate_snapshot(p,DAY,when("10:31"))


def private(path,obj):
    path.write_text(json.dumps(obj))
    path.chmod(0o600)


def test_source_refuses_incorrect_process_before_reading_portal(tmp_path,monkeypatch):
    private(tmp_path/"session.json",run.paper_trader.session_config(DAY))
    private(tmp_path/"process.json",{"session_date":DAY,"source":str(run.REPO),"data_dir":str(tmp_path)})
    monkeypatch.setattr(run.paper_trader,"process_matches",lambda obs:False)
    monkeypatch.setattr(run,"LocalSnapshot",lambda *args:pytest.fail("unverified loopback read"))
    with pytest.raises(ValueError,match="observer"):
        run.PrimarySnapshot(tmp_path, DAY)()


def test_report_without_ledger_never_creates_folder_or_db(tmp_path):
    tmp_path.chmod(0o700)
    folder = run.folder_for(tmp_path,DAY)
    assert run.report(folder,DAY,when("10:00"))["status"] == "NOT_STARTED"
    assert list(tmp_path.iterdir()) == []
    folder.mkdir(mode=0o700)
    assert run.report(folder,DAY,when("10:00"))["research"] is None
    assert list(folder.iterdir()) == []
    (folder/run.DB_NAME).symlink_to(tmp_path/"target")
    with pytest.raises(ValueError):
        run.report(folder,DAY,when("10:00"))


def test_report_marks_stale_worker_or_archived_terminal(tmp_path):
    tmp_path.chmod(0o700)
    folder = run.folder_for(tmp_path,DAY)
    folder.mkdir(mode=0o700)
    hb = dict(session_date=DAY,status="OBSERVING",at=when("10:34").isoformat())
    private(folder/"heartbeat.json",hb)
    assert run.report(folder,DAY,when("10:35"))["worker_health"] == "STALE_OR_UNAVAILABLE"
    private(folder/"heartbeat.json",{**hb,"status":"FINISHED_UNRESOLVED"})
    assert run.report(folder,DAY,when("11:36"))["worker_health"] == "TERMINAL_HISTORY"


def test_explicit_single_day_no_rollover_no_evidence_after_window(tmp_path):
    with pytest.raises(ValueError):
        run.bounds("2026-11-23")
    folder = tmp_path/"missing"
    private(tmp_path/"session.json",run.paper_trader.session_config(DAY))
    for at in [when("11:35"),when("10:30")+timedelta(days=3)]:
        with pytest.raises(ValueError, match="rollover"):
            run.ensure_startable(tmp_path,folder,DAY,at)
    assert not folder.exists()


def test_prewindow_constructs_no_feed_and_flat_stops_1115():
    clock = Clock("10:29:55")
    hbs, calls = [], []
    def observe(day, **kw):
        calls.append(clock())
        return "OBSERVING"
    def sleep(seconds):
        if clock().time() >= when("10:30").time():
            clock.now=when("11:15")
        else:
            clock.sleep(seconds)
    run.monitor(DAY,journal=Journal(),source=lambda:None,new_feed=lambda:pytest.fail("unused feed"),
                observe=observe,write=hbs.append,clock=clock,sleep=sleep)
    assert [e["status"] for e in hbs] == ["SCHEDULED","OBSERVING","FINISHED"]
    assert calls == [when("10:30")]
    assert hbs[-1]["coverage"]=="PARTIAL" and hbs[-1]["trailing_window_gap_seconds"]==45*60


def test_open_position_only_retries_to_1135_and_never_rolls_over():
    clock = Clock("11:34:49")
    hbs, attempts = [], []
    def observe(day, **kw):
        attempts.append(clock())
        return "EXIT_PENDING"
    run.monitor(DAY,journal=Journal({"status":"EXIT_PENDING"}),source=lambda:pytest.fail("primary not used on exits"),
                new_feed=lambda:pytest.fail("wrong feed"),observe=observe,write=hbs.append,clock=clock,sleep=clock.sleep)
    assert attempts == [when("11:34:49"),when("11:34:54"),when("11:34:59")]
    assert hbs[-1]["status"]=="FINISHED_UNRESOLVED"


def test_restart_preserves_prior_observations_and_counts_sleep_between_workers():
    clock, hbs = Clock("11:14:55"), []
    previous = {"session_date":DAY,"active_polls":2,"blocked_polls":1, "poll_status_counts":{"OBSERVING":1,"BLOCKED":1},
                "first_active_poll":when("10:30:01").isoformat(),"last_active_poll":when("10:30:06").isoformat(),
                "last_entry_window_poll":when("10:30:06").isoformat(),"poll_gaps_over_30s":0}
    run.monitor(DAY,journal=Journal(),source=None,new_feed=None,observe=lambda *a,**kw:"OBSERVING",
                write=hbs.append,clock=clock,sleep=clock.sleep,previous=previous)
    assert hbs[-1]["poll_status_counts"] == {"OBSERVING":2,"BLOCKED":1}
    assert hbs[-1]["usable_polls"] == 2 and hbs[-1]["poll_gaps_over_30s"] == 1
    assert hbs[-1]["coverage"] == "PARTIAL" and hbs[-1]["trailing_window_gap_seconds"] == 5


def test_connection_allowed_only_in_period_and_logged_in_retry_throttled():
    clock = Clock("10:29:59")
    calls = []
    class Feed:
        def connect(self): calls.append("connect")
        def health(self): return {"status":"BLOCKED","quote_logged_in":False}
        def close(self): calls.append("close")
        def option_quote(self,*a): pytest.fail("used unauthorized feed")
    start,_,end=run.bounds(DAY)
    feed=run.QuoteAccess(Feed,start,end,clock)
    for at in ("10:29:59","10:30:00","10:30:10","10:30:31","11:35:00"):
        clock.now=when(at)
        with pytest.raises(ValueError):
            feed.option_quote({})
    assert calls == ["connect","close","connect","close"]
    with pytest.raises(AttributeError):
        feed.unsubscribe_all()


def test_failing_observation_records_data_gap_without_filling():
    clock=Clock("11:14:59")
    log, hbs = Journal(), []
    def broken(*a,**kw): raise ValueError("private vendor detail")
    run.monitor(DAY,journal=log,source=None,new_feed=None,observe=broken,
                write=hbs.append,clock=clock,sleep=clock.sleep)
    assert log.notes[0][:2] == ("DATA_BLOCKED","Input unavailable; no fill inferred.")
    assert hbs[0]["status"] == "BLOCKED" and hbs[0]["late_start"]
    assert hbs[-1]["status"] == "NO_COVERAGE" and hbs[-1]["blocked_polls"] == 1
    assert hbs[-1]["coverage"] == "NO_USABLE_POLLS"
    assert "private vendor detail" not in str(hbs)


@pytest.mark.parametrize("failure", ["connect", "health"])
def test_exception_drops_only_own_broken_context_and_recreates_after_deadline(failure):
    clock = Clock("10:31")
    created, freed = [], []
    class Feed:
        def __init__(self):
            self.i=len(created)
            created.append(self)
        def connect(self):
            if self.i == 0 and failure == "connect":
                raise OSError("vendor details")
        def health(self):
            if self.i == 0 and failure == "health":
                raise OSError("vendor details")
            return {"status":"CONNECTED","quote_logged_in":True}
        def close(self):
            freed.append(self.i)
        def underlying(self,symbol):
            return symbol
    start, _, end = run.bounds(DAY)
    link = run.QuoteAccess(Feed,start,end,clock)
    with pytest.raises(ValueError,match="could not be verified"):
        link.underlying("SPY")
    clock.sleep(29)
    with pytest.raises(ValueError):
        link.underlying("SPY")
    assert len(created) == 1 and freed == [0]
    clock.sleep(1)
    assert link.underlying("SPY")=="SPY" and len(created)==2
    link.close()
    assert freed == [0,1]


def test_one_etf_unavailable_is_partial_not_an_unusable_or_complete_poll():
    clock, hbs = Clock("11:14:55"), []
    previous = {"session_date":DAY, "first_active_poll":when("10:30:00").isoformat(),
                "last_active_poll":when("11:14:50").isoformat(),
                "active_polls":1, "blocked_polls":0, "poll_status_counts":{"OBSERVING":1}}
    run.monitor(DAY,journal=Journal(),source=None,new_feed=None,observe=lambda *a,**kw:"PARTIAL",
                write=hbs.append,clock=clock,sleep=clock.sleep,previous=previous)
    assert hbs[-1]["status"]=="FINISHED" and hbs[-1]["coverage"]=="PARTIAL"
    assert hbs[-1]["partial_polls"]==1 and hbs[-1]["usable_polls"]==2 and not hbs[-1]["blocked_polls"]


def test_last_scan_is_inert_always_and_exposes_only_read_only_safe_diagnostics():
    calls = []
    class Feed:
        last_scan = {"symbol":"QQQ","direction":"PUT","metadata_eligible_contracts":2,
                     "book_requested_contracts":0,"quoted_contracts":0,"reason":"vendor text","secret":"test"}
        def connect(self): calls.append("connect")
        def health(self): return {"status":"CONNECTED","quote_logged_in":True}
        def candidates(self): return []
        def close(self): pass
    start,_,end=run.bounds(DAY)
    link=run.QuoteAccess(Feed,start,end,Clock("10:31"))
    assert link.last_scan is None and calls == []
    link.candidates()
    assert link.last_scan == {"symbol":"QQQ", "direction":"PUT", "metadata_eligible_contracts":2,
                              "book_requested_contracts":0, "quoted_contracts":0}
    link.last_scan["quoted_contracts"] = 5
    assert link.last_scan["quoted_contracts"] == 0 and calls == ["connect"]
    link.close()
    assert link.last_scan is None


def test_real_engine_and_private_journal_report_with_fake_no_signal_bars(tmp_path):
    from paper_trading.late_option_log import LateOptionLog
    from paper_trading.late_options import observe_step
    clock, hbs = Clock("11:14:55"), []
    folder = run.folder_for(tmp_path, DAY)
    folder.mkdir(mode=0o700)
    class FlatQuotes:
        def connect(self): pass
        def close(self): pass
        def health(self): return {"status":"CONNECTED","quote_logged_in":True}
        def bars(self, symbol, now):
            return [{"started_at":(when("09:30")+timedelta(minutes=5*i)).isoformat(),
                     "open":99.5,"high":100,"low":99,"close":99.5,"volume":1000} for i in range(20)]
    def source():
        p = primary()
        p.update(now=clock(),as_of=clock())
        p["agent"]["heartbeat_at"] = clock()
        p["account"]["entries_today"] = 0
        return run.validate_snapshot(p,DAY,clock())
    run.monitor(DAY, journal=LateOptionLog(folder/run.DB_NAME,DAY), source=source, new_feed=FlatQuotes,
                observe=observe_step, write=hbs.append, clock=clock, sleep=clock.sleep)
    private(folder/"heartbeat.json",hbs[-1])
    result = run.report(folder,DAY,clock())
    assert result["worker"]["coverage"] == "PARTIAL" and result["worker"]["usable_polls"] == 1
    assert result["research"]["event_counts"] == {"SIGNAL_CHECK":2}
    assert result["research"]["modeled_entries"] == 0 and result["research"]["net_closed_pnl_usd"] is None
