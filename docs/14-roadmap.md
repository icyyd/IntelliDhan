# 14 — Roadmap & Milestones

## Phase 0 — Foundations (Weeks 1–2)
- Repo scaffold (monorepo: `api/`, `engine/`, `web/`, `shared-schemas/`), Docker Compose, CI with indicator golden tests.
- Data plane v1: Robinhood MCP + Yahoo providers behind `DataProvider`, TimescaleDB schema, ingestor with quality sentinel.
- Market clock service + exchange calendar; historical backfill (10y D, 2y 1H, 90d 1m for universe).
**Exit criteria:** replay a recorded session end-to-end; indicators match reference within tolerance.

## Phase 1 — Engine Core + First Module (Weeks 3–6)
- MTF trend engine + level maps + factor framework + calibration harness (docs 03).
- **Swing module first** (slower cadence = safer bring-up): directional strategies + bull put spread.
- Alert Composer + schema, Telegram bot (alerts + buttons), Trade Log with paper track.
- Backtests + calibration tables for the first 6 strategies; 2-week SHADOW mode.
**Exit criteria:** 2 weeks of shadow alerts logged with calibration report; alert latency ≤ 2 s in replay.

## Phase 2 — Web App v1 (Weeks 5–9, overlaps)
- Design system + command center + Swing cockpit + Trade Log views + Settings (budgets, Telegram pairing).
- WebSocket live layer, PWA + web push, TradingView chart integration with alert overlays.
**Exit criteria:** 5-second-rule usability pass on alert cards; Lighthouse ≥ 90.

## Phase 3 — 0DTE Module (Weeks 8–12)
- Intraday pipeline hardening (1m/5m latency path), ORB/VWAP/EMA9/level-rejection strategies, walls & expected-move inputs, breadth pack.
- Income desk: credit spreads + condors; event lockouts; cooldowns; kill switches.
- 0DTE cockpit UI (chart rack, level ladder, suppression tape). 30-day shadow before live alerts.
**Exit criteria:** 30 shadow sessions with calibration gap ≤ 5 pts on ≥ 2 strategy classes.

## Phase 4 — LEAPS + HODL (Weeks 11–15)
- Quality screener + valuation bands; HODL accumulation/fear ladders; Lynch tagging.
- LEAPS strategies + **Dynamic Collar state machine** with collar dashboard; PMCC/ZEBRA.
- Daily Briefing pipeline (8:30 ET) + Briefings library + EOD wrap.
**Exit criteria:** collar state machine passes scenario tests (rally/crash/chop scripts); first live briefing week reviewed.

## Phase 3.5 — Auction & Pattern Layers (Weeks 10–14, overlaps)
- TPO/volume profile construction + value areas + open/day-type classifiers + auction-quality rules (doc 16) with the veto wiring into the engine; profile pane UI.
- Pattern detector library + market-stage model + candlestick heuristics + MACD states (doc 15); pattern overlays on charts.
- Golden-file tests: classifiers validated against 30 hand-labeled historical sessions (open types, day types, p/b shapes) — ≥ 90% agreement required before the vetoes go live.

## Phase 5 — Polish & Feedback Loop (Weeks 15–18)
- Calibration dashboards, auto-demotion live, weekly review generator, journal.
- Mental Game toolkit (doc 17): warmup/cooldown flows, A/B/C tagging, emotional maps, mental hand history, behavioral error detectors + intervention ladder, 20-Trade Sample Mode.
- Order staging via Robinhood MCP (behind toggle + confirm modal), positions-aware conflict warnings.
- Performance hardening, mobile PWA pass, accessibility audit, backup/restore.
**Exit criteria:** full-platform game-day (simulated CPI day replay) with zero guardrail violations.

## Phase 6 — Exploratory (Post-v1)
- Designed-for-skew strategies (GAMMA_WALL_FADE, SKEW_ARB_COLLAR, DISPERSION_LITE) through the governance pipeline (doc 08 §4).
- Additional data providers (Polygon/Tradier) for redundancy; options-flow feed upgrade.
- Multi-user consideration → legal/compliance gate (doc 13 §3) before any distribution.

## Open Questions (to resolve before Phase 1 build)
1. Confirm per-module capital budgets and account size band (drives all sizing defaults and kill-switch levels).
2. Telegram: single combined channel vs per-module topics?
3. TradingView: is a paid plan available for Advanced Charts datafeed, or start Lightweight-Charts-only?
4. Preferred backtest data vendor for 1-min index/options history (free tiers are thin for SPX/NDX options) — this is the main paid-data decision.
5. Robinhood account type (margin/IRA) — affects spread strategies availability and settlement handling.
