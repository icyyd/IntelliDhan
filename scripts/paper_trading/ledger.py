"""Private, dollar-denominated forward-paper ledger. No broker order interface.

Quoted prices are observations; fills below are conservative assumptions, not
executions. Money is persisted as integer cents. Account capital is initialized
once and cannot be reset by restarting a process.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
DEFAULT_POLICY = {
    "mode": "SIMULATION", "strategy": "ORB_RETEST_PAPER_V1",
    "symbols": ["SPY", "QQQ"], "max_contracts": 1, "max_entries_per_day": 2,
    "max_premium": 500.0, "max_daily_loss": 500.0,
    "fee_per_contract_side": 0.65, "slippage_per_share": 0.01,
    "max_quote_age_seconds": 10, "max_spread_fraction": 0.10,
    "minimum_delta": 0.50, "maximum_delta": 0.80,
    "minimum_volume": 100, "minimum_open_interest": 100,
    "entry_cutoff_et": "10:30", "flatten_et": "11:30",
    "settlement": "T+1 exchange session; no same-day proceeds reuse",
    "live_eligible": False,
}


def timestamp(value) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be ISO-8601 with a timezone") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return result.astimezone(timezone.utc)


def number(value, *, nonnegative=False) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric observation")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("numeric observation is missing or invalid") from exc
    if not math.isfinite(result) or (result < 0 if nonnegative else result <= 0):
        raise ValueError("numeric observation is outside its valid range")
    return result


def cents(value) -> int:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("money must be finite")
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid money amount") from exc


def dollars(value: int) -> float:
    return float(Decimal(value) / 100)


def fill_cents(price, slippage, *, entry):
    value = Decimal(str(price)) + (Decimal(str(slippage)) if entry else -Decimal(str(slippage)))
    rounding = ROUND_CEILING if entry else ROUND_FLOOR
    return int((max(Decimal("0"), value) * 100).quantize(Decimal("1"), rounding=rounding))


def session_dates(now: datetime) -> tuple[str, str]:
    """Fail closed if the installed exchange calendar cannot establish a session."""
    import exchange_calendars as xcals
    day = now.astimezone(ET).date().isoformat()
    calendar = xcals.get_calendar("XNYS")
    # exchange_calendars 4.11.x's is_session/next_session compare raw integer
    # units and are incompatible with pandas 3's microsecond DatetimeIndex.
    # Compare the calendar's explicit session labels, not raw nanoseconds.
    sessions = [item.date().isoformat() for item in calendar.sessions]
    if day not in sessions:
        raise ValueError("not a verified US exchange session")
    index = sessions.index(day)
    if index + 1 >= len(sessions):
        raise ValueError("next exchange session is outside the calendar horizon")
    following = sessions[index + 1]
    return day, following


class Ledger:
    def __init__(self, path, initial_capital=1000, policy=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("paper ledger must be a private regular file owned by this user")
        self.policy = {**DEFAULT_POLICY, **(policy or {})}
        if self.policy["mode"] != "SIMULATION" or self.policy["live_eligible"] is not False:
            raise ValueError("this ledger supports paper trading only")
        if self.policy["symbols"] != ["SPY", "QQQ"] or self.policy["max_contracts"] != 1:
            raise ValueError("paper pilot is limited to one SPY or QQQ contract")
        for key in ("max_premium", "max_daily_loss", "fee_per_contract_side", "slippage_per_share"):
            number(self.policy[key], nonnegative=key in {"fee_per_contract_side", "slippage_per_share"})
        initial = cents(number(initial_capital))
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trades(trade_id TEXT PRIMARY KEY, signal_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'audit events are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'audit events are immutable'); END;
            """)
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT value FROM metadata WHERE key='account'").fetchone()
            config = {"initial_cents": initial, "policy": self.policy}
            if prior is None:
                db.execute("INSERT INTO metadata VALUES('account', ?)", (json.dumps(config, sort_keys=True),))
            elif json.loads(prior[0]) != config:
                raise ValueError("persisted capital/policy differs; never reset an existing paper account")
        self.initial_cents = initial

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _trades(db):
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM trades ORDER BY rowid")]

    @staticmethod
    def _save(db, trade):
        db.execute("UPDATE trades SET payload=? WHERE trade_id=?", (json.dumps(trade, sort_keys=True), trade["trade_id"]))

    @staticmethod
    def _event(db, kind, reason, now, details, event_id):
        reason = str(reason).strip()
        if not reason:
            raise ValueError("an audit reason is required")
        db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)", (
            event_id, timestamp(now).isoformat(), kind, reason, json.dumps(details or {}, sort_keys=True),
        ))

    def record_event(self, kind, reason, now, details=None, event_id=None):
        at = timestamp(now).isoformat()
        key = event_id or hashlib.sha256(f"{kind}|{at}|{reason}|{json.dumps(details or {}, sort_keys=True)}".encode()).hexdigest()
        with self._db() as db:
            self._event(db, str(kind), reason, now, details, key)

    def _fresh(self, quote, now):
        at = timestamp(quote.get("observed_at"))
        age = (now - at).total_seconds()
        if not 0 <= age <= self.policy["max_quote_age_seconds"]:
            raise ValueError("quote is stale or future-dated")
        if not isinstance(quote.get("source"), str) or not quote["source"].strip():
            raise ValueError("quote source is required")
        return at

    def _underlying(self, quote, symbol, now):
        if quote.get("symbol") != symbol:
            raise ValueError("underlying quote symbol mismatch")
        self._fresh(quote, now)
        number(quote.get("price"))
        return dict(quote)

    def _option(self, quote, symbol, now, *, entry):
        if quote.get("symbol") != symbol or symbol not in self.policy["symbols"]:
            raise ValueError("option underlying symbol mismatch")
        self._fresh(quote, now)
        bid = number(quote.get("bid"), nonnegative=not entry)
        ask = number(quote.get("ask"), nonnegative=not entry)
        if ask < bid:
            raise ValueError("inverted option market")
        if not quote.get("option_id") or quote.get("option_type") not in {"call", "put"}:
            raise ValueError("option identity is missing or invalid")
        number(quote.get("strike"))
        if quote.get("expiration_date") != now.astimezone(ET).date().isoformat():
            raise ValueError("quote is not a same-day expiry observation")
        if entry:
            if (ask - bid) / ((ask + bid) / 2) > self.policy["max_spread_fraction"]:
                raise ValueError("option spread exceeds the cap")
            raw_delta = quote.get("delta")
            if isinstance(raw_delta, bool):
                raise ValueError("option delta is invalid")
            try:
                delta = float(raw_delta)
            except (TypeError, ValueError) as exc:
                raise ValueError("option delta is missing") from exc
            if not self.policy["minimum_delta"] <= abs(delta) <= self.policy["maximum_delta"]:
                raise ValueError("option delta is outside pilot range")
            if (quote["option_type"] == "call") != (delta > 0):
                raise ValueError("option delta sign mismatch")
            if number(quote.get("volume"), nonnegative=True) < self.policy["minimum_volume"]:
                raise ValueError("option volume is below pilot floor")
            if number(quote.get("open_interest"), nonnegative=True) < self.policy["minimum_open_interest"]:
                raise ValueError("option open interest is below pilot floor")
        return dict(quote)

    def _account(self, trades, now):
        today = now.astimezone(ET).date().isoformat()
        cash = settled = self.initial_cents
        realized = today_realized = today_losses = 0
        entries_today = 0
        for trade in trades:
            cash -= trade["entry_debit_cents"]
            settled -= trade["entry_debit_cents"]
            entries_today += trade["session_date"] == today
            if trade["status"] == "CLOSED":
                cash += trade["exit_credit_cents"]
                if trade["settles_on"] <= today or trade["exit_credit_cents"] < 0:
                    settled += trade["exit_credit_cents"]
                realized += trade["realized_pnl_cents"]
                if timestamp(trade["exited_at"]).astimezone(ET).date().isoformat() == today:
                    today_realized += trade["realized_pnl_cents"]
                    today_losses += min(0, trade["realized_pnl_cents"])
        return {"cash_cents": cash, "settled_cents": settled, "realized_cents": realized,
                "today_realized_cents": today_realized, "today_losses_cents": today_losses,
                "entries_today": entries_today}

    def enter(self, signal, option_quote, underlying_quote, now):
        now = timestamp(now)
        today, _ = session_dates(now)
        local_time = now.astimezone(ET).time()
        if not time(9, 45) <= local_time < time(10, 30):
            raise ValueError("outside the frozen paper entry window")
        symbol = signal.get("symbol")
        if not signal.get("signal_id") or not signal.get("reason"):
            raise ValueError("signal identity and entry reasoning are required")
        if not timestamp(signal.get("created_at")) <= now < timestamp(signal.get("expires_at")):
            raise ValueError("signal is expired or future-dated")
        direction = signal.get("direction")
        if direction not in {"CALL", "PUT"}:
            raise ValueError("signal direction must be CALL or PUT")
        trigger, stop, target = [number(signal.get(name)) for name in ("trigger", "stop", "target")]
        if not (stop < trigger < target if direction == "CALL" else target < trigger < stop):
            raise ValueError("signal stop/target geometry is invalid")
        underlying = self._underlying(underlying_quote, symbol, now)
        option = self._option(option_quote, symbol, now, entry=True)
        if timestamp(underlying["observed_at"]) < timestamp(signal["created_at"]):
            raise ValueError("underlying quote predates the confirmed signal")
        if option["option_type"].upper() != direction:
            raise ValueError("signal and option direction disagree")
        price = float(underlying["price"])
        if not (trigger < price < target if direction == "CALL" else target < price < trigger):
            raise ValueError("underlying has not triggered, or has already reached target")
        premium = fill_cents(option["ask"], self.policy["slippage_per_share"], entry=True) * 100
        fee = cents(self.policy["fee_per_contract_side"])
        debit = premium + fee
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            trades = self._trades(db)
            duplicate = next((t for t in trades if t["signal_id"] == signal["signal_id"]), None)
            if duplicate:
                return duplicate
            if db.execute("SELECT 1 FROM events WHERE event_id=?", (signal["signal_id"] + ":invalidated",)).fetchone():
                raise ValueError("setup was invalidated before entry; rebound cannot revive it")
            if any(t["status"] != "CLOSED" for t in trades):
                raise ValueError("an open or unresolved paper position already exists")
            account = self._account(trades, now)
            if account["entries_today"] >= self.policy["max_entries_per_day"]:
                raise ValueError("daily entry limit reached")
            if premium > cents(self.policy["max_premium"]):
                raise ValueError("contract premium exceeds the paper cap")
            # Gains do not increase the frozen daily loss budget.
            remaining_risk = cents(self.policy["max_daily_loss"]) + account["today_losses_cents"]
            if debit + fee > remaining_risk:
                raise ValueError("full premium plus fees exceeds remaining daily loss budget")
            if debit + fee > account["settled_cents"]:
                raise ValueError("insufficient settled paper cash; same-day proceeds cannot be reused")
            trade_id = "paper_" + hashlib.sha256(str(signal["signal_id"]).encode()).hexdigest()[:24]
            trade = {"trade_id": trade_id, "signal_id": signal["signal_id"], "symbol": symbol,
                     "direction": direction, "status": "OPEN", "simulated": True,
                     "strategy": self.policy["strategy"], "session_date": today, "quantity": 1,
                     "option_id": option["option_id"], "option_type": option["option_type"],
                     "strike": option["strike"], "expiration_date": option["expiration_date"],
                     "entered_at": now.isoformat(), "entry_reason": signal["reason"],
                     "entry_option_quote": option, "entry_underlying_quote": underlying,
                     "entry_price": dollars(premium // 100), "entry_premium_cents": premium,
                     "entry_fee_cents": fee, "entry_debit_cents": debit,
                     "trigger": trigger, "stop": stop, "target": target,
                     "evidence": signal.get("evidence") or {}, "mark": None,
                     "exit_requested_at": None, "exit_reason": None}
            db.execute("INSERT INTO trades VALUES(?,?,?)", (trade_id, signal["signal_id"], json.dumps(trade, sort_keys=True)))
            self._event(db, "ENTRY", signal["reason"], now, trade, trade_id + ":entry")
            return trade

    def request_exit(self, trade_id, reason, now):
        now = timestamp(now)
        if not str(reason).strip():
            raise ValueError("exit reasoning is required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            trade = next((t for t in self._trades(db) if t["trade_id"] == trade_id), None)
            if trade is None:
                raise ValueError("paper position not found")
            if trade["status"] != "OPEN":
                return trade
            if now < timestamp(trade["entered_at"]):
                raise ValueError("exit request predates entry")
            trade.update(status="EXIT_PENDING", exit_requested_at=now.isoformat(), exit_reason=str(reason))
            self._save(db, trade)
            self._event(db, "EXIT_REQUESTED", reason, now, {"trade_id": trade_id}, trade_id + ":exit-request")
            return trade

    def exit(self, trade_id, option_quote, underlying_quote, now):
        now = timestamp(now)
        today, settles = session_dates(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            trade = next((t for t in self._trades(db) if t["trade_id"] == trade_id), None)
            if trade is None:
                raise ValueError("paper position not found")
            if trade["status"] == "CLOSED":
                return trade
            if trade["status"] != "EXIT_PENDING":
                raise ValueError("record the exit decision before filling its observation")
            if today != trade["session_date"] or now < timestamp(trade["exit_requested_at"]):
                raise ValueError("cannot fabricate an expired or retroactive option exit")
            option = self._option(option_quote, trade["symbol"], now, entry=False)
            if timestamp(option["observed_at"]) < timestamp(trade["exit_requested_at"]):
                raise ValueError("exit quote predates the exit decision")
            if any(option[name] != trade[name] for name in ("option_id", "option_type", "expiration_date", "strike")):
                raise ValueError("exit quote does not match the held contract")
            if underlying_quote is not None:
                self._underlying(underlying_quote, trade["symbol"], now)
            premium = fill_cents(option["bid"], self.policy["slippage_per_share"], entry=False) * 100
            fee = cents(self.policy["fee_per_contract_side"])
            credit = premium - fee
            trade.update(status="CLOSED", exited_at=now.isoformat(), exit_option_quote=option,
                         exit_underlying_quote=underlying_quote, exit_price=dollars(premium // 100),
                         exit_fee_cents=fee, exit_credit_cents=credit,
                         realized_pnl_cents=credit - trade["entry_debit_cents"], settles_on=settles)
            self._save(db, trade)
            self._event(db, "EXIT", trade["exit_reason"], now, trade, trade_id + ":exit")
            return trade

    def mark(self, option_quote, now):
        now = timestamp(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            open_trade = next((t for t in self._trades(db) if t["status"] != "CLOSED"), None)
            if open_trade is None:
                return
            option = self._option(option_quote, open_trade["symbol"], now, entry=False)
            if any(option[name] != open_trade[name] for name in ("option_id", "option_type", "expiration_date", "strike")):
                raise ValueError("mark quote does not match the held contract")
            open_trade["mark"] = option
            self._save(db, open_trade)

    def snapshot(self, now, *, full_history=False):
        now = timestamp(now)
        with self._db() as db:
            db.execute("BEGIN")
            trades = self._trades(db)
            events = [{"event_id": r["event_id"], "recorded_at": r["recorded_at"], "kind": r["kind"],
                       "reason": r["reason"], "details": json.loads(r["payload"])}
                      for r in db.execute("SELECT * FROM events ORDER BY rowid DESC" + ("" if full_history else " LIMIT 200"))]
        account = self._account(trades, now)
        open_trade = next((t for t in trades if t["status"] != "CLOSED"), None)
        liquidation = unrealized = 0 if open_trade is None else None
        if open_trade and open_trade.get("mark"):
            try:
                quote = self._option(open_trade["mark"], open_trade["symbol"], now, entry=False)
                liquidation = fill_cents(quote["bid"], self.policy["slippage_per_share"], entry=False) * 100 - cents(self.policy["fee_per_contract_side"])
                unrealized = liquidation - open_trade["entry_debit_cents"]
            except ValueError:
                pass
        public_account = {
            "initial_capital": dollars(self.initial_cents), "cash": dollars(account["cash_cents"]),
            "settled_cash": dollars(account["settled_cents"]),
            "unsettled_cash": dollars(account["cash_cents"] - account["settled_cents"]),
            "equity": dollars(account["cash_cents"] + liquidation) if liquidation is not None else None,
            "realized_pnl": dollars(account["realized_cents"]),
            "today_realized_pnl": dollars(account["today_realized_cents"]),
            "unrealized_pnl": dollars(unrealized) if unrealized is not None else None,
            "today_pnl": dollars(account["today_realized_cents"] + unrealized) if unrealized is not None else None,
            "entries_today": account["entries_today"], "valuation_status": "CURRENT" if liquidation is not None else "UNRESOLVED_QUOTE",
            "currency": "USD", "simulated": True,
        }
        for trade in trades:
            trade["entry_debit"] = dollars(trade["entry_debit_cents"])
            trade["realized_pnl"] = dollars(trade["realized_pnl_cents"]) if "realized_pnl_cents" in trade else None
        return {"account": public_account, "open_position": open_trade, "trades": list(reversed(trades)),
                "events": events, "policy": self.policy, "as_of": now.isoformat()}
