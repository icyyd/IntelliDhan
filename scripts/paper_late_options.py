#!/usr/bin/env python3
"""One-day, local-only option quote study. No portal, broker, or primary ledger writes."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time as walltime

import paper_trader
from paper_regime_shadow import check_dir
from paper_site_sync import LocalSnapshot, _open_owned, _private_file, _read_private
from paper_trading.ledger import DEFAULT_POLICY, ET, timestamp
from paper_trading.runner import atomic_json, session_bounds, utcnow

SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[1]
PILOT_DAY = "2026-11-20"
DB_NAME = "late-options.sqlite"
TERMINAL = {"FINISHED", "FINISHED_UNRESOLVED", "NO_COVERAGE", "STORAGE_BLOCKED"}


def bounds(day):
    if day != PILOT_DAY:
        raise ValueError("This research run is authorized for one named session only")
    _, close = session_bounds(day)
    start, cutoff, end = [datetime.combine(date.fromisoformat(day), t, ET)
                          for t in (time(10, 30), time(11, 15), time(11, 35))]
    return start, cutoff, min(end, close)


def folder_for(data, day):
    check_dir(data)
    bounds(day)
    return data / ("research-late-options-" + day)


def validate_snapshot(state, day, now):
    if (not isinstance(state, dict) or state.get("mode") != "SIMULATION"
            or state.get("validation") != "RESEARCH_ONLY" or state.get("session_date") != day
            or state.get("policy") != DEFAULT_POLICY or not isinstance(state.get("trades"), list)
            or "open_position" not in state or not isinstance(state.get("events"), list)):
        raise ValueError("Not the matched V2 simulation")
    account, agent = state.get("account") or {}, state.get("agent") or {}
    if (account.get("simulated") is not True or account.get("currency") != "USD"
            or account.get("initial_capital") != 1000 or agent.get("status") not in
            {"WATCHING", "SESSION_COMPLETE", "IN_POSITION", "EXIT_PENDING"}):
        raise ValueError("Missing usable primary account or observer")
    for name in ("cash", "settled_cash", "today_realized_pnl"):
        x = account.get(name)
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise ValueError("Missing primary cash state")
    for at in (agent.get("heartbeat_at"), state.get("now"), state.get("as_of")):
        instant = timestamp(at)
        if instant.astimezone(ET).date().isoformat() != day or not 0 <= (now-instant).total_seconds() <= 30:
            raise ValueError("Stale or future primary snapshot")
    return state


class PrimarySnapshot:
    """Pin the actual owned process on first use; LocalSnapshot authenticates GET only."""
    def __init__(self, data, day, clock=utcnow):
        self.data, self.day, self.clock, self.reader = data, day, clock, None

    def __call__(self):
        if _read_private(self.data/"session.json") != paper_trader.session_config(self.day):
            raise ValueError("Primary session identity changed")
        if self.reader is None:
            obs = _read_private(self.data/"process.json")
            if (obs.get("session_date") != self.day or obs.get("data_dir") != str(self.data)
                    or obs.get("source") != str(REPO) or not paper_trader.process_matches(obs)):
                raise ValueError("Matching primary observer unavailable")
            self.reader = LocalSnapshot(self.data, self.day, obs)
        state = self.reader()
        return validate_snapshot(state, self.day, self.clock())


class QuoteAccess:
    """Lazy, quote-only connection with bounded login retries. No bars or quotes are cached here."""
    def __init__(self, new_feed, start, end, clock):
        self.new_feed, self.start, self.end, self.clock = new_feed, start, end, clock
        self.feed, self.login_ok, self.next_check = None, False, None

    @property
    def last_scan(self):
        """Copy only local scan identity/counts; do not acquire a feed or expose provider text."""
        scan = getattr(self.feed, "last_scan", None)
        if not isinstance(scan, dict) or scan.get("symbol") not in ("SPY", "QQQ") or scan.get("direction") not in ("CALL", "PUT"):
            return None
        result = {k:scan[k] for k in ("symbol", "direction")}
        for key in ("metadata_eligible_contracts", "book_requested_contracts", "quoted_contracts"):
            value = scan.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[key] = value
        return result

    def __getattr__(self, name):
        if name not in {"underlying", "bars", "candidates", "option_quote"}:
            raise AttributeError(name)
        def query(*args, **kwargs):
            now = self.clock()
            if not self.start <= now < self.end:
                raise ValueError("Quote observation outside the date-fenced research period")
            if self.next_check is None or now >= self.next_check:
                self.next_check, self.login_ok = now+timedelta(seconds=30), False
                try:
                    if self.feed is None:
                        self.feed = self.new_feed()
                    self.feed.connect()
                    health = self.feed.health()
                    self.login_ok = health.get("status") == "CONNECTED" and health.get("quote_logged_in") is True
                    if not self.login_ok:
                        raise ValueError("Unverified market-data login")
                except Exception:
                    self.close()  # release only our context; recreate after the existing retry deadline
                    raise ValueError("Read-only market-data connection could not be verified") from None
            if not self.login_ok or not self.start <= self.clock() < self.end:
                raise ValueError("Current read-only market data unavailable")
            return getattr(self.feed, name)(*args, **kwargs)
        return query

    def close(self):
        old, self.feed, self.login_ok = self.feed, None, False
        if old is not None:
            try:
                old.close()
            except Exception:
                pass  # disconnected SDK contexts need not acknowledge cleanup


def monitor(day, *, journal, source, new_feed, observe, write, clock=utcnow, sleep=walltime.sleep, previous=None):
    start, cutoff, end = bounds(day)
    feed = QuoteAccess(new_feed, start, end, clock)
    prior = (previous or {}) if (previous or {}).get("session_date") == day else {}
    first, last, gaps = (prior.get(k) for k in ("first_active_poll", "last_active_poll", "poll_gaps_over_30s"))
    gaps = gaps or 0
    last_entry_poll = prior.get("last_entry_window_poll")
    by_status = dict(prior.get("poll_status_counts", {}))
    active_polls, blocked_polls = (int(prior.get(k, 0)) for k in ("active_polls", "blocked_polls"))
    try:
        while True:
            now = clock()
            pos = journal.state()["open_position"]
            if now.astimezone(ET).date().isoformat() != day or now >= end or (now >= cutoff and pos is None):
                status = "FINISHED_UNRESOLVED" if pos else ("FINISHED" if active_polls > blocked_polls else "NO_COVERAGE")
            elif now < start:
                status = "SCHEDULED"
            else:
                if last and (now-timestamp(last)).total_seconds() > 30:
                    gaps += 1
                first = first or now.isoformat()
                last = now.isoformat()
                if now < cutoff:
                    last_entry_poll = last
                active_polls += 1
                try:
                    status = observe(day, feed=feed, source=source, journal=journal, clock=clock)
                    if status not in {"WAITING","OBSERVING","PARTIAL","BLOCKED","OPEN","EXIT_PENDING","CLOSED"}:
                        raise ValueError("Unknown observer outcome")
                except (OSError, ValueError, RuntimeError, TypeError, KeyError):
                    status = "BLOCKED"
                    journal.note("DATA_BLOCKED", "Input unavailable; no fill inferred.", clock())
                blocked_polls += int(status == "BLOCKED")
                by_status[status] = by_status.get(status,0) + 1
            late_start = (timestamp(first)-start).total_seconds() > 30 if first else None
            trailing = (max(0, (cutoff-timestamp(last_entry_poll)).total_seconds()) if last_entry_poll else
                        (cutoff-start).total_seconds()) if now >= cutoff else None
            usable = active_polls - blocked_polls
            coverage = ("NOT_STARTED" if now < start and not active_polls else "NO_USABLE_POLLS" if not usable
                        else "PARTIAL" if late_start or gaps or blocked_polls or by_status.get("PARTIAL") or (trailing or 0)>30
                        else "OBSERVED" if now >= cutoff else "IN_PROGRESS")
            write({"status":status, "at":clock().isoformat(), "first_active_poll":first, "last_active_poll":last,
                   "late_start":late_start, "last_entry_window_poll":last_entry_poll, "coverage":coverage,
                   "coverage_basis":"Worker poll coverage, not guaranteed quotes, tradable contracts or fills.",
                   "poll_status_counts":by_status.copy(), "partial_polls":by_status.get("PARTIAL", 0),
                   "usable_polls":usable, "trailing_window_gap_seconds":trailing,
                   "poll_gaps_over_30s":gaps, "active_polls":active_polls, "blocked_polls":blocked_polls,
                   "session_date":day, "live_eligible":False,
                   "reason": {"FINISHED_UNRESOLVED":"A research position lacks a verified exit. No backfill or rollover.",
                              "NO_COVERAGE":"No usable research poll recorded.",
                              "SCHEDULED":"Waiting until 10:30 ET. No market-data connection."}.get(status,status)})
            if status in TERMINAL:
                return
            sleep(min(15, max(.1, (start-now).total_seconds())) if status == "SCHEDULED" else 5)
    finally:
        feed.close()


def report(folder, day, now):
    if not folder.exists() and not folder.is_symlink():
        return {"session_date":day, "status":"NOT_STARTED", "research":None, "live_eligible":False}
    check_dir(folder)
    hb = _read_private(folder/"heartbeat.json", missing_ok=True)
    if hb and hb.get("session_date") != day:
        raise ValueError("Heartbeat belongs to another session")
    try:
        fresh = 0 <= (now-timestamp(hb.get("at"))).total_seconds() <= 30
    except ValueError:
        fresh = False
    health = "TERMINAL_HISTORY" if hb.get("status") in TERMINAL else ("FRESH" if fresh else "STALE_OR_UNAVAILABLE")
    db = folder/DB_NAME
    _private_file(db, missing_ok=True)
    out = None
    if db.exists():
        from paper_trading.late_option_log import LateOptionLog
        out = LateOptionLog(db, day, readonly=True).report()
    return {"session_date":day, "status":hb.get("status","NO_HEARTBEAT"), "worker_health":health,
            "worker":hb, "research":out, "live_eligible":False}


def ensure_startable(data, folder, day, now):
    start, cutoff, end = bounds(day)
    if now.astimezone(ET).date() != start.date() or now >= end:
        raise ValueError("No rollover or late backfill")
    if _read_private(data/"session.json") != paper_trader.session_config(day):
        raise ValueError("Not the matching primary paper configuration")
    if now >= cutoff:
        from paper_trading.late_option_log import LateOptionLog
        check_dir(folder)
        _private_file(folder/DB_NAME)
        if not LateOptionLog(folder/DB_NAME, day, readonly=True).state()["open_position"]:
            raise ValueError("Entry window has already ended and no exit is pending")


def serve(data, folder, day, instance):
    from paper_trading.late_option_feed import LateOptionFeed
    from paper_trading.late_option_log import LateOptionLog
    from paper_trading.late_options import observe_step
    if len(instance or "") != 32 or any(c not in "0123456789abcdef" for c in instance):
        raise ValueError("Invalid worker identity")
    with os.fdopen(_open_owned(folder/"worker.lock"),"a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ensure_startable(data, folder, day, utcnow())
        home = folder/"sdk-home"
        home.mkdir(mode=0o700, exist_ok=True)
        check_dir(home)
        safe_env = paper_trader.environment(folder)
        os.environ.clear()
        os.environ.update({**safe_env, "PYTHONNOUSERSITE":"1"})
        dest = folder/"heartbeat.json"
        prior = _read_private(dest, missing_ok=True)
        retained = dict(prior)
        def write(hb):
            retained.update(hb)
            _private_file(dest, missing_ok=True)
            atomic_json(dest, {**retained, "pid":os.getpid(), "instance":instance})
        try:
            monitor(day, journal=LateOptionLog(folder/DB_NAME, day), source=PrimarySnapshot(data, day),
                    new_feed=LateOptionFeed, observe=observe_step, write=write, previous=prior)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            write({"status":"STORAGE_BLOCKED", "at":utcnow().isoformat(), "session_date":day,
                   "reason":"Worker stopped; private observation state could not be verified.", "live_eligible":False})
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start","serve","report"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--session-date", type=date.fromisoformat, required=True)
    parser.add_argument("--instance", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    day, data = args.session_date.isoformat(), args.data_dir.absolute()
    folder = folder_for(data, day)
    if args.command == "report":
        print(json.dumps(report(folder, day, utcnow()), allow_nan=False))
        return 0
    os.umask(0o077)
    ensure_startable(data, folder, day, utcnow())
    folder.mkdir(mode=0o700, exist_ok=True)
    check_dir(folder)
    if args.command == "serve":
        serve(data, folder, day, args.instance)
        return 0
    instance = secrets.token_hex(16)
    with os.fdopen(_open_owned(folder/"worker.log"), "ab") as log:
        cmd = [sys.executable, str(SCRIPT), "serve", "--session-date", day, "--data-dir", str(data),"--instance",instance]
        child = subprocess.Popen(cmd, cwd=REPO, env={**paper_trader.environment(folder), "PYTHONNOUSERSITE":"1"},
                                 stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        h = _read_private(folder/"heartbeat.json", missing_ok=True)
        if h.get("instance") == instance and h.get("pid") == child.pid and h.get("session_date") == day:
            print(json.dumps({"session_date":day, "status":h["status"], "live_eligible":False}))
            return 0 if h["status"] != "STORAGE_BLOCKED" else 1
        if child.poll() is not None:
            raise ValueError("Worker not verified; inspect the private report before retrying")
        walltime.sleep(.2)
    raise ValueError("Worker startup is not yet verified; inspect the report before retrying")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
        print(f"Late quote research unavailable ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
