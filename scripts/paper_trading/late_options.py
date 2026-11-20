"""Prospective research observer for the incremental 10:30–11:15 ET window.

No broker interface, primary ledger writer, historical option-price proxy or
backfill. Each poll uses only the supplied read-only feed and research journal.
"""

from __future__ import annotations

from datetime import time, timedelta
import hashlib

from orb_cutoff_study import CONTROL_END, EXTENDED_END, historical_1115

from .late_option_log import VERSION, _book, _spot
from .ledger import DEFAULT_POLICY, ET, cents, fill_cents, number, timestamp
from .strategy import SNIPER_VERSION, exit_reason

EXIT_DEADLINE = time(11, 35)
SYMBOLS = ("SPY", "QQQ")
PROVIDER_ERRORS = (ValueError, RuntimeError, OSError, KeyError, TypeError)
IDENTITY = ("symbol", "option_id", "option_type", "strike", "expiration_date")


class ObservationError(ValueError):
    """Safe evidence error: reason contains no raw transport data."""


class PartialEvidence(ObservationError):
    """We observed a setup but part of the contemporaneous evidence was missing."""


def _active(day, now, start, end):
    local = timestamp(now).astimezone(ET)
    return local.date().isoformat() == day and start <= local.time() < end


def _tag_signal(base):
    parent = base["signal_id"]
    identity = hashlib.sha256(f"{VERSION}|{parent}".encode()).hexdigest()
    return {
        **base,
        "signal_id": identity,
        "research_only": True,
        "live_eligible": False,
        "evidence": {
            **base["evidence"],
            "strategy": VERSION,
            "technical_strategy": SNIPER_VERSION,
            "parent_signal_id": parent,
            "research_only": True,
            "live_eligible": False,
        },
    }


def _underlying(quote, symbol, now):
    return _spot(quote, symbol, timestamp(now))


def shared_quote(quote, symbol, direction, day, now, *, entry):
    """Require two real, timestamped book sides; no NBBO claim or model estimate."""
    now = timestamp(now)
    if (
        not isinstance(quote, dict)
        or quote.get("option_type") != direction.lower()
        or quote.get("expiration_date") != day
    ):
        raise ObservationError("Unverified standard contract, type or expiration.")
    return _book(quote, symbol, now, now - timedelta(seconds=DEFAULT_POLICY["max_quote_age_seconds"]), entry=entry)


def trigger_state(signal, quote, now, day):
    """Validate CURRENT underlying against the frozen setup; boundaries are terminal."""
    now = timestamp(now)
    created = timestamp(signal["created_at"])
    if (
        not _active(day, created, CONTROL_END, EXTENDED_END)
        or not _active(day, now, CONTROL_END, EXTENDED_END)
        or not created <= now < timestamp(signal["expires_at"])
    ):
        return "EXPIRED"
    q = _underlying(quote, signal["symbol"], now)
    if timestamp(q["observed_at"]) < created:
        raise ObservationError("Underlying observation predates the completed retest.")
    price = number(q["price"])
    call = signal["direction"] == "CALL"
    if price <= signal["stop"] if call else price >= signal["stop"]:
        return "STOP_BEFORE_ENTRY"
    if price >= signal["target"] if call else price <= signal["target"]:
        return "TARGET_BEFORE_ENTRY"
    return "TRIGGERED" if (price > signal["trigger"] if call else price < signal["trigger"]) else "ARMED"


def _trigger(journal, signal, quote, now, day):
    try:
        result = trigger_state(signal, quote, now, day)
    except (ValueError, TypeError, KeyError):
        journal.reject(signal["signal_id"], "Missing, stale or unverified underlying at trigger check.", now)
        raise PartialEvidence("Setup observed, but the trigger price could not be verified.") from None
    if result == "TRIGGERED":
        journal.trigger(signal["signal_id"], quote, now)
    elif result in {"STOP_BEFORE_ENTRY", "TARGET_BEFORE_ENTRY"}:
        journal.invalidate(signal["signal_id"], result, now)
    elif result == "EXPIRED":
        journal.reject(signal["signal_id"], "Setup expired; no late or retrospective entry.", now, final=True)
    return result == "TRIGGERED"


def _rank(raw, signal, day, at, max_premium):
    rows = []
    ids = set()
    for candidate in raw:
        try:
            q = shared_quote(candidate, signal["symbol"], signal["direction"], day, at, entry=True)
            if timestamp(q["observed_at"]) < timestamp(signal["created_at"]):
                continue
            if fill_cents(q["ask"], DEFAULT_POLICY["slippage_per_share"], entry=True) * 100 > cents(max_premium):
                continue
        except (ValueError, KeyError, TypeError):
            continue
        if q["option_id"] in ids:
            raise ObservationError("Duplicate contract in the observed selection universe.")
        ids.add(q["option_id"])
        rows.append(q)
    return sorted(rows, key=lambda q: (-abs(q["delta"]), q["ask"] - q["bid"], q["option_id"]))


def _scan(day, symbol, feed, source, journal, clock, gate, consumed):
    if not _active(day, clock(), CONTROL_END, EXTENDED_END):
        return "WAITING"
    bars = feed.bars(symbol, clock())
    now = clock()
    decision = historical_1115(symbol, bars, None, now, last_exit_at=gate["last_exit_at"])
    if decision["status"] == "BLOCKED":
        raise ObservationError("Completed-bar coverage or indicator validation blocked this symbol.")
    if not _active(day, now, CONTROL_END, EXTENDED_END):
        return "WAITING"
    if not decision["signal"]:
        at = timestamp(now)
        bucket = at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0)
        journal.note("SIGNAL_CHECK", decision["reason"], at, symbol=symbol, key=bucket.isoformat())
        return "OBSERVING"
    signal = _tag_signal(decision["signal"])
    ident = signal["signal_id"]
    if ident in consumed or not _active(day, signal["created_at"], CONTROL_END, EXTENDED_END):
        return "OBSERVING"
    journal.signal(signal, now)
    try:
        q = feed.underlying(symbol)
    except PROVIDER_ERRORS:
        journal.reject(ident, "Underlying unavailable for the armed trigger.", clock())
        return "PARTIAL"
    if not _trigger(journal, signal, q, clock(), day):
        return "OBSERVING"
    rejection = "No verified liquid contract within the observed scan and remaining budget."
    coverage = "OBSERVING"
    try:
        candidates = feed.candidates(symbol, signal["direction"], day, q["price"], max_premium=gate["max_premium"])
        ranked = _rank(candidates, signal, day, clock(), gate["max_premium"])
        scan = getattr(feed, "last_scan", None)
        if (
            not candidates
            and isinstance(scan, dict)
            and scan.get("symbol") == symbol
            and scan.get("direction") == signal["direction"]
            and scan.get("metadata_eligible_contracts", 0) > 0
            and scan.get("quoted_contracts") == 0
        ):
            coverage = "PARTIAL"
            rejection = (
                "Eligible contracts found, but no book capacity was available."
                if scan.get("book_requested_contracts") == 0
                else "Eligible contracts found, but no verified two-sided book was received."
            )
    except PROVIDER_ERRORS:
        ranked = []
        coverage = "PARTIAL"
        rejection = "Contract scan unavailable or inconsistent after the observed trigger."
    for selected in ranked:
        now = clock()
        if not _active(day, now, CONTROL_END, EXTENDED_END) or timestamp(now) >= timestamp(signal["expires_at"]):
            journal.reject(ident, "Setup expired during the contract scan.", now, final=True)
            return coverage
        try:
            opt = feed.option_quote(selected)
        except PROVIDER_ERRORS:
            rejection = "Selected contract could not be re-quoted."
            coverage = "PARTIAL"
            continue
        # Always act on an observed stop/no-chase boundary, including while
        # scanning a contract that ultimately fails its own liquidity checks.
        try:
            under = feed.underlying(symbol)
        except PROVIDER_ERRORS:
            journal.reject(ident, "Fresh underlying unavailable after option selection.", clock())
            return "PARTIAL"
        if not _trigger(journal, signal, under, clock(), day):
            return coverage
        try:
            baseline = source()
        except PROVIDER_ERRORS:
            journal.reject(ident, "Primary account snapshot unavailable at entry recheck.", clock())
            return "PARTIAL"
        now = clock()
        if not _trigger(journal, signal, under, now, day):
            return coverage
        try:
            shared_quote(opt, symbol, signal["direction"], day, now, entry=True)
            if any(opt[k] != selected[k] for k in IDENTITY) or timestamp(opt["observed_at"]) < timestamp(
                signal["created_at"]
            ):
                raise ObservationError("Refreshed contract identity or timestamp does not match the selection.")
        except (ValueError, KeyError, TypeError):
            rejection = "Re-quoted contract no longer has eligible identity, liquidity or timestamps."
            continue
        try:
            journal.enter(signal, opt, under, now, baseline)
        except ValueError as exc:
            rejection = str(exc)  # journal policy errors contain only locally authored reasons
            continue
        return "OPEN"
    journal.reject(ident, rejection, clock())
    now = clock()
    if _active(day, now, CONTROL_END, EXTENDED_END) and timestamp(now) < timestamp(signal["expires_at"]):
        try:
            after = feed.underlying(symbol)
            _trigger(journal, signal, after, clock(), day)
        except PROVIDER_ERRORS:
            coverage = "PARTIAL"
            journal.reject(ident, "Fresh underlying unavailable after the unfilled scan.", clock())
    return coverage


def _manage(day, pos, feed, journal, clock):
    tid, symbol = pos["trade_id"], pos["symbol"]
    now = clock()
    if not _active(day, now, CONTROL_END, EXIT_DEADLINE):
        return "EXIT_PENDING"  # no next-session, terminal, or retroactive fills
    under, bars = None, []
    reason = pos.get("exit_reason") if pos.get("status") == "EXIT_PENDING" else None
    if not reason:
        try:
            raw = feed.underlying(symbol)
            under = _underlying(raw, symbol, clock())
            if timestamp(clock()).astimezone(ET).time() < time(11, 30):
                bars = feed.bars(symbol, clock())
            now = clock()
            reason = exit_reason(pos, bars, under, now)
        except PROVIDER_ERRORS:
            now = clock()
            reason = "Underlying or bar evidence unavailable; request a protective exit."
        if reason:
            ev = {"underlying_quote": under, "decision_at": timestamp(now).isoformat()}
            if bars:
                try:
                    last = bars[-1]
                    started = timestamp(last["started_at"])
                    if started + timedelta(minutes=5) <= timestamp(now):
                        ev["last_completed_bar"] = {
                            **{k: number(last[k]) for k in ("high", "low", "close")},
                            "started_at": started.isoformat(),
                        }
                except (KeyError, TypeError, ValueError, OverflowError):
                    pass  # malformed optional evidence must not suppress the protective exit
            journal.request_exit(tid, reason, now, evidence=ev)
    try:
        raw = feed.option_quote(pos)
        now = clock()
        if not _active(day, now, CONTROL_END, EXIT_DEADLINE):
            raise ObservationError("Option observation arrived beyond the research collection deadline.")
        opt = shared_quote(raw, symbol, pos["direction"], day, now, entry=False)
        if any(pos[k] != opt[k] for k in IDENTITY):
            raise ObservationError("Exit book does not belong to the held contract.")
    except PROVIDER_ERRORS:
        if not reason:
            journal.request_exit(tid, "Held contract has no verified two-sided current book.", clock())
        journal.note(
            "DATA_BLOCKED",
            "Missing or invalid held-contract quote; outcome not assumed.",
            clock(),
            symbol=symbol,
            key=tid + ":exit-book",
        )
        return "EXIT_PENDING"
    if not reason and timestamp(now).astimezone(ET).time() >= time(11, 30):
        reason = "11:30 ET paper-session flatten cutoff."
        journal.request_exit(tid, reason, now)
    if not reason:
        return "OPEN"
    try:
        usable = _underlying(under, symbol, now) if under else None
    except PROVIDER_ERRORS:
        usable = None
    try:
        journal.exit(tid, opt, usable, now)
    except ValueError:
        journal.note(
            "DATA_BLOCKED",
            "No admissible held-contract quote after the recorded exit decision.",
            now,
            symbol=symbol,
            key=tid + ":exit-after-request",
        )
        return "EXIT_PENDING"
    return "CLOSED"


def observe_step(day, *, feed, source, journal, clock):
    """One wall-clock poll. Source and journal are deliberately distinct objects."""
    state = journal.state()
    if state["open_position"]:
        return _manage(day, state["open_position"], feed, journal, clock)
    if not _active(day, clock(), CONTROL_END, EXTENDED_END):
        return "WAITING"
    try:
        baseline = source()
    except PROVIDER_ERRORS:
        journal.note("DATA_BLOCKED", "Primary research-account snapshot unavailable.", clock())
        return "BLOCKED"
    try:
        gate = journal.gate(baseline, clock())
    except ValueError as exc:
        journal.note("DATA_BLOCKED", str(exc), clock())
        return "BLOCKED"
    statuses = []
    for symbol in SYMBOLS:
        try:
            status = _scan(day, symbol, feed, source, journal, clock, gate, state["invalidated_signal_ids"])
            if status == "OPEN":
                return "OPEN"
            statuses.append(status)
        except PROVIDER_ERRORS as exc:
            # SDK/transport text may include private diagnostics. Never persist it.
            reason = (
                str(exc)
                if isinstance(exc, ObservationError)
                else f"Quote or setup unavailable ({type(exc).__name__})."
            )
            journal.note("DATA_BLOCKED", reason, clock(), symbol=symbol)
            statuses.append("PARTIAL" if isinstance(exc, PartialEvidence) else "BLOCKED")
    return statuses[0] if len(set(statuses)) == 1 else "PARTIAL"
