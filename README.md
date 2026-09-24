# IntelliDhan — Trading Signal Platform Specification

**Version:** 0.2 alpha · **Date:** 2026-09-24 · **Status:** Research beta; live profitability unvalidated

**Last system pass:** added a private, durable local Simulation runtime with
generated local credentials, loopback-only access, account setup, and verified
SQLite backups. Cloud credentials are not inherited. Research-only 9EMA signals
can now enter Simulation without weakening Live confidence/calibration gates.
Fresh simulated entries recheck premium debit, current capital limits, daily
usage, and open-position count without silently resizing a selected contract.
Contract validation rejects boolean quantities explicitly and follows the
repository's pinned Ruff rules.
Underlying paper exits respect shortened sessions; missing exit observations
are shown as unscored, never invented wins or losses. The earlier beta outage
recovery and lower-overhead refresh work remains in this branch. No strategy
was promoted to Live and no real orders were placed.

Fresh frozen-parameter SPY/QQQ tests on 42 completed sessions through September 23
were negative after costs for both ORR profiles and the 30m EMA crossover.
See [beta validation evidence](docs/research/2026-09-24-beta-validation.md).
Koyeb's free PostgreSQL tier provides only five active hours per month;
these optimizations **do not make continuous trading reliable on that quota**
or restore a quota-exhausted database. The local research profile removes that
hosting dependency without replacing or importing the cloud database. Koyeb
hosting/billing and cloud execution mode were not changed. Local operation still
depends on this computer, internet, provider limits, and validated evidence; it
does not establish profitability. See [local research desk](docs/35-local-research-desk.md)
and [beta limits and recovery](docs/34-beta-reliability-and-strategy-readiness.md).

The Opening Range Reversal video rules are captured as a separate,
underlying-only research backtest (`ORB_REVERSAL_15M`) with a point-in-time
15-minute range, daily-ATR manipulation gate, 5-minute reversal confirmation,
prior-day level experiment, costs, and walk-forward tuning. Its short Yahoo
window is diagnostic only; daily context uses the same raw price basis as the
intraday bars, and it is not live-eligible or an options-performance claim.
See [Opening Range Reversal Backtest](docs/32-opening-range-reversal-backtest.md).
The July permutation study found promising shadow candidates, but the September
hold-later check did not reproduce their edge. The exact
1,152-variant study is reproducible with
`scripts/opening_range_reversal_permutations.py`. The improved settings are
available only as the explicit `shadow_candidate` research profile; `control`
remains the default and live eligibility is unchanged. The five-year SPY/SPX
tuning plan and data-source handoff are documented in
[ORR Five-Year Fine-Tuning Model](docs/33-orr-five-year-fine-tuning-model.md).

IntelliDhan is a working personal stock-picking and signal terminal. Today it
provides a configured-universe market monitor, research-stage 0DTE/Swing signal
engine, durable alert/paper audit state, a compact technical screener, saved
screens/watchlists, and on-demand forward trend analysis. LEAPS, HODL,
fundamentals, estimates, events, and portfolio reconciliation remain planned and
must not be presented as implemented.

## Current implementation

- Signals-first Today terminal: signal radar leads the page; SPX/SPY/QQQ trend,
  brief/headlines, macro events, and the completed-bar watch sit below as
  context. Selecting a radar card opens the complete plan while the legacy board
  stays hidden to avoid duplicate alerts. Discover, Analyze, 0DTE, and Swing
  remain separate tasks.
- Module screens keep the signal queue primary, expose compact live status
  strips, and collapse chart/profile context until requested. External research
  feeds and the daily brief refresh every two minutes while Today is visible; stock-pick
  cards expose a compact business case, evidence, analyst-target context when
  available, and explicit invalidation/risk context before the full Analyze
  dossier.
- Responsive collision guards keep the header, mobile task bar, sign-in/account
  controls, and compact action rows inside the viewport from 320px through
  desktop widths; controls compress or hide status-only chips before they can
  overlap or paint off-canvas, while SPX/SPY/QQQ feed copy uses a shrink-safe
  text wrapper so the status dot never covers the first character.
- The Today view is one responsive scan path with no duplicate right-rail
  decision rail. Live state, daily brief, macro events, focus ranking, and
  alerts continue to refresh automatically; long-form signal cards and detail
  context remain available progressively when a user opens a setup.
- Development checks pin Ruff 0.4.10 while the existing codebase is migrated
  to newer lint rules, keeping CI results reproducible across contributors.
- Robinhood-informed interaction hierarchy with one global command surface,
  progressive disclosure for secondary evidence, fewer visible actions, and
  automatic two-minute market/analysis refresh with freshness feedback.
- Production D4.2 owl-and-lotus assets, favicon, deterministic 1200×630 share artwork,
  agent-readable design tokens, and a responsive dark/light visual theme.
  External brand lettering is outlined for portable rendering; orange marks
  brand, focus, and primary actions while labeled green/red remain reserved for
  market semantics.
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
- Private local launcher with durable accounts/journals outside Git, no cloud
  `.env` loading, host/origin checks, Simulation-only server enforcement, safe
  start/status/stop, first-admin setup, and consistent SQLite backups. It does
  not install a background broker agent or survive sleep/reboot automatically.
- Live data-quality quarantine and readiness-aware `/api/health`.
- Storage failures return sanitized HTTP 503 with `Retry-After`; a shared
  circuit breaker retries normal outages after 60 seconds and active-time quota
  failures after 30 minutes. Account cookies are preserved. Boot restoration
  and account handlers run off the event loop; remaining synchronous market
  persistence is still a scaling limitation. Successful browser probes cannot
  bypass required engine reconciliation. Explicit logout still clears local
  cookies during an outage and reports unsuccessful server-side revocation.
- Optional option research uses 0–1DTE scalp, 21–90DTE swing, and ≥365DTE LEAPS
  contracts; HODL stays equity-only. Quotes must pass finite/two-sided liquidity
  checks; full debit is reserved as maximum option loss. Yahoo options remain
  research-only, and the production composer still uses underlying plans.
  Expiries beyond the verified exchange calendar (currently 2027) are withheld.
- Adjusted, settled-session smart-play ranking for momentum leaders, breakout
  watches, and trend pullbacks, with partial-scan failures and configured-universe
  scope shown explicitly.
- Optional, account-only OpenAI thesis synthesis from server evidence with
  a closed evidence-selection schema and server-rendered narrative; it cannot
  invent prose, alter rank, or create an execution intent.
- Optional, signed-in Claude review of the immutable multi-brain dossier packet.
  Claude uses server-side structured output without tools, browsing, MCP, or
  automation or personal sizing state; user capital budgets and reference
  directories are excluded. It can surface conflicts, risks, and diligence
  questions, but cannot change deterministic specialist scores, posture, rank,
  sizing, or execution. Configure only the deployment secret
  `ANTHROPIC_API_KEY`; never paste the key into the browser or repository.
- Optional ADMIN/TRADER dispatch of a server-verified candidate to a published
  ChatGPT Workspace Agent for deeper research. IntelliDhan requests analysis
  only; production requires a dedicated agent with no broker tools and no
  general write tools. A separately reviewed, destination-constrained result
  delivery action is the only allowed exception. The trigger API queues the run
  but does not yet return its output.
- Arbitrary-ticker, adjusted-history analysis with conservative 21/63-session
  forward evidence and fixed-rule backtests; signed-in configured-universe
  dossiers also expose the current SEC/news/social research snapshot.
- Codex execution-intent bridge for Robinhood's official Trading MCP. Contract
  v2.0 exposes only `SIMULATION` and time-limited `LIVE`; the project declaration
  contains no credentials and claims require the exact `codex` identity.
  Simulation is the default, broker authentication stays in the local Codex
  host, and the former Claude execution contract remains archived.
- Research-only `EMA9_MTF_0DTE` monitor for completed-bar SPY/QQQ 9EMA reclaims
  with 5m/15m/1h/daily alignment, VWAP, RSI, relative-volume, time-window, and
  risk-geometry gates. Qualified observations are stored as `SHADOW` signals
  and underlying paper trades; they cannot reach Telegram or Robinhood. The
  chronological 55-day study failed its sample/stability bar, so no profitability
  or probability claim is made.
- Strategy evidence object on Setup/Alert (`evidence_status`, sample size,
  intervals, expected net R after cost stress). `pop_based` live path may be
  suppressed with `gate=expectancy` when declared net edge is negative.
  `PULLBACK_CONTINUATION_MACD` is a separate shadow identity and must not reuse
  the baseline 76.5% calibration map.
- Authenticated `/api/trade-log` combines signal plans, underlying paper
  outcomes, real-quote option Simulation entries/exits with reasoning, and
  immutable intent history. Global broker events remain ADMIN/owner-only.
  Dynamic 9EMA option plans accept only fresh liquid 0/1DTE candidates, prefer
  the highest affordable delta, and maximize whole contracts inside all active
  capital and risk caps; long-option premium is treated as maximum order risk.
  Pending Simulation intents expire with signal validity and block when their
  source signal is canceled.
- Underlying 0DTE paper trades use session close minus five minutes, including
  half-days. Missing cutoff observations become `UNRESOLVED_DATA` and are
  visible but excluded from scored returns; material gaps block promotion.
  This partial-profit underlying model is not the v2 full-exit option lifecycle.

## Private local setup

From a checkout with the project dependencies installed in `.venv`:

```bash
.venv/bin/python scripts/local_runtime.py start
.venv/bin/python scripts/local_runtime.py create-admin
```

Open `http://127.0.0.1:8321` and sign in with the local account you create in the
terminal. The launcher generates setup tokens privately; do not paste them
into chat or the browser. Use `status`, `backup`, and `stop` with the same
launcher. Existing cloud accounts/history are not copied. On macOS the data
directory is `~/Library/Application Support/IntelliDhan/local`.

See [local setup, limits, and validation plan](docs/35-local-research-desk.md)
for dependency installation and shared-worktree commands. Real option
Simulation still requires read-only quotes from the official Robinhood MCP;
the server does not supply them automatically. Authentication and runtime tool
discovery are separate from starting the app. Follow the mandatory
[execution contract](docs/27-codex-robinhood-execution.md). This local profile
rejects Live mode even if a saved policy requests it. Ordinary hosted setup
remains in [Accounts and personal settings](docs/21-accounts-and-personal-settings.md).

## Repository change discipline

Every system-changing pass must update this `README.md` in the same commit.
Reconcile the date and last-system-pass marker, current capabilities, setup and
deployment instructions, safety boundaries, and document index as applicable.
`AGENT_CONTEXT.md` and pull-request notes supplement this README; they do not
replace the README update. CI checks each commit after this policy is present on
the base branch and rejects changes to runtime, configuration, UI, deployment,
scripts, architecture, or active system documentation that omit `README.md`.
Tracking/build boundaries such as `.gitignore`, `.dockerignore`, `.claude/`,
and `.codex/` are included; explicitly archived or decommissioned docs and
test-only/context-only commits are excluded. Rename detection is disabled for
this check so moving an active system file into an excluded location still
requires the same-commit README update.

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
| 15a | [Multi-Brain Stock Analysis](docs/15-multibrain-stock-analysis.md) | Deterministic specialist reconciliation plus isolated, advisory Claude review contract |
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
| 26 | [UI Decluttering Pass](docs/26-ui-declutter-pass.md) | Reduced Today hierarchy, removed duplicate panels, and progressive-disclosure contract |
| 27 | [Codex + Robinhood Execution](docs/27-codex-robinhood-execution.md) | Two-mode contract, highest-feasible-delta selection, maximum-threshold sizing, official MCP loop, and fail-closed execution |
| 28 | [Platform Safety &amp; Data Integrity](docs/28-platform-safety-and-data-integrity.md) | Active token, research-isolation, persistence, no-lookahead, GitOps, and deployment controls |
| 29 | [Brand System](docs/29-brand-system.md) | Approved D4.2 owl-and-lotus identity, SVG asset stack, palette, typography, usage rules, and product application |
| 30 | [SPY/QQQ 9EMA 0DTE Auto-Trader](docs/30-ema9-0dte-shadow-autotrader.md) | Exact rules, historical evidence, real-quote Simulation journal, 0/1DTE selection/sizing, trend-break exits, and Live-promotion gates |
| 31 | [Live Modules &amp; Research Cards](docs/31-live-modules-and-research-cards.md) | Auto-refresh cadence, simplified module hierarchy, and the stock-pick evidence/risk presentation contract |
| 32 | [Strategy Evidence &amp; Expectancy](docs/32-strategy-evidence-and-expectancy.md) | StrategyEvidence schema, net-expectancy gate, MACD pullback shadow identity, trend suitability context |
| 33 | [Modern Terminal UI](docs/30-modern-terminal-ui.md) | Robinhood-informed hierarchy, reduced actions, responsive overlap rules, and the two-minute single-flight refresh contract |
| 34 | [Opening Range Reversal Backtest](docs/32-opening-range-reversal-backtest.md) | Deterministic 15m opening-range reversal rules, ATR manipulation gate, prior-day level experiment, slippage-aware walk-forward diagnostics |
| 35 | [ORR Five-Year Fine-Tuning Model](docs/33-orr-five-year-fine-tuning-model.md) | SPY/SPX point-in-time data contract, interpretable meta-labeler, purged walk-forward tuning, robustness score, and promotion gates |
| 36 | [UX Signals-First Simplicity](docs/32-ux-signals-first-simplicity.md) | Signals-first Today hierarchy, density reduction, progressive disclosure; logo/colors unchanged |
| 37 | [Beta Reliability &amp; Strategy Readiness](docs/34-beta-reliability-and-strategy-readiness.md) | Free-tier limits, outage recovery, reduced polling/AI spend, horizon safeguards, and remaining live blockers |
| 38 | [September Beta Validation](docs/research/2026-09-24-beta-validation.md) | Frozen later-period SPY/QQQ results, cost sensitivity, session-block intervals, and daily benchmark comparison |
| 39 | [Local Research Desk](docs/35-local-research-desk.md) | Private durable local setup, backups, evidence boundaries, remaining broker/data prerequisites, and forward-validation plan |

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
create supervised, credential-free execution intents for OpenAI Codex using the
official Robinhood Trading MCP; real execution remains fail-closed behind explicit evidence,
allowlist, risk, owner, broker-review/confirmation, protection, and reconciliation controls.
