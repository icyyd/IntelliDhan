# 12 — Daily Briefing (8:30 AM ET)

A pre-market intelligence report generated every trading day, rendered on the homepage and pushed to Telegram at **8:30 AM ET** sharp (assembled 8:15–8:29 so 8:30 econ prints get a fast follow-up addendum when they land).

## 1. Content Sections (fixed order)

| # | Section | Contents & sources |
|---|---|---|
| 1 | **Market Bias Meter** | One-line stance: `RISK-ON / NEUTRAL / RISK-OFF / EVENT-DAY` + confidence; derived from MacroContext + index MTF trends |
| 2 | **Overnight & Futures** | ES/NQ/RTY % moves, notable overnight range, Asia/Europe session summary, DXY/10Y/oil/gold/BTC deltas |
| 3 | **Index Technical Read** | SPX, NDX, SMH (+ QQQ/SPY levels): trend matrix (M→1H), key levels for today (prior H/L, overnight H/L, VWAP anchors, gamma walls, expected move), pattern notes ("NDX coiling under 23,200 wall; break targets 23,325") |
| 4 | **Volatility & Positioning** | VIX level/term structure, IV rank on indices, put/call ratio, notable 0DTE positioning, "premium rich/cheap today" verdict (feeds RULE-T11) |
| 5 | **Macro Calendar** | Today's prints with times, consensus, prior (CPI/PPI/NFP/FOMC/PCE/claims/auctions) — FRED/BLS schedule reconciled against the MarketWatch calendar; event-lockout windows the engine will observe; Fed speakers + FOMC-cycle state (blackout/decision week); **IPO lockup expirations this week** for universe-adjacent names |
| 6 | **Headlines & Ratings Digest** | 5–8 market-moving headlines, one-line each with sentiment tag; earnings before/after bell for universe names with **confirmed times (Earnings Whispers), consensus + whisper EPS, and implied moves**; notable analyst upgrades/downgrades this morning (Benzinga); notable insider/superinvestor moves when present (Form 4 clusters, Dataroma updates) |
| 6b | **Sector Heat Map** | Finviz-style sector/industry performance treemap (internally computed, doc 02 §7) for 1D/1W/1M with the strongest & weakest sectors called out — feeds the "where's the money flowing" line |
| 7 | **Today's Game Plan** | Per module: what the engine is watching ("0DTE: ORB plays favored, trend day probability 62%; Swings: 3 setups near trigger — AAPL, AVGO, BABA; LEAPS: TQQQ collar roll window open; HODL: 2 names entered Attractive zone") |
| 8 | **Discipline Reminder** | Rotating card from the rulebook — the 4 Disciplines, Council quotes, and the Douglas/Tendler reminder library (doc 17 §6) — echoes the poster aesthetic |

## 2. Generation Pipeline

```
07:50  Data pull: overnight bars, futures, macro calendar, headlines, chains snapshot
08:00  Engine pre-compute: MTF refresh, level maps, expected moves, walls, IVR
08:15  LLM composer: sections 3/6/7 narrative from structured facts
       — constrained generation: every claim must cite an engine-computed fact ID;
         no free-form market opinions; numbers injected, never generated
08:25  Render: web panel (rich) + Telegram (HTML) + archive to Briefings library
08:30  Deliver. If an 8:30 print lands (CPI/PPI/NFP): auto-addendum within 3 min
       with actual-vs-consensus, surprise score, and updated bias if changed
12:30  Midday refresh (web only): bias check, morning recap, afternoon watch-list
16:15  EOD wrap: day recap, alert outcomes, tomorrow's calendar preview
```

The LLM step is presentation-only: all numbers, levels, trends, and probabilities come from the engine's structured outputs (accuracy requirement — the composer cannot invent data; failed fact-binding blocks publication and falls back to the structured table view).

## 3. Telegram Format (excerpt)

```
☀️ IntelliDhan Daily Brief — Thu Jul 10, 2026 · 8:30 ET
━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧭 Bias: RISK-ON (72%) — trend intact, quiet calendar
📈 Futures: ES +0.3% · NQ +0.5% · VIX 13.8 (−0.4)
── Index Read ──
SPX ▲: hold 6,340 → targets 6,382 wall · fail 6,318
NDX ▲▲: coiling under 23,200 · break targets 23,325 · EM ±0.8%
SMH ▲: leader; above all EMAs · 292 pivot
── Calendar ──
08:30 Jobless Claims (est 232k) · 13:00 10Y auction · Fed: Waller 14:00
── Game Plan ──
0DTE: ORB continuation favored · Swings: AAPL/AVGO/BABA near triggers
LEAPS: TQQQ collar roll window · HODL: 2 names in Attractive zone
🎯 Discipline: "The big money is not in the buying and selling, but in the waiting."
[📊 Full brief]
```

## 4. Briefings Library

Archive page: calendar view of all briefs + addenda + EOD wraps; each brief later annotated with "how the day actually went" (auto-generated at close) — the briefing itself is calibration-tracked (bias-call accuracy stat shown on the page, RULE-C2).
