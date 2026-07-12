# IntelliDhan — Trading Signal Platform Specification

**Version:** 0.1 (Draft Spec) · **Date:** 2026-07-10 · **Status:** Pre-implementation

IntelliDhan is a high-performance, graphically rich web platform that generates high-confidence trading alerts across four time horizons — **0DTE**, **Swings**, **LEAPS**, and **Buy & Hold (HODL)** — grounded in the principles of great investors (Munger, Buffett, Ackman, Lynch) and disciplined technical analysis (multi-timeframe trend, VWAP, ORB, EMA structure, RSI zones).

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

## Core Product Tenets

1. **Quality over quantity.** Only alert setups the engine scores at ≥75% calibrated confidence. Silence is a feature.
2. **Every alert is complete.** Entry (BTO/STO price), stop, take-profit zones, contract count sized to the user's daily capital budget, confidence score, and the multi-timeframe rationale — nothing left for the user to compute.
3. **Trend is law.** No alert fires against the dominant multi-timeframe trend without an explicit, labeled counter-trend justification.
4. **Protect the downside first.** Position sizing, stops, and hedged structures (collars, spreads) are first-class citizens, not afterthoughts.
5. **Measure everything.** Every alert is logged and tracked to outcome; the confidence engine is recalibrated against realized results.
6. **Readable at a glance.** The UI privileges clarity: a trader should grasp any alert in under 5 seconds.

## Honest Framing (read first)

"75% chance of profitability" is implemented as a **calibrated confidence score**: a blend of model-estimated probability of profit (POP), historical win rate of the identical setup class in backtests, and live forward-tracked accuracy. The platform continuously reports its *realized* hit rate next to its *claimed* confidence so drift is visible. No market prediction is guaranteed; the system is an analysis and alerting tool, **not financial advice and not an auto-trader** — order execution always requires explicit human confirmation.
