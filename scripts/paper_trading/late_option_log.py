"""Private, append-only observations for the 10:30–11:15 research arm.

This is not an account or an execution adapter. The primary journal is an
in-memory read-only snapshot supplied by its owner, never a database path.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from .ledger import (
    DEFAULT_POLICY,
    ET,
    Ledger,
    cents,
    dollars,
    fill_cents,
    number,
    session_dates,
    timestamp,
)

VERSION = "ORB_LATE_OPTIONS_FORWARD_V1"
PARENT = "ORB_RETEST_SNIPER_V2"
FIVE = timedelta(minutes=5)
FEE = cents(DEFAULT_POLICY["fee_per_contract_side"])
SLIP = DEFAULT_POLICY["slippage_per_share"]


def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _dump(v):
    return json.dumps(v, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _reason(s):
    if not isinstance(s, str) or not s.strip() or len(s) > 1200:
        raise ValueError("a bounded, nonempty observation reason is required")
    return s.strip()


class _Quotes:
    """Reuse the baseline's quote checks, without constructing its writable ledger."""

    policy = DEFAULT_POLICY
    _fresh = Ledger._fresh
    _underlying = Ledger._underlying
    _option = Ledger._option


def _book(quote, symbol, now, earliest, *, entry):
    if (
        not isinstance(quote, dict)
        or quote.get("source") != "MOOMOO_OPEND"
        or quote.get("timestamp_basis") != "PROVIDER_ORDER_BOOK_AND_SNAPSHOT"
    ):
        raise ValueError("provider-timestamped OpenD order book and metadata required")
    times = [
        timestamp(quote.get(k))
        for k in ("bid_observed_at", "ask_observed_at", "metadata_observed_at")
    ]
    if (
        min(times) != timestamp(quote.get("observed_at"))
        or min(times) < earliest
        or max(times) > now
    ):
        raise ValueError(
            "book timestamps must follow decision, agree with observation and not be future"
        )
    checked = _Quotes()._option(quote, symbol, now, entry=entry)
    if (
        not isinstance(checked["option_id"], str)
        or not checked["option_id"].startswith("US." + symbol)
        or number(quote.get("contract_size", 100)) != 100
        or number(quote["bid"]) <= 0
        or any(number(quote.get(k)) < 1 for k in ("ask_size", "bid_size"))
    ):
        raise ValueError("identified contract with a positive, sized, two-sided book required")
    return checked


def _spot(quote, symbol, now):
    if not isinstance(quote, dict) or quote.get("source") != "MOOMOO_OPEND":
        raise ValueError("provider-timestamped OpenD underlying observation required")
    return _Quotes()._underlying(quote, symbol, now)


def _state(events):
    signals, trades, consumed = {}, {}, set()
    for e in events:
        k, sid, payload = e["kind"], e["signal_id"], e["details"]
        if k == "SIGNAL":
            signals[sid] = payload
        elif k in ("ENTRY", "EXIT", "EXIT_REQUESTED"):
            trades[payload["trade_id"]] = payload
            consumed.add(sid)
        elif k in ("SIGNAL_INVALIDATED", "REJECTED"):
            consumed.add(sid)
    trades = list(trades.values())
    closed = [t["exited_at"] for t in trades if t["status"] == "CLOSED"]
    return {
        "open_position": next((t for t in trades if t["status"] != "CLOSED"), None),
        "trades": trades,
        "signals": list(signals.values()),
        "invalidated_signal_ids": sorted(consumed),
        "last_exit_at": max(closed, default=None),
    }


class LateOptionLog:
    def __init__(self, path, day, *, readonly=False):
        self.path, self.day, self.readonly = Path(path).absolute(), day, readonly
        day_obj = date.fromisoformat(day)
        if session_dates(datetime.combine(day_obj, time(10, 30), ET))[0] != day:
            raise ValueError("expected an exchange session")
        folder = self.path.parent
        if (
            self.path.name != "late-options.sqlite"
            or folder.name != "research-late-options-" + day
            or folder.resolve() != folder
            or stat.S_IMODE(folder.stat().st_mode) != 0o700
            or folder.stat().st_uid != os.getuid()
        ):
            raise ValueError("expected the separate owned private late-options research directory")
        if not readonly:
            try:
                os.close(
                    os.open(self.path, os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                )
            except FileExistsError:
                pass
        with self._db(write=not readonly) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables and not readonly:
                db.execute("CREATE TABLE late_meta(session TEXT NOT NULL, version TEXT NOT NULL)")
                db.execute("INSERT INTO late_meta VALUES (?,?)", (day, VERSION))
                db.execute("CREATE TABLE late_events(id TEXT PRIMARY KEY, body TEXT NOT NULL)")
                for action in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER no_{action} BEFORE {action} ON late_events "
                        "BEGIN SELECT RAISE(ABORT,'research audit is immutable'); END"
                    )
            elif tables != {"late_meta", "late_events"}:
                raise ValueError("not a late-options research journal; refusing to modify it")
            if db.execute("SELECT session,version FROM late_meta").fetchall() != [(day, VERSION)]:
                raise ValueError("journal date or research identity mismatch")

    @contextmanager
    def _db(self, *, write=False):
        if self.readonly and write:
            raise ValueError("this report handle is read-only")
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                info = Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                if not suffix:
                    raise
                continue
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ValueError(
                    "journal and side files must be privately owned regular files without links"
                )
        db = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=3
        )
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _all(db):
        return [
            json.loads(row[0]) for row in db.execute("SELECT body FROM late_events ORDER BY rowid")
        ]

    def events(self):
        with self._db() as db:
            return self._all(db)

    def state(self):
        return _state(self.events())

    def _at(self, now, *, entry=False):
        at = timestamp(now)
        local = at.astimezone(ET)
        if (
            local.date().isoformat() != self.day
            or entry
            and not time(10, 30) <= local.time() < time(11, 15)
        ):
            raise ValueError("outside the dated late research observation window")
        return at

    def _event(self, db, key, kind, now, reason, details, symbol=None, sid=None):
        data = dict(
            event_id=key,
            kind=kind,
            at=self._at(now).isoformat(),
            reason=_reason(reason),
            symbol=symbol,
            signal_id=sid,
            details=details,
        )
        db.execute("INSERT OR IGNORE INTO late_events VALUES (?,?)", (key, _dump(data)))

    def note(self, kind, reason, now, *, symbol=None, key=None):
        if kind not in ("SKIPPED", "DATA_BLOCKED", "SIGNAL_CHECK") or symbol not in (
            None,
            "SPY",
            "QQQ",
        ):
            raise ValueError("unknown research diagnostic")
        identity = "note:" + _hash(_dump([kind, symbol, reason, key]))
        with self._db(write=True) as db:
            self._event(db, identity, kind, now, reason, {}, symbol)

    def _validate_signal(self, s, now):
        ev = s.get("evidence") or {}
        parent = ev.get("parent_signal_id")
        if (
            s.get("symbol") not in ("SPY", "QQQ")
            or s.get("direction") not in ("CALL", "PUT")
            or ev.get("strategy") != VERSION
            or ev.get("technical_strategy") != PARENT
            or s.get("live_eligible", ev.get("live_eligible")) is not False
            or s.get("research_only", ev.get("research_only")) is not True
            or not isinstance(parent, str)
            or len(parent) != 64
            or any(c not in "0123456789abcdef" for c in parent)
            or s.get("signal_id") != _hash(f"{VERSION}|{parent}")
        ):
            raise ValueError("signal must carry verifiable late-research provenance")
        opened, expires = self._at(s.get("created_at"), entry=True), timestamp(s.get("expires_at"))
        if (
            opened.minute % 5
            or opened.second
            or opened.microsecond
            or expires != opened + FIVE
            or not opened <= self._at(now, entry=True) < expires
        ):
            raise ValueError("late research signal must be inside its five-minute trigger candle")
        trigger, stop, target = [number(s.get(k)) for k in ("trigger", "stop", "target")]
        if not (stop < trigger < target if s["direction"] == "CALL" else target < trigger < stop):
            raise ValueError("invalid stop/trigger/target geometry")
        _reason(s.get("reason"))

    def signal(self, signal, now):
        self._validate_signal(signal, now)
        sid = signal["signal_id"]
        with self._db(write=True) as db:
            old = db.execute(
                "SELECT body FROM late_events WHERE id=?", ("signal:" + sid,)
            ).fetchone()
            if old and json.loads(old[0])["details"] != signal:
                raise ValueError("research signal identity changed")
            self._event(
                db, "signal:" + sid, "SIGNAL", now, signal["reason"], signal, signal["symbol"], sid
            )
        return sid

    def _outcome(self, sid, reason, now, kind):
        with self._db(write=True) as db:
            state = _state(self._all(db))
            s = next((x for x in state["signals"] if x["signal_id"] == sid), None)
            if not s:
                raise ValueError("unknown research signal")
            if sid in state["invalidated_signal_ids"]:
                return
            k = kind + ":" + sid + (":" + _hash(reason) if kind == "SKIPPED" else "")
            self._event(db, k, kind, now, reason, {}, s["symbol"], sid)

    def reject(self, signal_id, reason, now, *, final=False):
        self._outcome(signal_id, reason, now, "REJECTED" if final else "SKIPPED")

    def invalidate(self, signal_id, reason, now):
        self._outcome(signal_id, reason, now, "SIGNAL_INVALIDATED")

    def trigger(self, signal_id, underlying_quote, now):
        with self._db(write=True) as db:
            state = _state(self._all(db))
            s = next((s for s in state["signals"] if s["signal_id"] == signal_id), None)
            if not s or signal_id in state["invalidated_signal_ids"]:
                raise ValueError("unknown or consumed research signal")
            at = self._at(now, entry=True)
            self._validate_signal(s, at)
            spot = _spot(underlying_quote, s["symbol"], at)
            price = number(spot["price"])
            saved = json.loads(
                db.execute(
                    "SELECT body FROM late_events WHERE id=?", ("signal:" + signal_id,)
                ).fetchone()[0]
            )
            if at < timestamp(saved["at"]):
                raise ValueError("underlying trigger predates the recorded setup")
            if timestamp(spot["observed_at"]) < timestamp(s["created_at"]) or not (
                s["trigger"] < price < s["target"]
                if s["direction"] == "CALL"
                else s["target"] < price < s["trigger"]
            ):
                raise ValueError("no fresh underlying crossing before target")
            self._event(
                db,
                "trigger:" + signal_id,
                "TRIGGER",
                at,
                "Underlying crossed the completed retest trigger.",
                {"underlying_quote": spot},
                s["symbol"],
                signal_id,
            )

    def _gate(self, b, now, state):
        at = self._at(now, entry=True)
        if (
            not isinstance(b, dict)
            or b.get("mode") != "SIMULATION"
            or b.get("validation") != "RESEARCH_ONLY"
            or b.get("session_date") != self.day
            or b.get("policy") != DEFAULT_POLICY
            or "open_position" not in b
            or not isinstance(b.get("trades"), list)
        ):
            raise ValueError("missing or unexpected full primary Simulation snapshot")
        for t in (b.get("as_of"), b.get("now"), (b.get("agent") or {}).get("heartbeat_at")):
            if not 0 <= (at - timestamp(t)).total_seconds() <= 30:
                raise ValueError("primary snapshot/heartbeat is stale or future")
        if b["open_position"] is not None or state["open_position"] is not None:
            raise ValueError("a primary or late position is open or unresolved")
        actual, own, acct = b["trades"], state["trades"], b.get("account") or {}
        if (
            acct.get("simulated") is not True
            or acct.get("currency") != "USD"
            or acct.get("initial_capital") != 1000
            or acct.get("entries_today") != len(actual)
        ):
            raise ValueError("primary account identity or trade coverage is incomplete")
        spent, loss, realized, seen, exits = 0, 0, 0, set(), []
        for t in actual:
            key = t.get("trade_id")
            debit, credit, pnl = (
                t.get(k) for k in ("entry_debit_cents", "exit_credit_cents", "realized_pnl_cents")
            )
            if (
                not key
                or key in seen
                or t.get("simulated") is not True
                or t.get("quantity") != 1
                or t.get("strategy") != PARENT
                or t.get("symbol") not in ("SPY", "QQQ")
                or t.get("session_date") != self.day
                or t.get("status") != "CLOSED"
                or not all(
                    isinstance(v, int) and not isinstance(v, bool) for v in (debit, credit, pnl)
                )
                or debit <= 0
                or pnl != credit - debit
            ):
                raise ValueError("primary historical receipt is incomplete, repeated or unresolved")
            entered = self._at(t.get("entered_at"))
            exited = self._at(t.get("exited_at"))
            if entered.astimezone(ET).time() >= time(10, 30) or not entered <= exited <= at:
                raise ValueError("primary receipt time is inconsistent")
            seen.add(key)
            exits.append(exited)
            spent += debit - min(0, credit)
            loss += min(0, pnl)
            realized += pnl
        if cents(acct.get("settled_cash")) != 100000 - spent:
            raise ValueError("primary settled cash does not reconcile to the supplied receipts")
        if (
            cents(acct.get("cash")) != 100000 + realized
            or cents(acct.get("today_realized_pnl")) != realized
        ):
            raise ValueError(
                "primary cash or daily realized P/L does not reconcile to closed receipts"
            )
        for t in own:
            spent += t["entry_debit_cents"] - min(0, t["exit_credit_cents"])
            loss += min(0, t["realized_pnl_cents"])
            exits.append(timestamp(t["exited_at"]))
        settled, risk = 100000 - spent, 50000 + loss
        ceiling = min(50000, settled - 2 * FEE, risk - 2 * FEE)
        if ceiling < 1:
            raise ValueError("no settled funds or frozen daily risk left after reserving both fees")
        return {
            "last_exit_at": max(exits).isoformat() if exits else None,
            "max_premium": dollars(ceiling),
            "available_settled_cents": settled,
            "remaining_risk_cents": risk,
        }

    def gate(self, baseline, now):
        return self._gate(baseline, now, self.state())

    def enter(self, signal, option_quote, underlying_quote, now, baseline):
        sid = signal["signal_id"]
        with self._db(write=True) as db:
            events = self._all(db)
            state = _state(events)
            prior = next((t for t in state["trades"] if t["signal_id"] == sid), None)
            if prior:
                return prior
            at = self._at(now, entry=True)
            self._validate_signal(signal, at)
            registered = next(
                (e for e in events if e["kind"] == "SIGNAL" and e["signal_id"] == sid), None
            )
            if (
                not registered
                or registered["details"] != signal
                or timestamp(registered["at"]) > at
                or sid in state["invalidated_signal_ids"]
            ):
                raise ValueError("missing, changed or already consumed signal")
            observed_trigger = next(
                (e for e in events if e["kind"] == "TRIGGER" and e["signal_id"] == sid), None
            )
            if not observed_trigger or timestamp(observed_trigger["at"]) > at:
                raise ValueError("record a fresh underlying trigger before any modeled entry")
            budget = self._gate(baseline, at, state)
            created = timestamp(signal["created_at"])
            if budget["last_exit_at"]:
                e = signal["evidence"]
                reset, breakout, retest = (
                    timestamp(e.get(k)) for k in ("reset_bar_at", "breakout_at", "retest_at")
                )
                if not (
                    timestamp(budget["last_exit_at"]) <= reset
                    and reset + FIVE <= breakout
                    and breakout + FIVE <= retest <= breakout + 3 * FIVE
                    and retest + FIVE == created
                ):
                    raise ValueError(
                        "new setup requires a full post-exit reset, breakout, then retest"
                    )
            symbol = signal["symbol"]
            spot = _spot(underlying_quote, symbol, at)
            option = _book(option_quote, symbol, at, created, entry=True)
            if any(
                timestamp(option[k]) < timestamp(observed_trigger["at"])
                for k in ("ask_observed_at", "bid_observed_at")
            ):
                raise ValueError(
                    "both sides must be observed after the recorded trigger; pre-cross books cannot fill"
                )
            price, trig, tgt = float(spot["price"]), signal["trigger"], signal["target"]
            if (
                timestamp(spot["observed_at"])
                < timestamp(observed_trigger["details"]["underlying_quote"]["observed_at"])
                or option["option_type"].upper() != signal["direction"]
                or not (trig < price < tgt if signal["direction"] == "CALL" else tgt < price < trig)
            ):
                raise ValueError("underlying quote has not freshly triggered or has reached target")
            price_c = fill_cents(option["ask"], SLIP, entry=True)
            premium = price_c * 100
            if premium > cents(budget["max_premium"]):
                raise ValueError(
                    "option exceeds remaining settled cash, premium or frozen daily loss ceiling"
                )
            trade = {
                **{
                    k: signal[k]
                    for k in ("symbol", "direction", "trigger", "stop", "target", "evidence")
                },
                **{k: option[k] for k in ("option_id", "option_type", "expiration_date", "strike")},
                "trade_id": "late_" + sid,
                "signal_id": sid,
                "session_date": self.day,
                "strategy": VERSION,
                "simulated": True,
                "research_only": True,
                "live_eligible": False,
                "quantity": 1,
                "status": "OPEN",
                "entry_reason": signal["reason"],
                "entered_at": at.isoformat(),
                "entry_budget": budget,
                "entry_price": dollars(price_c),
                "entry_premium_cents": premium,
                "entry_fee_cents": FEE,
                "entry_debit_cents": premium + FEE,
                "entry_option_quote": option,
                "entry_underlying_quote": spot,
                "exit_requested_at": None,
                "exit_reason": None,
            }
            self._event(db, "entry:" + sid, "ENTRY", at, signal["reason"], trade, symbol, sid)
            return trade

    @staticmethod
    def _trade(db, key):
        state = _state(LateOptionLog._all(db))
        trade = next((t for t in state["trades"] if t["trade_id"] == key), None)
        if not trade:
            raise ValueError("unknown late research position")
        return trade

    def request_exit(self, trade_id, reason, now, *, evidence=None):
        with self._db(write=True) as db:
            t = self._trade(db, trade_id)
            if t["status"] != "OPEN":
                return t
            at = self._at(now)
            if at < timestamp(t["entered_at"]):
                raise ValueError("exit request predates entry")
            t.update(
                status="EXIT_PENDING",
                exit_requested_at=at.isoformat(),
                exit_reason=_reason(reason),
                exit_request_evidence=evidence,
            )
            self._event(
                db,
                "request:" + trade_id,
                "EXIT_REQUESTED",
                at,
                reason,
                t,
                t["symbol"],
                t["signal_id"],
            )
            return t

    def exit(self, trade_id, option_quote, underlying_quote, now):
        with self._db(write=True) as db:
            t = self._trade(db, trade_id)
            if t["status"] == "CLOSED":
                return t
            if t["status"] != "EXIT_PENDING":
                raise ValueError("durably request the exit before observing a fill")
            at = self._at(now)
            if at.astimezone(ET).time() >= time(11, 35):
                raise ValueError(
                    "research exit observation deadline passed; leave position unresolved"
                )
            option = _book(
                option_quote, t["symbol"], at, timestamp(t["exit_requested_at"]), entry=False
            )
            if any(
                option[k] != t[k] for k in ("option_id", "option_type", "strike", "expiration_date")
            ):
                raise ValueError("exit quote does not match observed entry contract")
            if underlying_quote is not None:
                _spot(underlying_quote, t["symbol"], at)
            price = fill_cents(option["bid"], SLIP, entry=False)
            credit = price * 100 - FEE
            t.update(
                status="CLOSED",
                exited_at=at.isoformat(),
                exit_option_quote=option,
                exit_underlying_quote=underlying_quote,
                exit_price=dollars(price),
                exit_fee_cents=FEE,
                exit_credit_cents=credit,
                realized_pnl_cents=credit - t["entry_debit_cents"],
            )
            self._event(
                db, "exit:" + trade_id, "EXIT", at, t["exit_reason"], t, t["symbol"], t["signal_id"]
            )
            return t

    def report(self):
        events = self.events()
        state = _state(events)
        trades, signals = state["trades"], state["signals"]
        closed = [t for t in trades if t["status"] == "CLOSED"]
        unresolved = len(trades) - len(closed)
        triggered = sum(e["kind"] == "TRIGGER" for e in events)
        net = dollars(sum(t["realized_pnl_cents"] for t in closed)) if closed else None
        raw = sum(
            (float(t["exit_option_quote"]["bid"]) - float(t["entry_option_quote"]["ask"])) * 100
            for t in closed
        )
        fees = 2 * FEE * len(closed)
        spread = sum(
            (float(t[k]["ask"]) - float(t[k]["bid"])) * 50
            for t in closed
            for k in ("entry_option_quote", "exit_option_quote")
        )
        return {
            "session_date": self.day,
            "strategy": VERSION,
            "research_only": True,
            "live_eligible": False,
            "unique_signals": len(signals),
            "modeled_entries": len(trades),
            "closed": len(closed),
            "unresolved": unresolved,
            "unique_triggered": triggered,
            "triggered_without_entry": triggered - len(trades),
            "armed_without_observed_trigger": len(signals) - triggered,
            "signals_without_entry": len(signals) - len(trades),
            "open_position": state["open_position"],
            "net_closed_pnl_usd": net,
            "resolved_observations_pnl_usd": net if not unresolved else None,
            "wins": sum(t["realized_pnl_cents"] > 0 for t in closed),
            "win_rate_denominator": len(closed),
            "win_rate_pct": round(
                100 * sum(t["realized_pnl_cents"] > 0 for t in closed) / len(closed), 2
            )
            if closed
            else None,
            "closed_gross_bid_ask_pnl_usd": round(raw, 4) if closed else None,
            "closed_commissions_usd": dollars(fees),
            "closed_slippage_and_rounding_usd": round(raw - net - dollars(fees), 4)
            if closed
            else None,
            "closed_spread_vs_mid_usd": round(spread, 4) if closed else None,
            "event_counts": dict(Counter(e["kind"] for e in events)),
            "signals": signals,
            "trades": trades,
            "events": events,
            "interpretation": "Incremental late-window modeled positions only, not primary account P/L. "
            "Bid/ask already includes spread. Missing fills and unresolved exits have no imputed P/L; "
            "coverage and worker health must be evaluated separately.",
        }
