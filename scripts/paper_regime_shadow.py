#!/usr/bin/env python3
"""Date-fenced matched-subset experiment. Never owns cash, orders or positions."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import csv
from datetime import date, datetime, time, timedelta
import fcntl
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import subprocess
import sys
import time as walltime

import paper_trader
from paper_site_sync import LocalSnapshot, _open_owned, _private_file, _read_private
from paper_trading.feed import OpenDFeed
from paper_trading.ledger import ET, timestamp
from paper_trading.regime import RULE_VERSION, regime_context
from paper_trading.runner import atomic_json, session_bounds, utcnow

SYMBOLS = ("SPY", "QQQ")
BASELINES = {"ORB_RETEST_PAPER_V1", "ORB_RETEST_SNIPER_V2"}
SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[1]
CACHE = REPO / "data/orb-study"


def check_dir(path):
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("Expected an owned 0700 directory without symlinks")


def paths(data, day, *, create=False):
    data = Path(data).absolute()
    check_dir(data)
    session = _read_private(data / "session.json")
    if (session.get("session_date") != day or session.get("mode") != "SIMULATION"
            or session.get("source") != str(REPO) or session.get("strategy") not in BASELINES):
        raise ValueError("Not this observer's date-fenced paper session")
    session_bounds(day)
    path = data / ("research-shadow-" + day)
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    check_dir(path)
    return path


def prepare_prior(day, now, cache=CACHE):
    """Select only prior openings; pin actual receipt time, not guessed vendor times."""
    import exchange_calendars as xcals

    schedule = xcals.get_calendar("XNYS").schedule
    days = [x.date().isoformat() for x in schedule.loc[:day].index][-21:-1]
    if len(days) != 20:
        raise ValueError("Prior exchange session window unavailable")
    dayset = set(days)
    prior = {"day": day, "version": RULE_VERSION, "received_at": now.isoformat(), "openings": {},
             "vix": None, "diagnostics": []}
    for sym in SYMBOLS:
        bars = []
        # Explicit input format: this is the research downloader's END-labeled
        # regular-session file, not a TradingView or other vendor export.
        files = sorted(cache.glob(f"{sym}_????-??-??_{days[-1]}.csv"))
        if len(files) == 1 and files[0].is_file() and not files[0].is_symlink():
            with files[0].open(newline="") as fh:
                for row in csv.DictReader(fh):
                    raw = row["time_key"]
                    if raw[:10] not in dayset or raw[11:] not in {"09:35:00", "09:40:00", "09:45:00"}:
                        continue
                    at = datetime.fromisoformat(raw).replace(tzinfo=ET) - timedelta(minutes=5)
                    bars.append({"symbol": sym, "started_at": at.isoformat(), "volume": row["volume"]})
        else:
            prior["diagnostics"].append(f"{sym}: missing or ambiguous prior OpenD cache")
        prior["openings"][sym] = bars
    source = cache / "VIX_History.csv"
    if source.is_file() and not source.is_symlink():
        wanted = date.fromisoformat(days[-1]).strftime("%m/%d/%Y")
        with source.open(newline="") as fh:
            rows = [row for row in csv.DictReader(fh) if row["DATE"] == wanted]
        if len(rows) == 1:
            prior["vix"] = {"source": "CBOE_VIX_HISTORY_CSV", "session_date": days[-1],
                            "close": rows[0]["CLOSE"], "available_at": now.isoformat()}
    if prior["vix"] is None:
        prior["diagnostics"].append("Exact prior exchange-session Cboe VIX close unavailable")
    return prior


def database(folder):
    for suffix in ("", "-journal", "-wal", "-shm"):
        _private_file(folder / ("comparison.sqlite" + suffix), missing_ok=True)
    # Create explicitly mode 0600 even if called by an interactive report/test.
    os.close(_open_owned(folder / "comparison.sqlite"))
    db = sqlite3.connect(folder / "comparison.sqlite", timeout=2)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS contexts(symbol TEXT PRIMARY KEY, captured TEXT NOT NULL, body TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, symbol TEXT, kind TEXT, at TEXT,
          reason TEXT, decision TEXT, trade_id TEXT, net_cents INTEGER, rvol TEXT, vix TEXT);
      CREATE UNIQUE INDEX IF NOT EXISTS unique_receipt ON observations(trade_id,kind) WHERE trade_id IS NOT NULL;
    """)
    return db


def dump(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def optional_gamma(folder):
    """Optional operator-attested observation; never used as a directional signal."""
    path = folder / "gamma-observations.json"
    if not path.exists():
        return {}, "No trusted point-in-time gamma archive installed."
    try:
        _private_file(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Oversized gamma file")
        value = json.loads(raw)
        if not isinstance(value, dict) or not set(value) <= set(SYMBOLS):
            raise ValueError("Unexpected gamma file")
        return value, "Operator-attested local archive; no dealer sign inferred."
    except (ValueError, OSError, TypeError):
        return {}, "Gamma archive invalid or not private; gamma is unavailable."


def capture_contexts(db, day, prior, feed, *, clock=utcnow, gamma=None):
    """Freeze before first possible trigger. Never recover missing factors from later data."""
    local = clock().astimezone(ET)
    if local.date().isoformat() != day or not time(9, 45, 5) <= local.time() < time(9, 55):
        return
    diagnostics = {}
    for sym in SYMBOLS:
        if db.execute("SELECT 1 FROM contexts WHERE symbol=?", (sym,)).fetchone():
            continue
        try:
            feed.connect()
            bars = feed.bars(sym, clock())
        except (ValueError, RuntimeError, OSError) as exc:
            diagnostics[sym] = f"Read-only opening bars unavailable ({type(exc).__name__})."
            continue
        captured = clock()
        if captured.astimezone(ET).time() >= time(9, 55) or captured.astimezone(ET).date().isoformat() != day:
            return
        today = [b for b in bars if timestamp(b["started_at"]).astimezone(ET).date().isoformat() == day]
        context = regime_context(sym, prior["openings"][sym] + today, captured, vix=prior["vix"],
                                 gamma=(gamma or {}).get(sym),
                                 volume_source="MOOMOO_OPEND_5M_RTH")
        # Allow a delayed final bar a few retries before the absolute deadline.
        if context["rvol"]["status"] == "UNKNOWN" and captured.astimezone(ET).time() < time(9, 54):
            continue
        with db:
            db.execute("INSERT OR IGNORE INTO contexts VALUES (?,?,?)", (sym, captured.isoformat(), dump(context)))
    return diagnostics


def observe(db, state, day, now, *, baseline_strategy):
    if (state.get("mode") != "SIMULATION" or state.get("validation") != "RESEARCH_ONLY"
            or state.get("session_date") != day or state.get("policy", {}).get("live_eligible") is not False
            or baseline_strategy not in BASELINES or state["policy"].get("strategy") != baseline_strategy):
        raise ValueError("Unexpected paper source")
    for stamp in [state.get("now"), state.get("agent", {}).get("heartbeat_at")]:
        if not 0 <= (now - timestamp(stamp)).total_seconds() <= 30:
            raise ValueError("Paper observer heartbeat or export is stale")
    contexts = {symbol: (timestamp(at), json.loads(body)) for symbol, at, body in db.execute("SELECT * FROM contexts")}
    trades = {t["trade_id"]: t for t in state.get("trades", []) if t.get("simulated") is True and t.get("session_date") == day}
    with db:
        for event in state["events"]:
            kind = event["kind"]
            if kind not in {"SIGNAL_CHECK", "SIGNAL_INVALIDATED", "SKIPPED", "DATA_BLOCKED", "ENTRY", "EXIT", "EXIT_REQUESTED"}:
                continue
            at = timestamp(event["recorded_at"])
            if at.astimezone(ET).date().isoformat() != day or at > now:
                continue
            details = event.get("details") or {}
            sym = details.get("symbol")
            if kind == "EXIT_REQUESTED" and not sym:
                sym = trades.get(details.get("trade_id"), {}).get("symbol")
            if sym not in SYMBOLS:
                continue  # Includes legacy ancillary events without symbol.
            ident, pnl = None, None
            if kind in {"ENTRY", "EXIT"}:
                ident = details.get("trade_id")
                if not ident:
                    continue  # Runner's additional free-text EXIT is not a fill.
                if (details.get("simulated") is not True or details.get("session_date") != day
                        or details.get("strategy") != baseline_strategy
                        or details.get("quantity") != 1 or details.get("symbol") != sym):
                    raise ValueError("Not a verified baseline paper receipt")
                if kind == "EXIT":
                    pnl = details.get("realized_pnl_cents")
                    if (details.get("status") != "CLOSED" or not isinstance(pnl, int) or isinstance(pnl, bool)
                            or pnl != details["exit_credit_cents"] - details["entry_debit_cents"]):
                        raise ValueError("Paper receipt P/L does not reconcile")
            # Eligibility is measured at the entry time, not the exit or polling
            # time. Missing measurements never become an implicit PASS.
            decision_at = timestamp(details["entered_at"]) if ident else at
            ctx = contexts.get(sym)
            factors = ctx[1] if ctx and ctx[0] <= decision_at else {}
            decision = factors.get("combined", "UNKNOWN")
            db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (event["event_id"], sym, kind, at.isoformat(), event["reason"],
                        decision, ident, pnl, factors.get("rvol", {}).get("status", "UNKNOWN"),
                        factors.get("vix", {}).get("status", "UNKNOWN")))


def report(db):
    contexts = {sym: json.loads(body) for sym, body in db.execute("SELECT symbol,body FROM contexts")}
    events = db.execute("SELECT symbol,kind,at,reason,decision,trade_id,net_cents FROM observations ORDER BY at,id").fetchall()
    entries = {i: (s, d) for s, k, _, _, d, i, _ in events if k == "ENTRY"}
    exits = {i: p for _, k, _, _, _, i, p in events if k == "EXIT" and i in entries}
    matched = {i for i, (_, d) in entries.items() if d == "PASS"}
    passed_skips = {s for s, k, _, _, d, _, _ in events if k == "SKIPPED" and d == "PASS"}
    arms = {}
    for arm, field in (("baseline", None), ("rvol_only", "rvol"), ("vix_only", "vix"), ("combined", "decision")):
        rows = db.execute("SELECT trade_id," + (field or "'PASS'") + " FROM observations WHERE kind='ENTRY'")
        decisions = dict(rows)
        eligible = {i for i, status in decisions.items() if status == "PASS"}
        closed = eligible & exits.keys()
        arms[arm] = {"entries": len(eligible), "closed": len(closed), "unknown": sum(s == "UNKNOWN" for s in decisions.values()),
                     "net_realized_cents": sum(exits[i] for i in closed) if closed else None}
    return {"version": RULE_VERSION, "scope": "MATCHED_BASELINE_PAPER_FILLS_ONLY", "live_eligible": False,
            "contexts": contexts, "arms": arms, "counts": dict(Counter(r[1] for r in events)),
            "baseline_entries": len(entries), "baseline_closed": len(exits),
            "baseline_realized_cents": sum(exits.values()) if exits else None,
            "matched_entries": len(matched), "matched_closed": len(matched & exits.keys()),
            "matched_realized_cents": sum(exits[i] for i in matched & exits.keys()) if matched & exits.keys() else None,
            "unknown_entries": sum(d == "UNKNOWN" for _, d in entries.values()),
            "passed_but_no_fill_symbols": sorted(passed_skips - {s for s, _ in entries.values()}),
            "passing_skip_event_counts": dict(Counter(s for s, k, _, _, d, _, _ in events if k == "SKIPPED" and d == "PASS")),
            "last_observations": [{"symbol": s, "kind": k, "at": at, "reason": reason, "overlay": d}
                                  for s, k, at, reason, d, _, _ in events[-12:]],
            "limitations": ["Already-net modeled receipts; no independent fills, extra capital reuse or extra fees.",
                            "Baseline skips/exposure may hide later opportunities; only observed fills are comparable.",
                            "Skip events are deduplicated source messages, not distinct opportunities. A symbol may later enter.",
                            "Missing pre-entry factors are unknown. No verified dealer-positioning feed."]}


def serve(folder, data, day, instance, cache):
    os.umask(0o077)
    with os.fdopen(_open_owned(folder / "worker.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior_file = folder / "prior.json"
        strategy = _read_private(data / "session.json")["strategy"]
        if strategy not in BASELINES:
            raise ValueError("Unknown session strategy")
        if not prior_file.exists():
            atomic_json(prior_file, {**prepare_prior(day, utcnow(), cache), "baseline_strategy": strategy})
        _private_file(prior_file)
        prior = json.loads(prior_file.read_text())
        if prior.get("day") != day or prior.get("version") != RULE_VERSION or prior.get("baseline_strategy") != strategy:
            raise ValueError("Existing context belongs to another experiment")
        feed, reader = OpenDFeed(), None
        _, close = session_bounds(day)
        retry_at = 0
        factor_reason = "Waiting for the opening range to close."
        gamma_note = "No verified gamma snapshot received."
        with closing(database(folder)) as db:
            try:
                while True:
                    now = utcnow()
                    local = now.astimezone(ET)
                    state, reason = "SCHEDULED", "Waiting for the opening window."
                    if local.date().isoformat() == day and local.time() >= time(9, 35):
                        if time(9, 45, 5) <= local.time() < time(9, 55) and walltime.monotonic() >= retry_at:
                            try:
                                gamma, gamma_note = optional_gamma(folder)
                                errors = capture_contexts(db, day, prior, feed, gamma=gamma)
                                factor_reason = dump(errors) if errors else "Frozen factors recorded when coverage permits; waiting on any incomplete opening bars."
                            except (ValueError, RuntimeError, OSError) as exc:
                                factor_reason = f"Read-only bar or factor check failed ({type(exc).__name__}); no inferred replacement."
                            retry_at = walltime.monotonic() + 15
                        try:
                            obs = _read_private(data / "process.json")
                            if (obs.get("session_date") != day or obs.get("data_dir") != str(data)
                                    or obs.get("source") != str(REPO)):
                                raise ValueError("Paper observer identity mismatch")
                            if not reader or reader.observer != obs:
                                reader = LocalSnapshot(data, day, obs)
                            snapshot = reader()
                            observe(db, snapshot, day, utcnow(), baseline_strategy=strategy)
                            state, reason = "OBSERVING", "Comparing only the original paper observer's decisions."
                        except (ValueError, KeyError, OSError, TypeError):
                            state, reason = "SOURCE_BLOCKED", "Missing, stale, or unverified paper export; no new results assumed."
                    if now >= close + timedelta(minutes=1):
                        state, reason = "FINISHED", "Comparison ended; unobserved exits are unresolved."
                    count = db.execute("SELECT COUNT(*) FROM contexts").fetchone()[0]
                    if local.date().isoformat() == day and local.time() >= time(9, 55) and count < len(SYMBOLS):
                        factor_reason = "Pre-entry measurement deadline missed for one or more symbols; permanently UNKNOWN."
                    atomic_json(folder / "heartbeat.json", {"pid": os.getpid(), "instance": instance,
                                "status": state, "reason": reason, "at": utcnow().isoformat(),
                                "session_date": day, "baseline_strategy": strategy, "frozen_symbols": count, "live_eligible": False,
                                "prior_vix": prior["vix"], "prior_diagnostics": prior["diagnostics"],
                                "coverage": factor_reason, "gamma_source": gamma_note})
                    if state == "FINISHED":
                        break
                    walltime.sleep(15 if state == "SCHEDULED" else 5)
            finally:
                feed.close()


def worker_status(heartbeat, now):
    result = dict(heartbeat)
    if result.get("status") == "FINISHED":
        return {**result, "worker_health": "TERMINAL_HISTORY"}
    try:
        current = 0 <= (now - timestamp(result.get("at"))).total_seconds() <= 30
    except (TypeError, ValueError):
        current = False
    return {**result, "worker_health": "FRESH" if current else "STALE_OR_UNAVAILABLE"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "serve", "report"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--session-date", type=date.fromisoformat, required=True)
    parser.add_argument("--history-dir", type=Path, default=CACHE)
    parser.add_argument("--instance", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    day = args.session_date.isoformat()
    data = args.data_dir.absolute()
    folder = paths(data, day, create=args.command != "report")
    if args.command == "report":
        # Do not create or repair storage when inspecting a report.
        _private_file(folder / "comparison.sqlite")
        with closing(sqlite3.connect((folder / "comparison.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
            print(dump({"worker": worker_status(_read_private(folder / "heartbeat.json", missing_ok=True), utcnow()), **report(db)}))
        return 0
    if args.command == "serve":
        serve(folder, data, day, args.instance, args.history_dir)
        return 0
    if utcnow() >= session_bounds(day)[1]:
        raise ValueError("Cannot start a forward experiment after session close")
    os.umask(0o077)
    instance = secrets.token_hex(16)
    # Worker owns the exclusive lock: simultaneous starts cannot both observe.
    with os.fdopen(_open_owned(folder / "shadow.log"), "ab") as log:
        cmd = [sys.executable, str(SCRIPT), "serve", "--session-date", day, "--data-dir", str(data),
               "--instance", instance, "--history-dir", str(args.history_dir.absolute())]
        child = subprocess.Popen(cmd, cwd=REPO, env=paper_trader.environment(data),
                                 stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        if child.poll() is not None:
            raise ValueError("Shadow worker not started; check for an existing worker or private log")
        h = _read_private(folder / "heartbeat.json", missing_ok=True)
        if h.get("pid") == child.pid and h.get("instance") == instance:
            print(dump({"running": True, "session_date": day, "research_only": True, "worker": h["status"]}))
            return 0
        walltime.sleep(.2)
    raise ValueError("Startup not yet verified; inspect the private heartbeat before retrying")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Research shadow unavailable ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
