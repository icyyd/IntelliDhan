"""Telegram/console alert formatter — doc 09 §5 house style (BABA-screenshot layout)."""

from __future__ import annotations

from intellidhan_ingestor.market_clock import ET
from intellidhan_schemas.signals import Alert, Vehicle

_ARROWS = {"STRONG_UP": "↑↑", "UP": "↑", "NEUTRAL": "→", "DOWN": "↓", "STRONG_DOWN": "↓↓"}

DISCLAIMER = "⚠️ Educational tool — not financial advice. Options risk 100% loss."


def format_alert(alert: Alert) -> str:
    et = alert.created_at.astimezone(ET)
    lines = [
        f"🚨 IntelliDhan · {alert.module.value} · {alert.strategy.replace('_', ' ')}"
        f"            ⏱ {et:%H:%M} ET",
        "━" * 27,
    ]
    if alert.vehicle == Vehicle.OPTION and alert.legs:
        leg = alert.legs[0]
        lines.append(f"📈 {alert.symbol} {leg.strike:g} {leg.option_type}  ·  exp {leg.expiry}"
                     f"  ·  Δ{abs(leg.delta or 0):.2f} (est)")
        size = f"#️⃣ {alert.contracts} contract{'s' if alert.contracts != 1 else ''}"
    else:
        lines.append(f"📈 {alert.symbol} {'SHARES' if alert.equity_qty else ''} @ "
                     f"{alert.underlying_price:.2f}")
        size = f"#️⃣ {alert.equity_qty} shares"
    lines += [
        f"🎯 {alert.action.value} limit: ${alert.entry_limit:,.2f}   "
        f"(zone {alert.entry_zone[0]:,.2f}–{alert.entry_zone[1]:,.2f} — don't chase above)",
        f"🛑 Stop: {alert.symbol} {alert.stop_underlying:,.2f}"
        + (f"  (~${alert.stop_est_vehicle:,.2f} opt)" if alert.vehicle == Vehicle.OPTION else "")
        + f"  · {alert.stop_rule}",
    ]
    tps = [f"T{i + 1} {tp.underlying:,.2f} (trim {tp.tranche:.0%})"
           for i, tp in enumerate(alert.take_profits) if tp.underlying]
    lines.append("💰 " + " · ".join(tps))
    lines.append(f"{size}  ·  {alert.budget_note}")
    matrix = "  ".join(f"{tf}{_ARROWS.get(st, '?')}" for tf, st in
                       sorted(alert.trend_matrix.items()))
    lines.append(f"📊 Confidence {alert.confidence:.0%}  ·  R:R {alert.reward_risk}  ·  {matrix}")
    lines.append(f"💡 {alert.thesis}")
    for m in alert.management:
        lines.append(f"▸ {m}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_milestone(alert: Alert, pct_gain: float, current: float, new_stop: str) -> str:
    return (f"💰 {alert.symbol} +{pct_gain:.0%} reached — now ${current:,.2f} "
            f"(entry ${alert.entry_limit:,.2f}). {new_stop}\n{DISCLAIMER}")
