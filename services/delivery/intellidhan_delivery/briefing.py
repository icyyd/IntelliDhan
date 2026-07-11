"""Daily briefing generator — fact-bound, template-based (doc 12).

Every number comes from engine state; no LLM sits in this path (doc 01 §5).
Renders Telegram text and a web payload. Scheduled 8:30 ET by the live loop
(pre-market variant uses last session's state + daily trends).
"""

from __future__ import annotations

from datetime import datetime

from intellidhan_analytics.profile import ProfileState
from intellidhan_engine.state import SymbolState
from intellidhan_ingestor.market_clock import ET
from intellidhan_schemas import Timeframe

_ARROW = {"STRONG_UP": "▲▲", "UP": "▲", "NEUTRAL": "►", "DOWN": "▼", "STRONG_DOWN": "▼▼"}

DISCIPLINE_CARDS = [
    "“The big money is not in the buying and selling, but in the waiting.” — Munger",
    "“Anything can happen.” — Douglas, Truth #1",
    "“Rigid in our rules and flexible in our expectations.” — Douglas",
    "“Losses are simply the cost of doing business.” — Douglas",
    "Trade the trend. Use a stop, every time. Control size. Don't revenge trade.",
    "“Your job is to suck less.” — Tendler, on improving your worst days first",
    "“Be fearful when others are greedy, greedy when others are fearful.” — Buffett",
]


def _bias(states: dict[str, SymbolState]) -> tuple[str, float]:
    scores = []
    for sym in ("QQQ", "SPY"):
        st = states.get(sym)
        if st:
            snap = st.trend_snap(Timeframe.D1)
            if snap:
                scores.append(snap.score)
    if not scores:
        return "NEUTRAL", 0.0
    avg = sum(scores) / len(scores)
    label = ("RISK-ON" if avg >= 25 else "RISK-OFF" if avg <= -25 else "NEUTRAL")
    return label, round(avg, 1)


def build_briefing(
    states: dict[str, SymbolState],
    profiles: dict[str, ProfileState] | None,
    now: datetime,
    game_plan: list[str] | None = None,
) -> dict:
    """Returns {telegram: str, web: dict} — same facts, two renderings."""
    et = now.astimezone(ET)
    bias, bias_score = _bias(states)
    index_reads = []
    for sym, st in states.items():
        matrix = st.mtf_states()
        chips = "  ".join(f"{tf}{_ARROW.get(s, '?')}" for tf, s in sorted(
            matrix.items(), key=lambda kv: kv[0]))
        d = st.indicators(Timeframe.D1)
        line = {"symbol": sym, "chips": chips,
                "close": round(st.last_bar.close, 2) if st.last_bar else None,
                "atr": round(d.atr14, 2) if d and d.atr14 else None,
                "rsi": round(d.rsi14, 1) if d and d.rsi14 else None,
                "levels": [
                    {"price": round(lv.price, 2), "role": lv.role.value,
                     "strength": lv.strength, "flipped": lv.flipped}
                    for lv in st.levels.visible()[:4]
                ]}
        if profiles and sym in profiles:
            p = profiles[sym]
            line["profile"] = {"poc": p.poc, "va": [p.va_low, p.va_high],
                               "open_type": p.open_type.value, "shape": p.shape.value}
        index_reads.append(line)

    card = DISCIPLINE_CARDS[et.timetuple().tm_yday % len(DISCIPLINE_CARDS)]
    plan = game_plan or [
        "Engine live on QQQ/SPY/SMH/TQQQ — alerts only above calibrated 75%.",
        "Strategies under SHADOW calibration continue harvesting outcomes.",
    ]

    tg_lines = [
        f"☀️ IntelliDhan Daily Brief — {et:%a %b %d, %Y · %H:%M} ET",
        "━" * 27,
        f"🧭 Bias: {bias} (D-trend {bias_score:+.0f})",
        "── Index Read ──",
    ]
    for r in index_reads:
        lvl = " · ".join(f"{v['role'][:3]} {v['price']}" for v in r["levels"][:2])
        tg_lines.append(
            f"{r['symbol']}: {r['chips']}"
            + (f" · close {r['close']}" if r['close'] else "")
            + (f" · RSI {r['rsi']}" if r['rsi'] else "")
            + (f" · {lvl}" if lvl else ""))
    tg_lines += ["── Game Plan ──", *plan, f"🎯 {card}",
                 "⚠️ Educational tool — not financial advice."]
    return {
        "telegram": "\n".join(tg_lines),
        "web": {"generated_at": now.isoformat(), "bias": bias, "bias_score": bias_score,
                "index_reads": index_reads, "game_plan": plan, "discipline_card": card},
    }
