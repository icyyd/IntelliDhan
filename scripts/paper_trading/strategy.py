"""Frozen ORB/retest paper experiment. No calibrated probability or order tools."""
from __future__ import annotations

from datetime import datetime, time, timedelta
import hashlib

from .ledger import ET, number, session_dates, timestamp


def _bars(raw, now):
    bars = []
    seen = set()
    for row in raw:
        at = timestamp(row.get("started_at"))
        if at in seen:
            raise ValueError("duplicate five-minute candle")
        seen.add(at)
        if at.second or at.microsecond or at.minute % 5:
            raise ValueError("candle is not aligned to five minutes")
        if at + timedelta(minutes=5) > now:
            continue  # live/incomplete bars cannot establish a thesis
        local = at.astimezone(ET)
        if not time(9, 30) <= local.time() < time(16):
            continue  # regular-hours-only warmup
        clean = {name: number(row.get(name), nonnegative=name == "volume") for name in ("open", "high", "low", "close", "volume")}
        if not clean["low"] <= min(clean["open"], clean["close"]) <= max(clean["open"], clean["close"]) <= clean["high"]:
            raise ValueError("invalid candle OHLC range")
        bars.append({**clean, "at": at})
    bars.sort(key=lambda row: row["at"])
    if len(bars) < 15:
        raise ValueError("at least 15 completed regular-hours candles required for ATR warmup")
    current_date = now.astimezone(ET).date()
    today = [b for b in bars if b["at"].astimezone(ET).date() == current_date]
    start = datetime.combine(current_date, time(9, 30), ET)
    expected_count = max(0, int((now - start).total_seconds() // 300))
    expected = [start + timedelta(minutes=5 * n) for n in range(expected_count)]
    if [b["at"] for b in today] != expected:
        raise ValueError("today's completed five-minute candles are missing or non-contiguous")
    ema = bars[0]["close"]
    true_ranges = []
    previous = bars[0]["close"]
    for i, bar in enumerate(bars):
        ema = bar["close"] * 0.2 + ema * 0.8 if i else ema
        true_ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous), abs(bar["low"] - previous)))
        bar["ema9"] = ema
        bar["atr14"] = sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else None
        previous = bar["close"]
    volume = weighted = 0
    for bar in today:
        volume += bar["volume"]
        weighted += (bar["high"] + bar["low"] + bar["close"]) / 3 * bar["volume"]
        bar["vwap"] = weighted / volume if volume > 0 else None
    return today


def evaluate(symbol, bars, underlying_quote, now):
    """Return WAIT/BLOCKED/ARMED/ENTRY plus a deterministic, expiring signal."""
    now = timestamp(now)
    result = {"status": "WAIT", "reason": "Waiting for the 15-minute opening range.", "signal": None, "evidence": {}}
    if symbol not in {"SPY", "QQQ"}:
        return {**result, "status": "BLOCKED", "reason": "Only SPY and QQQ belong to this paper pilot."}
    try:
        day, _ = session_dates(now)
        local = now.astimezone(ET)
        if local.time() < time(9, 45):
            return result
        if local.time() >= time(10, 30):
            return {**result, "reason": "New entries stop at 10:30 ET."}
        today = _bars(bars, now)
        if len(today) < 3:
            return result
        high = max(row["high"] for row in today[:3])
        low = min(row["low"] for row in today[:3])
        evidence = {"opening_range_high": high, "opening_range_low": low, "range_completed_at": (today[2]["at"] + timedelta(minutes=5)).isoformat(), "research_only": True}
        result["evidence"] = evidence
        breakout_index = next((i for i in range(3, len(today)) if today[i]["close"] > high or today[i]["close"] < low), None)
        if breakout_index is None:
            return {**result, "reason": "Waiting for a completed candle to close outside the opening range."}
        direction = "CALL" if today[breakout_index]["close"] > high else "PUT"
        boundary = high if direction == "CALL" else low
        evidence.update(direction=direction, breakout_at=today[breakout_index]["at"].isoformat())
        retest = None
        for row in today[breakout_index + 1:breakout_index + 4]:
            outside = row["close"] > boundary if direction == "CALL" else row["close"] < boundary
            if not outside:
                return {**result, "reason": "Breakout canceled: a candle closed back through the range boundary."}
            touches = row["low"] <= boundary if direction == "CALL" else row["high"] >= boundary
            if touches:
                aligned = row["vwap"] is not None and (row["close"] > max(row["ema9"], row["vwap"]) if direction == "CALL" else row["close"] < min(row["ema9"], row["vwap"]))
                if aligned:
                    retest = row
                    break
        if retest is None:
            message = "Retest expired after three completed candles." if len(today) - breakout_index - 1 >= 3 else "Waiting for a retest that holds the range and aligns with EMA/VWAP."
            return {**result, "reason": message}
        created = retest["at"] + timedelta(minutes=5)
        expires = created + timedelta(minutes=5)
        if now >= expires:
            return {**result, "reason": "Next-candle entry window expired; no late entry is allowed."}
        atr = retest["atr14"]
        if atr is None or atr <= 0:
            raise ValueError("ATR warmup unavailable")
        trigger = retest["high"] if direction == "CALL" else retest["low"]
        stop = retest["low"] - 0.1 * atr if direction == "CALL" else retest["high"] + 0.1 * atr
        risk = abs(trigger - stop)
        target = trigger + 2 * risk if direction == "CALL" else trigger - 2 * risk
        evidence.update(retest_at=retest["at"].isoformat(), ema9=retest["ema9"], vwap=retest["vwap"], atr14=atr)
        identity = f"ORB_RETEST_PAPER_V1|{day}|{symbol}|{direction}|{retest['at'].isoformat()}"
        reason = f"{symbol} held its opening-range {'high' if direction == 'CALL' else 'low'} on a completed retest, aligned with 9EMA and session VWAP; next-candle {'high' if direction == 'CALL' else 'low'} break. Experimental, not a proven win rate."
        signal = {"signal_id": hashlib.sha256(identity.encode()).hexdigest(), "symbol": symbol,
                  "direction": direction, "trigger": trigger, "stop": stop, "target": target,
                  "created_at": created.isoformat(), "expires_at": expires.isoformat(),
                  "reason": reason, "evidence": evidence}
        result.update(status="ARMED", reason="Retest confirmed; waiting for the next candle to break its trigger.", signal=signal)
        if underlying_quote is None:
            return result
        if underlying_quote.get("symbol") != symbol or not underlying_quote.get("source"):
            raise ValueError("underlying identity/source is missing")
        observed = timestamp(underlying_quote.get("observed_at"))
        if not 0 <= (now - observed).total_seconds() <= 10 or observed < created:
            raise ValueError("underlying quote is stale, future-dated, or before the signal")
        price = number(underlying_quote.get("price"))
        if price <= stop if direction == "CALL" else price >= stop:
            return {**result, "status": "WAIT", "signal": None,
                    "invalidated_signal_id": signal["signal_id"],
                    "reason": "Retest stop already invalidated before entry."}
        triggered = price > trigger if direction == "CALL" else price < trigger
        if triggered:
            if price >= target if direction == "CALL" else price <= target:
                return {**result, "status": "WAIT", "signal": None, "reason": "Move already reached its target; do not chase."}
            result.update(status="ENTRY", reason=reason)
        return result
    except (ValueError, TypeError, KeyError) as exc:
        return {**result, "status": "BLOCKED", "signal": None, "reason": str(exc)}


def exit_reason(position, bars, underlying_quote, now):
    """Persist returned reason before attempting a fill; missing quotes never close."""
    now = timestamp(now)
    if position.get("exit_requested_at"):
        return position.get("exit_reason") or "Previously requested exit awaits a valid quote."
    if now.astimezone(ET).date().isoformat() != position["session_date"]:
        return "Unresolved prior-session position; an expiry-day exit observation is missing."
    if now.astimezone(ET).time() >= time(11, 30):
        return "11:30 ET paper-session flatten cutoff."
    if now - timestamp(position["entered_at"]) >= timedelta(minutes=60):
        return "Maximum 60-minute holding period reached."
    try:
        if underlying_quote is None or underlying_quote.get("symbol") != position["symbol"] or not underlying_quote.get("source"):
            raise ValueError("underlying observation unavailable")
        at = timestamp(underlying_quote.get("observed_at"))
        if not 0 <= (now - at).total_seconds() <= 10:
            raise ValueError("underlying quote stale or future-dated")
        price = number(underlying_quote.get("price"))
        call = position["direction"] == "CALL"
        if price <= position["stop"] if call else price >= position["stop"]:
            return "Underlying price reached the frozen retest stop."
        if price >= position["target"] if call else price <= position["target"]:
            return "Underlying price reached the 2R target."
        current = _bars(bars, now)
        latest = current[-1]
        if latest["at"] + timedelta(minutes=5) > timestamp(position["entered_at"]):
            if latest["close"] < latest["ema9"] if call else latest["close"] > latest["ema9"]:
                return "Completed five-minute candle broke the 9EMA against the position."
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return f"Data-health exit requested: {exc}. Wait for a genuine option quote; do not invent a fill."
    return None
