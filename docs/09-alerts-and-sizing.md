# 09 — Alerts & Position Sizing

The alert is the product. It must be complete (nothing left to compute), instantly readable, and identical in substance across web and Telegram.

## 1. Alert Schema (canonical, Pydantic → generated TS types)

```jsonc
{
  "alert_id": "alr_20260710_1004_ndx_orb_c",
  "created_at": "2026-07-10T14:04:31Z",
  "module": "0DTE",                       // 0DTE | SWING | LEAPS | HODL
  "strategy": "ORB_BREAKOUT",
  "action": "BTO",                        // BTO | STO | BTC | STC | MULTI_LEG | EQUITY_BUY | EQUITY_SELL
  "symbol": "NDX",  "underlying_price": 23142.55,
  "instrument": {
    "type": "OPTION",                     // OPTION | SPREAD | EQUITY | COLLAR | ...
    "legs": [{ "occ": "NDXP 260710C23200000", "side": "BUY", "type": "CALL",
               "strike": 23200, "expiry": "2026-07-10", "delta": 0.38, "iv": 0.19 }]
  },
  "pricing": {
    "entry_limit": 24.50,                 // suggested BTO/STO limit (mid + slippage model)
    "entry_zone": [24.00, 25.20],         // acceptable range; outside → do not chase
    "stop": { "type": "UNDERLYING_CLOSE", "level": 23085, "est_option_value": 14.80,
              "rule": "2 consecutive 5m closes below ORB mid" },
    "take_profit": [
      { "zone": [31.0, 33.0], "underlying": 23260, "tranche": 0.33, "basis": "prior high + 0.5x expected move" },
      { "zone": [38.0, 41.0], "underlying": 23320, "tranche": 0.33, "basis": "gamma wall 23325" },
      { "zone": null,          "underlying": null,  "tranche": 0.34, "basis": "runner — trail 5m 9EMA" }
    ],
    "max_loss_per_contract": 970, "reward_risk": 2.4, "expected_move_today": 0.82
  },
  "sizing": {
    "budget_ref": "daily_0dte", "budget_available": 4000,
    "risk_cap_pct": 0.25, "contracts": 1, "capital_required": 2450,
    "dollar_risk": 970, "note": "1 contract risks $970 = 24% of today's $4,000 0DTE budget"
  },
  "confidence": { "score": 0.78, "threshold": 0.75,
    "factors": { "trend": 84, "setup": 78, "levels": 70, "momentum": 88,
                 "volatility": 66, "flow": 61, "macro": 75, "pop": 72 },
    "class_realized_winrate_90d": 0.74 },
  "trend_matrix": { "D": "UP", "1H": "STRONG_UP", "15m": "STRONG_UP", "5m": "STRONG_UP" },
  "thesis": "NDX broke the 30-min opening range high (23,120) with two 5m closes above, 2.1x relative volume, holding above VWAP. 15m/1H/D all aligned up; next resistance = gamma wall at 23,325.",
  "invalidation": "2 consecutive 5m closes back below ORB mid (23,085) — momentum failure.",
  "management": ["Trim 33% in TP1 zone, move stop to breakeven", "Trim 33% at TP2", "Trail runner on 5m 9EMA", "Hard flatten 15:55 ET"],
  "risks": ["CPI in 4 days (outside horizon)", "Lunch-hour volume fade after 11:30"],
  "expiry_context": { "flatten_by": "15:55 ET" },
  "links": { "chart": "/chart/NDX?alert=alr_...", "log": "/log/alr_..." },
  "status": "ACTIVE"                      // ACTIVE | FILLED_TRACKED | STOPPED | TP_HIT | EXPIRED | CANCELLED
}
```

## 2. Pricing Algorithms

- **Entry limit:** options mid ± slippage (BTO: mid + 40% of half-spread; STO: mid − 40%); rounded to tick. **Entry zone** upper bound = limit + 0.25× expected 5-min option move — beyond it the alert card flips to "DO NOT CHASE".
- **Stop:** always derived from the *underlying* invalidation level (structure break, RULE-M1 inversion), then translated to an estimated option value via delta/gamma — both shown. Premium structures: stop = 2× credit or short-strike breach.
- **TP zones:** level-map targets (walls, prior highs, measured moves, expected-move fractions) — zones not points, with per-tranche fractions.
- Stale-quote guard: if the option's quote is older than 10 s at compose time, re-fetch before publishing; alert auto-expires (`CANCELLED`) if not acknowledged within its validity window (0DTE: 10 min; swing: end of day; LEAPS/HODL: 3 days).

## 3. Position Sizing

Inputs: per-module **daily/standing capital budgets** (user settings, editable any time), risk caps, drawdown state.

```
risk_budget   = module_budget × risk_cap_pct × drawdown_multiplier
contracts     = floor(risk_budget / per_contract_risk)     // per_contract_risk = entry − stop_est (long) | width − credit (spreads)
capital_check = contracts × capital_per_contract ≤ module_budget_available
if contracts == 0 → try budget-fit alternatives (cheaper vehicle: TQQQ instead of NDX,
                    narrower spread width, further expiry) → else suppress alert with
                    reason "insufficient budget for defined-risk entry" (never force size)
```

- **Drawdown multiplier (RULE-C3, anti-martingale):** 1.0 baseline; 0.5 after module down ≥ 50% of daily budget; 0 (halt) at 100% — kill switch, doc 13.
- Robinhood buying power cross-check: alert flags if suggested capital exceeds live buying power.
- Every alert states its sizing sentence in plain English (see `sizing.note`).

## 4. Delivery — Web

- WebSocket push → alert card slides into the Alert Rail + toast + optional sound (per-module sounds, user-configurable, off by default for HODL).
- Web Push (PWA) for background delivery, deep-links to the alert card.
- Missed-alert backfill on reconnect; alerts are idempotent by `alert_id`.

## 5. Delivery — Telegram (format spec)

Mirrors the BABA-screenshot house style — scannable field grid, emoji anchors, milestone follow-ups:

```
🚨 IntelliDhan · 0DTE · ORB BREAKOUT            ⏱ 10:04 ET
━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 NDX 23200 CALL  ·  exp 2026-07-10 (0DTE)
🎯 BTO limit: $24.50   (zone 24.00–25.20 — don't chase above)
🛑 Stop: NDX 23,085  (~$14.80 opt)  · 2×5m closes below ORB mid
💰 TP1 $31–33 (trim ⅓, stop→BE) · TP2 $38–41 (trim ⅓) · runner trails 5m 9EMA
#️⃣ 1 contract  ·  risk $970 (24% of $4,000 budget)
📊 Confidence 78%  ·  trend D↑ 1H↑↑ 15m↑↑ 5m↑↑
💡 ORB break + 2.1x vol, above VWAP; next wall 23,325
⚠️ Flatten by 15:55 ET
[✅ Took it] [👀 Watching] [❌ Pass] [📈 Chart]
```

- Chart snapshot image attached (entry/stop/TP zones drawn).
- **Milestone follow-ups** (threaded reply to original): `💰 +60% reached — now $2.47 (entry $1.50). Raise stop to +40% ($2.10). Next TP zone $2.75–2.90.` Also stop-hit, TP-hit, flatten-reminder, and thesis-break messages.
- Daily briefing message spec in doc 12; all messages owner-chat-locked.

## 6. Alert Card (web) — content contract

Doc 11 owns visuals; content contract here: header (module chip, strategy name, confidence dial, age timer) → instrument line (symbol, strike/expiry, action badge BTO/STO color-coded) → price ladder graphic (entry zone / stop / TP zones drawn vertically to scale) → sizing sentence → trend matrix chips → collapsible thesis/invalidation/management/risks → action row (Track / Pass / Stage order via MCP / Open chart). A card must be fully comprehensible in ≤ 5 seconds at the collapsed state.
