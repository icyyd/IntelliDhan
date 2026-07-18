# IntelliDhan — Trading Signal Platform Specification

**Version:** 0.2 alpha · **Date:** 2026-07-17 · **Status:** Working personal-terminal foundation

IntelliDhan is a working personal stock-picking and signal terminal. Today it
provides a configured-universe market monitor, research-stage 0DTE/Swing signal
engine, durable alert/paper audit state, a compact technical screener, saved
screens/watchlists, and on-demand forward trend analysis. LEAPS, HODL,
fundamentals, estimates, events, and portfolio reconciliation remain planned and
must not be presented as implemented.

## Current implementation

- Card-first Today terminal with SPX/SPY/QQQ context, a deterministic top-three
  focus list, curated radar, rich signal plans, Discover, Analyze, 0DTE, and
  Swing tasks.
- Optional SEC filing/financial-strength, Alpha Vantage news-tone, and Finnhub
  social-attention enrichment. Every provider carries source status and missing
  inputs are excluded with visible score coverage.
- Invite-only database accounts with scrypt password hashes, opaque
  server-expiring sessions, and ADMIN/TRADER/VIEWER roles. Preferences, capital
  limits, watchlists, and saved screens are isolated per user; broker
  credentials never enter this app.
- SQLite local operational state or PostgreSQL via `DATABASE_URL`; production
  deployments require PostgreSQL or a mounted persistent volume, enforced by a
  readiness gate when `INTELLIDHAN_REQUIRE_DURABLE_STATE=true`.
- Live data-quality quarantine and readiness-aware `/api/health`.
- Adjusted, settled-session smart-play ranking for momentum leaders, breakout
  watches, and trend pullbacks, with partial-scan failures and configured-universe
  scope shown explicitly.
- Optional, account-only OpenAI thesis synthesis from server evidence with
  a closed evidence-selection schema and server-rendered narrative; it cannot
  invent prose, alter rank, or create an execution intent.
- Optional ADMIN/TRADER dispatch of a server-verified candidate to a published
  ChatGPT Workspace Agent for deeper research. IntelliDhan requests analysis
  only; production requires a dedicated agent with no broker tools and no
  general write tools. A separately reviewed, destination-constrained result
  delivery action is the only allowed exception. The trigger API queues the run
  but does not yet return its output.
- Arbitrary-ticker, adjusted-history analysis with conservative 21/63-session
  forward evidence and fixed-rule backtests; signed-in configured-universe
  dossiers also expose the current SEC/news/social research snapshot.

Run locally with `.venv/bin/uvicorn intellidhan_gateway.app:app --port 8321`.
Copy `.env.example` to `.env`, set a random `INTELLIDHAN_OWNER_TOKEN` of at
least 24 characters for first-admin setup, optionally set a separate
`INTELLIDHAN_INVITE_CODE`, and configure durable state before production. Open
the Account panel to create the first administrator. See
[Accounts and personal settings](docs/21-accounts-and-personal-settings.md).

## Document Index

| # | Document | Contents |
|---|----------|----------|
| 00 | [Vision & Investing Principles](docs/00-vision-and-principles.md) | Product vision, the Investor Council rulebook, technical discipline codex |
| 01 | [Unified Platform Architecture](docs/01-architecture.md) | Six-plane event-driven design, event bus topics, MarketStateSnapshot, closed behavioral loop, degradation matrix, monorepo layout, doc→component traceability |
| 02 | [Data Sources & Integrations](docs/02-data-sources.md) | TradingView, Yahoo Finance, macro/econ feeds, Robinhood MCP, Telegram |
| 03 | [Signal & Confidence Engine](docs/03-signal-engine.md) | Multi-timeframe trend engine, factor scoring, ≥75% confidence gating, calibration |
| 04 | [Module: 0DTE](docs/04-module-0dte.md) | SPX/NDX/SMH + leveraged ETFs, ORB/VWAP/EMA playbooks, intraday spreads |
| 05 | [Module: Swings](docs/05-module-swings.md) | 2–20 day options & equity swings, earnings plays, credit spreads |
| 06 | [Module: LEAPS](docs/06-module-leaps.md) | Long-dated options, PMCC, Dynamic Collar (TQQQ strategy), stock replacement |
| 07 | [Module: Buy & Hold (HODL)](docs/07-module-hodl.md) | Quality-compounder screening, valuation gates, accumulation zones |
| 08 | [Strategy Library](docs/08-strategy-library.md) | Full catalog: collars, credit spreads, condors, diagonals, wheels, income engines |
| 09 | [Alerts & Position Sizing](docs/09-alerts-and-sizing.md) | Alert schema, BTO/STO pricing, stops/TP zones, contract sizing, Telegram delivery |
| 10 | [Trade Log & Performance](docs/10-trade-log.md) | Automated tracking, P&L simulation, calibration feedback loop |
| 11 | [UI / UX Specification](docs/11-ui-ux.md) | Design system, screens, components, charting, interaction model |
| 12 | [Daily Briefing](docs/12-daily-briefing.md) | 8:30 AM ET market analysis: format, content pipeline, delivery |
| 13 | [Risk, Guardrails & Compliance](docs/13-risk-and-compliance.md) | Capital protection rules, kill switches, disclaimers, data licensing |
| 14 | [Roadmap & Milestones](docs/14-roadmap.md) | Phased build plan from MVP to full platform |
| 15 | [Technical Playbook](docs/15-technical-playbook.md) | Price action (4 stages, M.A.E., candlestick reading), chart-pattern library, MACD sheet, tape proxies |
| 16 | [Market Profile Layer](docs/16-market-profile.md) | Dalton auction theory: value areas, open types, day types, failed auctions, p/b shape vetoes |
| 17 | [Trader Psychology Layer](docs/17-trader-psychology.md) | Douglas probabilistic voice + consistency framework; Tendler mental-game toolkit & error detection |
| 18 | [Enhancement Review](docs/18-enhancement-review.md) | Post-implementation audit: research/production parity, risk-state wiring, evidence vocabulary, UX direction — living document, agent-readable implementation brief |
| 19 | [Trend Analysis &amp; On-Demand Module](docs/19-trend-analysis-and-on-demand-module.md) | Cross-ticker trend methods, walk-forward evidence, and arbitrary-symbol analysis contract |
| 20 | [One-Stop Terminal Gap Analysis](docs/20-one-stop-terminal-gap-analysis.md) | Full-solution audit and prioritized terminal roadmap |
| 21 | [Accounts &amp; Personal Settings](docs/21-accounts-and-personal-settings.md) | Account/session architecture, roles, user-owned database state, APIs, and deployment requirements |
| 22 | [Smart-Play Scanner &amp; AI Thesis](docs/22-smart-play-scanner-and-ai-thesis.md) | Fixed momentum/breakout/pullback rules, walk-forward diagnostic, OpenAI evidence contract, and card-first UX |
| 23 | [ChatGPT Workspace Agent Dispatch](docs/23-chatgpt-workspace-agent-dispatch.md) | Published-agent API trigger, security boundary, setup, UX, and operational contract |
| 24 | [Daily Brief Landing Integration](docs/24-daily-brief-landing-integration.md) | Private artifact adapter, freshness/fallback contract, setup-card UX, and deployment configuration |
| 25 | [Signal Terminal Redesign](docs/25-signal-terminal-redesign.md) | Agent-readable Today hierarchy, multi-feed rank contract, rich alerts, strategy evidence boundaries, and rollout plan |

## Core Product Tenets

1. **Quality over quantity.** Only alert setups the engine scores at ≥75% calibrated confidence. Silence is a feature.
2. **Every alert is complete.** Entry (BTO/STO price), stop, take-profit zones, contract count sized to the user's daily capital budget, confidence score, and the multi-timeframe rationale — nothing left for the user to compute.
3. **Trend is law.** No alert fires against the dominant multi-timeframe trend without an explicit, labeled counter-trend justification.
4. **Protect the downside first.** Position sizing, stops, and hedged structures (collars, spreads) are first-class citizens, not afterthoughts.
5. **Measure everything.** Every alert is logged and tracked to outcome; the confidence engine is recalibrated against realized results.
6. **Readable at a glance.** The UI privileges clarity: a trader should grasp any alert in under 5 seconds.

## Honest Framing (read first)

"75% chance of profitability" is a gated calibration claim, not a marketing
label. Unvalidated evidence is capped below the live threshold, and no strategy
is currently assumed qualified. The platform is not financial advice. It can
create supervised, credential-free execution intents for a primary agent using
Robinhood MCP; real execution remains fail-closed behind explicit evidence,
allowlist, risk, owner, agent-review, protection, and reconciliation controls.
