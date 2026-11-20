"""Date-fenced paper observer; all decisions are deterministic and journaled."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .ledger import Ledger
from .strategy import evaluate_sniper, exit_reason

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def session_bounds(day: str) -> tuple[datetime, datetime]:
    """Never substitute weekday checks for the exchange calendar."""
    import exchange_calendars as xcals

    calendar = xcals.get_calendar("XNYS")
    if day not in calendar.schedule.index:
        raise ValueError("The requested date is not an exchange trading session")
    schedule = calendar.schedule.loc[day]
    return (
        schedule["open"].to_pydatetime(),
        schedule["close"].to_pydatetime(),
    )


def atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError("Runtime file must not be a symlink")
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, allow_nan=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


class PaperRunner:
    """One session, one virtual account, and no route to real orders."""

    def __init__(self, data_dir: Path, session_date: str, feed, *, clock=utcnow):
        self.data_dir = data_dir
        self.session_date = date.fromisoformat(session_date).isoformat()
        self.open_at, self.close_at = session_bounds(self.session_date)
        self.feed = feed
        self.clock = clock
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.connected = False
        self.ledger = Ledger(data_dir / "journal.sqlite", initial_capital=1000)
        self.agent = {
            "status": "STARTING", "reason": "Checking the paper account and data feed.",
            "heartbeat_at": None, "feed_status": "NOT_CHECKED",
        }
        self.watchlist: list[dict] = []
        self.last_event: dict[str, str] = {}
        self.last_connect_attempt: datetime | None = None
        self.last_preflight: datetime | None = None
        preflight = data_dir / "preflight.json"
        if preflight.exists() and not preflight.is_symlink():
            diagnostic = json.loads(preflight.read_text())
            for symbol in ("SPY", "QQQ"):
                check = diagnostic.get("checks", {}).get(symbol, {})
                reason = check.get("reason") or check.get("scan", {}).get("reason")
                if reason:
                    self.ledger.record_event("PREFLIGHT", str(reason), self.clock(),
                                             details={"symbol": symbol, "diagnostic_at": diagnostic.get("observed_at")},
                                             event_id=f"preflight:{diagnostic.get('observed_at')}:{symbol}")

    def event(self, kind: str, reason: str, now: datetime, details=None) -> None:
        # Repeated unchanged waiting states are not a new trading observation.
        key = kind + ":" + str((details or {}).get("symbol", ""))
        if self.last_event.get(key) != reason:
            self.ledger.record_event(kind, reason, now, details=details)
            self.last_event[key] = reason

    def update(self, status: str, reason: str, now: datetime, feed_status=None) -> None:
        with self.lock:
            self.agent.update(status=status, reason=reason, heartbeat_at=now.isoformat())
            if feed_status is not None:
                self.agent["feed_status"] = feed_status
            atomic_json(self.data_dir / "heartbeat.json", self.agent)

    def _observe_invalidation(self, symbol, decision, now):
        identity = decision.get("invalidated_signal_id")
        if identity:
            self.ledger.record_event("SIGNAL_INVALIDATED", decision["reason"], now,
                                     details={"symbol": symbol, "signal_id": identity},
                                     event_id=identity + ":invalidated")

    def snapshot(self, *, full_history=False) -> dict:
        now = self.clock()
        result = self.ledger.snapshot(now, full_history=full_history)
        with self.lock:
            agent = dict(self.agent)
            watchlist = list(self.watchlist)
        heartbeat = agent.get("heartbeat_at")
        if heartbeat and (now - datetime.fromisoformat(heartbeat)).total_seconds() > 30:
            agent["status"] = "STALE"
            agent["reason"] = "The observer heartbeat is overdue. Do not treat old data as current."
        return {
            **result, "mode": "SIMULATION", "session_date": self.session_date,
            "now": now.isoformat(), "agent": agent, "watchlist": watchlist,
            "validation": "RESEARCH_ONLY",
            "strategy": "15-minute opening-range break, retest and fresh-reset re-entry",
            "schedule": {
                "timezone": "America/New_York", "open": self.open_at.isoformat(),
                "close": self.close_at.isoformat(), "entry_start": "09:45",
                "entry_end": "10:30", "flatten_by": "11:30",
            },
            "limitations": [
                "Paper observations are not broker fills or evidence of a profitable strategy.",
                "A green day is an objective, not a promise; no trade is a valid result.",
                "The feed must provide fresh timestamps. Missing exit quotes remain unresolved.",
                "This local process requires the Mac to remain awake and OpenD to stay signed in.",
            ],
        }

    def _connect(self, now: datetime) -> bool:
        if self.connected:
            return True
        if self.last_connect_attempt and (now - self.last_connect_attempt).total_seconds() < 30:
            return False
        self.last_connect_attempt = now
        try:
            self.feed.connect()
            health = self.feed.health()
            if health.get("status") != "CONNECTED" or health.get("quote_logged_in") is not True:
                raise ValueError("OpenD quote login is unavailable")
            self.connected = True
            self.event("FEED", "OpenD quote connection established; entry freshness is checked separately.", now,
                       details={"health": health})
            return True
        except Exception as exc:
            self.update("DATA_BLOCKED", self.safe_error(exc), now, "UNAVAILABLE")
            self.event("DATA_BLOCKED", self.safe_error(exc), now)
            try:
                self.feed.close()
            except Exception:
                pass
            return False

    @staticmethod
    def safe_error(exc: Exception) -> str:
        # Only our adapter/validation errors are permitted to carry details.
        if exc.__class__.__module__.startswith("paper_trading"):
            return str(exc)[:400]
        return f"Paper observer check failed ({type(exc).__name__}); inspect the local diagnostics."

    def _manage_position(self, position: dict, now: datetime) -> None:
        symbol = position["symbol"]
        underlying = None
        bars = []
        try:
            underlying = self.feed.underlying(symbol)
            bars = self.feed.bars(symbol, self.clock())
            now = self.clock()
            reason = exit_reason(position, bars, underlying, now)
        except Exception as exc:
            reason = "Data-quality protection: " + self.safe_error(exc)
        # A prior exit intent survives process restarts and takes precedence.
        reason = position.get("exit_reason") if position.get("status") == "EXIT_PENDING" else reason
        if now.astimezone(ET).date().isoformat() != self.session_date:
            reason = reason or "Session ended; no new exposure is permitted."
        if reason:
            self.ledger.request_exit(position["trade_id"], reason, now)
        try:
            quote = self.feed.option_quote(position)
            now = self.clock()
            self.ledger.mark(quote, now)
            if reason:
                # A missing underlying observation must not suppress a fresh
                # option-side exit. The durable reason explains the data failure.
                try:
                    age = (now - datetime.fromisoformat(underlying["observed_at"])).total_seconds()
                    usable = (underlying["symbol"] == symbol and 0 <= age <= 10
                              and isinstance(underlying.get("source"), str) and bool(underlying["source"].strip())
                              and not isinstance(underlying["price"], bool)
                              and math.isfinite(float(underlying["price"])) and float(underlying["price"]) > 0)
                except (TypeError, ValueError, KeyError):
                    usable = False
                if not usable:
                    self.event("EXIT_DATA_GAP", "Underlying observation unavailable for exit; using a fresh option bid only.", now,
                               {"symbol": symbol, "last_underlying_observation": underlying})
                    underlying = None
                self.ledger.exit(position["trade_id"], quote, underlying, now)
                self.event("EXIT", reason, now, {"symbol": symbol})
                self.update("WATCHING", "The paper position is closed; risk checks remain active.", now, "OBSERVED")
            else:
                self.update("IN_POSITION", "Watching the option bid, underlying stop, target, and time limit.", now, "OBSERVED")
        except Exception as exc:
            pending = reason or "Fresh option quote unavailable: " + self.safe_error(exc)
            self.ledger.request_exit(position["trade_id"], pending, now)
            self.update("EXIT_PENDING", "Exit requested, but a valid observed quote is missing. P/L is not finalized.", now, "BLOCKED")
            self.event("EXIT_PENDING", pending, now, {"symbol": symbol})

    def step(self) -> None:
        now = self.clock()
        local = now.astimezone(ET)
        snapshot = self.ledger.snapshot(now)
        position = snapshot.get("open_position")
        if not self._connect(now):
            if position:
                self.ledger.request_exit(position["trade_id"], "OpenD unavailable; exit requires a real quote.", now)
            return
        if position:
            self._manage_position(position, now)
            return
        if local.date().isoformat() < self.session_date:
            self.update("SCHEDULED", f"Waiting for {self.session_date}, 9:45 a.m. ET. No entries before the opening range closes.", now, "CONNECTED_NOT_VALIDATED")
            return
        if local.date().isoformat() > self.session_date or local.time() >= time(10, 30):
            self.update("SESSION_COMPLETE", "Entry window closed. The ledger is preserved; no automatic reset or next-day trading.", now, "IDLE")
            return
        if local.time() < time(9, 45):
            if local.time() >= time(9, 20) and (
                self.last_preflight is None or (now - self.last_preflight).total_seconds() >= 60
            ):
                self.last_preflight = now
                checks = []
                for symbol in ("SPY", "QQQ"):
                    try:
                        underlying = self.feed.underlying(symbol)
                        options = self.feed.candidates(symbol, "CALL", self.session_date, underlying["price"])
                        reason = "Quote preflight passed; entry checks still apply." if options else "No verified option quotes yet; entries remain blocked."
                        checks.append({"symbol": symbol, "status": "WAIT" if options else "DATA_BLOCKED", "reason": reason})
                        self.event("PREFLIGHT", reason, self.clock(), {"symbol": symbol, "scan": getattr(self.feed, "last_scan", {})})
                    except Exception as exc:
                        checks.append({"symbol": symbol, "status": "DATA_BLOCKED", "reason": self.safe_error(exc)})
                        self.event("PREFLIGHT", self.safe_error(exc), self.clock(), {"symbol": symbol})
                with self.lock:
                    self.watchlist = checks
            self.update("WAITING_FOR_RANGE", "Waiting for the full 9:30–9:45 a.m. opening range; not taking an early guess.", now, "CONNECTED_NOT_VALIDATED")
            return
        watchlist = []
        for symbol in ("SPY", "QQQ"):
            try:
                underlying = self.feed.underlying(symbol)
                bars = self.feed.bars(symbol, self.clock())
                now = self.clock()
                prior_exits = [datetime.fromisoformat(t["exited_at"]) for t in snapshot["trades"]
                               if t["status"] == "CLOSED" and t["session_date"] == self.session_date]
                invalidated = {event["details"].get("signal_id") for event in snapshot["events"]
                               if event["kind"] == "SIGNAL_INVALIDATED"}
                context = {"last_exit_at": max(prior_exits, default=None), "invalidated_signal_ids": invalidated}
                decision = evaluate_sniper(symbol, bars, underlying, now, **context)
                watchlist.append({"symbol": symbol, "price": underlying["price"],
                                  "observed_at": underlying["observed_at"],
                                  "status": decision["status"], "reason": decision["reason"]})
                signal = decision.get("signal")
                self.event("SIGNAL_CHECK", decision["reason"], now, {"symbol": symbol, "evidence": decision.get("evidence")})
                self._observe_invalidation(symbol, decision, now)
                if signal is None or decision["status"] != "ENTRY":
                    continue
                if any(trade["signal_id"] == signal["signal_id"] for trade in snapshot["trades"]):
                    self.event("SKIPPED", "This setup has already been traded; duplicate entry prevented.", now, {"symbol": symbol})
                    continue
                candidates = self.feed.candidates(symbol, signal["direction"], self.session_date, underlying["price"])
                # Higher absolute delta first, then tighter spread. Every attempted
                # candidate is still subjected to the ledger's authoritative gates.
                candidates.sort(key=lambda q: (-abs(q["delta"]), q["ask"] - q["bid"]))
                rejection = "No verified, fresh, liquid option fits the policy."
                for quote in candidates:
                    try:
                        # Fetch underlying again after a chain scan; never reuse a stale trigger.
                        current_option = self.feed.option_quote(quote)
                        current_underlying = self.feed.underlying(symbol)
                        now = self.clock()
                        refreshed = evaluate_sniper(symbol, bars, current_underlying, now, **context)
                        self._observe_invalidation(symbol, refreshed, now)
                        if (refreshed["status"] != "ENTRY" or
                                refreshed["signal"]["signal_id"] != signal["signal_id"]):
                            rejection = refreshed["reason"]
                            break  # back off until the next poll; an invalidated ID stays consumed
                        entered = self.ledger.enter(signal, current_option, current_underlying, now)
                        if entered["status"] == "CLOSED":
                            rejection = "This setup has already closed; no duplicate entry."
                            break
                        self.update("IN_POSITION", signal["reason"], self.clock(), "OBSERVED")
                        with self.lock:
                            self.watchlist = watchlist
                        return
                    except ValueError as exc:
                        rejection = str(exc)[:400]
                self.event("SKIPPED", rejection, now, {"symbol": symbol, "scan": getattr(self.feed, "last_scan", {})})
                watchlist[-1].update(status="SKIPPED", reason=rejection)
            except Exception as exc:
                reason = self.safe_error(exc)
                watchlist.append({"symbol": symbol, "status": "DATA_BLOCKED", "reason": reason})
                self.event("DATA_BLOCKED", reason, now, {"symbol": symbol})
        with self.lock:
            self.watchlist = watchlist
        blocked = any(row["status"] in {"DATA_BLOCKED", "BLOCKED"} for row in watchlist)
        self.update("DATA_BLOCKED" if blocked else "WATCHING",
                    "Data validation is blocking one or more symbols." if blocked else "Watching for a qualified break-and-retest; no trade is required.",
                    now, "BLOCKED" if blocked else "OBSERVED")

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    self.step()
                except Exception as exc:
                    now = self.clock()
                    self.update("ERROR", self.safe_error(exc), now, "BLOCKED")
                    self.event("ERROR", self.safe_error(exc), now)
                self.stop_event.wait(5)
        finally:
            self.feed.close()
