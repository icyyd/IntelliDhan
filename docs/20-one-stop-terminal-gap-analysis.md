# One-Stop Stock-Picking Terminal: Product and Architecture Gap Analysis

**Status:** Agent-readable implementation brief  
**Review date:** 2026-07-13  
**Scope:** Product, UX, data, analytics, strategy governance, portfolio workflow,
execution, security, persistence, operations, and documentation  
**Relationship to current work:** `trend-analysis-v2` and the first trustworthy
terminal slice are now implemented on the active feature branch. This document
does not promote any strategy or change live eligibility.

## 1. Executive conclusion

IntelliDhan is currently strongest as a **personal technical signal cockpit**:
it monitors a configured 12-symbol universe, computes multi-timeframe technical
and auction state, evaluates five strategies, shows signal/suppression cards,
persists operational and paper state, and exposes a guarded
Claude-to-Robinhood intent bridge. The on-demand stock module adds transparent
trend state, fixed-rule backtests, and a conservative forward-outcome layer.

It is **not yet a one-stop stock-picking and analysis terminal**. The implemented
technical screener, Research watchlist, and symbol dossier now establish the
start of the product loop, but comparison, fundamental/catalyst evidence,
portfolio-aware decisions, and outcome review are not complete:

```text
Discover candidates -> compare -> analyze a company -> form a thesis
-> create a watch/alert -> size against the portfolio -> act -> review outcome
```

The highest-value next step is not another indicator or strategy. It is to
complete the durable **Discover -> Analyze -> Decide -> Track** workflow with
reliable fundamental/event data, portfolio state, and evidence-aware ranking.

## 2. Current-state reality versus the specification

The repository contains an ambitious specification and a smaller working
product. Product language and navigation must reflect the implementation rather
than imply that specified modules already exist.

| Capability | Implemented reality | Material gap | Priority |
|---|---|---|---|
| Market monitor | Yahoo daily/5m bars, 12 configured symbols, MTF state, levels, profile, bar quarantine, readiness | No provider routing, real-time entitlement, dynamic broad universe, or broad-market coverage | P0 |
| Signal engine | Five technical strategies, veto wall, calibration maps, suppression audit; missing factors are excluded and weights renormalized | Zero demonstrably live-qualified strategies; production/research parity and versioned evidence promotion remain open | P0 |
| Stock analysis | Arbitrary ticker, 3/6/12m momentum, SMA200, channel, yearly-high context, risk, backtest, v2 forward validation; configured-universe dossiers can include the current research-rank snapshot | No valuation, peers, estimates/revisions, ownership, transcript change, or complete catalyst analysis | P1 |
| Discovery | Adjusted, settled-session technical EOD screener over the configured universe, explicit partial-scan failures, four presets, ranked evidence cards, saved screens, watchlists | No point-in-time broad universe, sector map, cohort-relative fundamentals, comparison, or catalyst ranking | P1 |
| Fundamentals | Current SEC EDGAR filing links and a five-metric absolute filing-quality screen with source coverage | No point-in-time snapshots, sector-relative normalization, valuation, estimates, restatement history, or return-model validation | P1 |
| News and events | Daily briefing, SEC filings, and optional bounded Alpha Vantage news tone; optional capped Finnhub social context | No reliable earnings/economic calendar, transcript changes, analyst revisions, broad catalyst model, or event lockouts | P0/P1 |
| Options | Yahoo selector exists but live composer sets `option_selector=None` | No live chains, spread/liquidity surface, IV history, expected move, payoff lab, or options P&L truth | P2 |
| Portfolio | Capital budgets only | No holdings, lots, cash, realized/unrealized P&L, exposure, correlation, earnings concentration, or broker reconciliation | P1 |
| Trade review | Durable paper records and aggregate performance | No taken/pass disposition, outcome evidence, calibration history, journal, exports, or confirmed post-deploy retention | P0/P1 |
| Execution | Credential-free intent queue with allowlists, caps, approvals, receipts, and database-backed policy/intent support | No app-owned broker session by design; production durability is unconfirmed; no portfolio-aware pre-trade conflict check | P0/P2 |
| Web app | Modern Today screen with benchmark pulse, fail-closed top-three focus rank, curated radar, rich signals, multi-feed dossier context, Discover, Analyze, 0DTE, and swings | No URL routes, comparison, portfolio, decision journal, trade log, or complete workspace preferences | P1 |
| Operations | CI, Docker/Koyeb, readiness-aware health, tests, SQLite/PostgreSQL operational store | Production persistence/backups are not configured or verified; telemetry, alerting, migrations, and deployment smoke tests remain limited | P0 |

## 3. What “one-stop” should mean

One-stop should not mean placing every possible market widget on one page. It
should mean a user can complete a stock-selection decision without leaving the
workflow or manually reconciling conflicting evidence.

### 3.1 Canonical user journey

1. **Discover:** Start from a maintained market universe, saved screen, sector,
   theme, watchlist, or current catalyst.
2. **Compare:** Rank candidates with visible pillar scores and cohort-relative
   metrics; compare up to four names side by side.
3. **Analyze:** Open a unified symbol dossier containing price/trend, historical
   forward evidence, financial quality, valuation, growth/revisions, catalysts,
   risk, peers, options, and source freshness.
4. **Decide:** Save a thesis, add to a watchlist, create a condition-based alert,
   or explicitly pass with a reason.
5. **Plan:** Translate an eligible strategy into entry, invalidation, targets,
   size, portfolio effect, and expected-event risk.
6. **Act:** Paper track by default; optionally create a supervised broker intent
   after every evidence/risk gate passes.
7. **Review:** Reconcile fills/outcomes, compare forecast versus realization,
   update calibration, and surface process mistakes separately from P&L.

### 3.2 Product principles

- **Rank, do not proclaim.** A stock-picking score orders research candidates; it
  is not a probability of profit.
- **Separate state, forecast, and strategy.** “Uptrend,” “historically favorable
  forward state,” and “eligible trade setup” are distinct claims.
- **Show the benchmark.** Every forecast and strategy metric must compare with a
  simple base rate or buy-and-hold alternative.
- **Make uncertainty operational.** Thin samples, stale inputs, and negative
  walk-forward skill change labels and allowed actions, not only footnotes.
- **Keep source provenance visible.** Every financial, estimate, event, quote,
  and forecast carries source and as-of timestamps.
- **Portfolio before order.** A good standalone stock can still be a bad next
  position because of exposure, concentration, earnings, or correlation.

## 4. Target information architecture

Replace module-first navigation with task-first navigation. Strategy horizon is
a filter within the signal experience, not the primary product hierarchy.

```text
Today
Discover
Analyze
Signals
Portfolio
Review
Settings
```

### Today

- Market/session state, data health, next material events, portfolio risk.
- Ranked “needs attention” cards: new candidates, earnings proximity, thesis
  breaks, active signals, expiring intents, and degraded data.
- Daily briefing with source-backed facts and explicit “no edge” state.

### Discover

- Saved screens and presets: quality momentum, reasonable-growth, earnings
  revisions, pullback in uptrend, fresh breakout, unusual volume, defensive
  quality, and post-earnings drift watch.
- Filter builder with a deliberately small v1 metric set.
- Sortable/virtualized result table plus compact cards on mobile.
- Bulk add to watchlist, compare, export, and create a watchlist alert.

### Analyze

One persistent symbol workspace with tabs or sections:

1. **Decision summary:** what is attractive, what can fail, evidence quality,
   data freshness, and unresolved conflicts.
2. **Forward outlook:** 21/63-session conditional odds, ticker base rate,
   interval, sample count, walk-forward skill, and benchmark.
3. **Price and technicals:** trend state, relative strength, structure, levels,
   volume, volatility, and optional charts.
4. **Business quality:** ROIC/ROE, margins, cash conversion, leverage, dilution,
   and stability.
5. **Growth and expectations:** revenue/EPS/FCF history, consensus revisions,
   surprise history, and estimate dispersion.
6. **Valuation:** current/history/peer percentiles; earnings, FCF, sales, and
   enterprise-value views appropriate to the sector.
7. **Catalysts and risks:** earnings, macro sensitivity, filings, news, insider
   events, gaps, beta, drawdown, and known thesis risks.
8. **Peers:** side-by-side cohort comparison with user-selectable peers.
9. **Options:** only when reliable chain data is present; expected move, IV
   percentile, liquidity, expiries, and payoff—not an automatic contract pick.
10. **Notes and alerts:** thesis, watch state, tags, conditions, and history.

### Signals

- Inbox and suppression tape with horizon filters: 0DTE, Swing, LEAPS, HODL.
- Strategy card includes calibration status, sample size, base rate, historical
  expectancy, current portfolio conflict, data freshness, and event risk.
- 0DTE and Swing cockpits remain useful secondary views.

### Portfolio

- Accounts/positions/lots/cash; realized and unrealized P&L.
- Exposure by sector, factor, direction, horizon, and correlated cluster.
- Earnings calendar and downside-risk concentration.
- Watchlists are separate from holdings but can be promoted into paper/model
  portfolios.

### Review

- Durable signal/trade ledger, paper versus taken outcomes, calibration,
  forecast realization, journal, process adherence, and weekly review.

## 5. Stock discovery and ranking design

### 5.1 Minimum viable universe

Start with S&P 500 + Nasdaq-100 constituents and the configured liquid ETFs.
Maintain point-in-time membership history for research. Do not call a current
constituent backtest survivorship-safe.

### 5.2 V1 screenable fields

Keep the initial set to approximately 20 trustworthy fields:

- identity: sector, industry, exchange, market cap;
- liquidity/risk: ADV dollars, beta, realized volatility, max drawdown;
- trend: price versus SMA200, relative strength versus SPY and sector, 3/6/12m
  returns, 52-week-high proximity;
- quality: ROIC, FCF margin, net-debt/EBITDA, cash conversion;
- growth: revenue/EPS/FCF growth and analyst revision direction;
- valuation: earnings yield, FCF yield, EV/EBITDA or EV/sales, five-year and
  sector percentile;
- events: earnings date, last surprise, next material event;
- evidence: forecast label, sample count, and Brier skill.

### 5.3 Candidate score

Use separate 0–100 pillars, never a hidden monolithic probability:

| Pillar | Purpose | Example inputs |
|---|---|---|
| Quality | Can the business compound safely? | ROIC, margins, cash conversion, leverage, dilution |
| Growth | Are fundamentals and expectations improving? | Revenue/EPS/FCF growth, revisions, surprise trend |
| Valuation | Is the price reasonable relative to history and peers? | Yield/multiples percentiles, sector-aware models |
| Trend | Is market behavior supportive? | Relative strength, regime, momentum, structure |
| Catalyst | Is there a dated reason for repricing? | Earnings, guidance, product/regulatory/filing events |
| Risk | What can invalidate or impair the thesis? | Volatility, drawdown, beta, gaps, balance sheet, concentration |

The UI displays every pillar, missing-data penalties, cohort, and rule version.
Presets may weight pillars differently, but weights are frozen, versioned, and
backtested as ranking models—not tuned per ticker.

## 6. Analytics and strategy improvements

### P0 correctness

1. **Remove positive “neutral” stubs.** `F6_flow` and missing macro currently use
   `60`, which can add favorable composite weight despite absent data. Missing
   factors should be excluded with weight renormalization or explicitly neutral
   at `50` and labeled unavailable.
2. **Keep unvalidated confidence fail-closed.** Preserve the pending calibration
   change that caps unvalidated confidence below the 0.75 live gate.
3. **Enforce strategy/spec parity.** Examples requiring audit:
   `DAILY_BREAKOUT` claims two-daily-close confirmation in its docstring but its
   evaluator checks only the current close; ORB volume confirmation defaults to
   disabled (`min_relvol=0`).
4. **Separate setup scoring from probability.** Composite factors may rank
   setups. Only a versioned calibration model with held-out/forward evidence may
   produce a claimed win probability.
5. **Model realistic execution.** Include spread/slippage/fees, next-open gap,
   partial fills, corporate actions, and options-specific fills before strategy
   promotion.
6. **Use robust research populations.** Point-in-time universes, delisted names,
   non-overlapping or dependence-aware confidence intervals, frozen tests,
   multiple-testing controls, and production-path replay.

### P1 usefulness

- Add relative-strength ranking against SPY and sector peers.
- Add earnings/revision and fundamental-quality features as separate research
  pillars, not technical-score boosters.
- Record forecast snapshots at decision time, then evaluate calibration and
  directional accuracy after the horizon expires.
- Add regime segmentation only as a report until enough samples prove a stable
  improvement over the unconditional benchmark.
- Maintain a model/strategy registry containing version, parameters, training
  window, validation window, universe, costs, metrics, status, and rollback.

### Do not add yet

- Per-ticker parameter optimization.
- An LLM-generated numeric stock score or price target.
- Dozens of correlated indicators.
- Options automation before chain history and portfolio reconciliation exist.
- A new live strategy merely because its in-sample CAGR is high.

## 7. Data-platform priorities

### P0: make current data honest

- Wire `sentinel.check_bars` into the live ingestion path; currently it is used
  in backfill/tests but not before live engine updates.
- Add boot/readiness state and per-source last-success/error/staleness metrics.
  `/api/health` must not return healthy before boot and fresh data complete.
- Load `config/universe.yaml` instead of maintaining separate hard-coded
  universes across live and research modules.
- Add timeouts, retries with jitter, and circuit breakers around provider calls.
- Persist bars/outcomes actually used by production, not only optional backfill.

### P1: support a real terminal

- Introduce a provider router by capability and field-level provenance.
- Add a licensed/reliable source for real-time quotes, company fundamentals,
  estimates, earnings dates, and options chains. Yahoo remains fallback and
  development history, not an accuracy-critical sole source.
- Normalize entities: security, listing, company, sector/industry, currency,
  corporate actions, fiscal periods, estimates, events, and peers.
- Add nightly cross-source reconciliation and quarantine material mismatches.
- Store point-in-time screen universes and fundamental/estimate snapshots so
  backtests cannot see revised data.

## 8. Persistence and API architecture

Remain a modular monolith for now; splitting into network microservices would
add operational complexity before the product loop is complete.

### Durable domains

```text
market_data      bars, quotes, corporate actions, source quality
discovery        universes, screens, screen runs, rankings
research         fundamentals, estimates, events, forecast snapshots
signals          candidates, suppressions, alerts, strategy versions
portfolio        accounts, positions, lots, cash, exposure snapshots
execution        intents, approvals, claims, receipts, reconciliation
learning         paper trades, real trades, outcomes, calibration versions
workspace        users, watchlists, notes, saved views, preferences
```

### Remaining persistence gaps

- `TerminalStore` now persists alerts, paper trades, briefings, watchlists,
  budgets, screens, universe metadata, automation policy, and execution intents;
  suppression/calibration observations still need normalized durable records.
- The SQLite fallback survives a local process restart but not an ephemeral
  Koyeb replacement. Production requires PostgreSQL or a mounted path, backup
  policy, and a deployment restore test.
- Market bars and indicator snapshots still use the older storage abstraction;
  consolidate migrations, ownership, and retention before broad-universe scale.
- Add schema migrations and append-only audit records before expanding broker
  actions.

### API contract

- Version APIs (`/api/v1/...`) before adding screeners and portfolios.
- Generate typed frontend contracts from Pydantic/OpenAPI.
- Use pagination/cursors for screens, news, ledger, and alerts.
- Add idempotency keys for mutations and broker-intent lifecycle operations.
- Add request IDs, structured errors, and source/as-of metadata.

## 9. Security and production hardening

These are release blockers for a public personal-finance terminal.

1. **Finish the account boundary.** Invite-only database accounts now protect
   personal signal/briefing/automation reads, mutations, and `/ws`, with
   user-scoped preferences, limits, watchlists, and screens. Add CSRF, password
   recovery/change, email verification, session management, and account
   lifecycle controls before public exposure; decide whether non-personal
   research APIs also sit behind an identity-aware proxy.
2. **Add CSRF tokens and identity-aware limits.** SameSite cookies and
   process-local IP limits are foundations, not a distributed control plane.
3. **Finish WebSocket controls.** Authentication and bounded newest-event queues
   are implemented; add connection caps, heartbeat/timeouts, and telemetry.
4. **Rate-limit expensive analysis** per identity/IP in addition to process-wide
   provider concurrency.
5. **Keep control secrets out of page state.** Normal login uses an opaque
   HttpOnly account cookie and stores only its digest. The legacy owner token is
   bootstrap/emergency compatibility; add CSRF tokens before public expansion.
6. **Run the container as a non-root user**, pin dependency versions, scan the
   image/dependencies, and enable secret scanning.
7. **Make audit logs durable and tamper-evident** for policy, approvals, intents,
   and receipts.
8. **Separate liveness and readiness.** Readiness includes successful boot,
   provider freshness, market clock, state-store access, and strategy registry.
9. **Add deployment smoke tests** for health, static UI, API schema, and one
   non-mutating analysis call before marking a revision good.

## 10. UX and frontend architecture

The current card-first visual direction is appropriate. Preserve the rounded,
modern surfaces, semantic badges, dark/light themes, progressive disclosure,
and optional charts. The improvement is primarily information architecture and
workflow continuity.

### P0/P1 UX changes

- Add a universal omnibox for ticker/company search, screens, watchlists,
  actions, and recent symbols.
- Replace hidden mobile navigation with a persistent mobile bottom bar for the
  primary tasks.
- Give every major view a URL/deep link and preserve symbol/workspace state.
- Build reusable components and routes; `web/index.html` is now a large
  all-in-one HTML/CSS/JS artifact that will become risky as the terminal grows.
- Add saved views, column presets, multi-sort, bulk actions, virtualized rows,
  export, and keyboard shortcuts to Discover/Watchlists.
- Keep charts secondary on discovery and decision-summary surfaces; make them
  full-featured inside the symbol Technicals tab.
- Show text/icons with color, clear focus states, `aria-live` errors, and a
  pause/control for streaming visual changes.
- Add an explicit evidence-state component reused across forecast, strategy,
  fundamentals, and catalysts: source, as-of, sample, status, limitation.

### Benchmark patterns worth adopting

- Koyfin joins screens, reusable watchlist views, portfolios, dashboards,
  financials, transcripts, news, and relative-performance analysis. Its useful
  pattern is saved data views reused across screens/watchlists, not unrestricted
  widget customization. [Koyfin functionality](https://www.koyfin.com/help/topic/functionality/),
  [Koyfin screens](https://www.koyfin.com/help/my-screens/)
- TradingView connects screen results, chart views, watchlists, fundamentals,
  technicals, and list-wide alerts. The key pattern is one symbol/list context
  flowing between discovery, analysis, and monitoring. [TradingView stock
  screener](https://www.tradingview.com/support/solutions/43000718866-tradingview-stock-screener-trade-smarter-not-harder/),
  [TradingView watchlists](https://www.tradingview.com/support/solutions/43000745825-mastering-the-tradingview-watchlists/)
- Finviz demonstrates the enduring value of fast combined fundamental/technical
  filtering, saved presets, multiple compact result views, and instant
  navigation. [Finviz screener help](https://finviz.com/help/screener.ashx)

Do not copy their density wholesale. IntelliDhan's differentiation should be
evidence honesty, risk-aware decision cards, and continuity from candidate to
review.

## 11. Documentation and product-truth cleanup

- Keep `README.md` and the capability matrix synchronized with shipped branch
  state; README now reflects the personal-terminal foundation.
- Update `ARCHITECTURE.md`: it describes a React/Vite frontend and behavior
  package that are not implemented as documented.
- Convert `docs/14-roadmap.md` from week estimates to capability/status gates.
- Mark every spec feature `IMPLEMENTED`, `PARTIAL`, `RESEARCH`, or `PLANNED`.
- Resolve the contradiction between the original “not an auto-trader” language
  and the later `ARMED` intent mode by documenting exactly what IntelliDhan,
  Claude, Robinhood MCP, and the user each authorize.
- Keep one generated capability matrix as the product source of truth.

## 12. Prioritized delivery plan

### Milestone A — Trustworthy personal terminal foundation (P0)

**Goal:** current features are secure, durable, observable, and honest.

- Owner authentication, protected budgets, authenticated WS, rate limits.
- Postgres persistence for alerts, paper trades, briefs, runtime config, and
  intents; migrations and backups.
- Live data sentinel, provider readiness, honest health endpoint, telemetry.
- Remove positive missing-data stubs and complete strategy/spec parity audit.
- Keep every strategy shadow-only until its versioned evidence gate passes.

**Exit:** restart/deploy loses no user or audit state; unauthenticated mutations
fail; stale/bad data cannot reach the engine; health fails when data is unready.

### Milestone B — Discover and Watch (P1)

**Goal:** users can find candidates instead of already knowing the ticker.

- Point-in-time maintained broad universe.
- V1 screener fields/presets, ranked pillars, saved screens, watchlists.
- Bulk actions, compare tray, screen-to-watchlist alerts.

**Exit:** a user can identify, compare, save, and monitor a candidate without
editing config or leaving the app.

### Milestone C — Unified symbol dossier (P1)

**Goal:** make an evidence-complete stock decision in one workspace.

- Fundamentals/estimates/events ingestion with provenance.
- Quality/growth/value/risk/catalyst sections, peers, notes.
- Integrate technical state and v2 forward evidence without conflating them.

**Exit:** every displayed claim has source/as-of; missing data is explicit; no
external spreadsheet is required for standard equity analysis.

### Milestone D — Portfolio and Review (P1)

**Goal:** connect a stock idea to actual capital and learning.

- Broker/read-only portfolio reconciliation, lots/cash/exposure/risk.
- Durable alert/trade ledger, paper/taken dispositions, outcome charts,
  calibration and forecast-realization reports.

**Exit:** position sizing accounts for current holdings and every recommendation
can be audited from evidence through outcome.

### Milestone E — Qualified signals and supervised execution (P2)

**Goal:** promote only strategies that survive the full research pipeline.

- Production-parity research, costs, point-in-time universes, forward paper.
- Strategy registry/status/rollback and portfolio conflict gate.
- Durable supervised intent execution and reconciliation.

**Exit:** at least one strategy has an approved evidence version and 30+ forward
sessions; broker receipts reconcile; kill/rollback drills pass.

### Milestone F — Options, LEAPS, and HODL depth (P2)

**Goal:** expand horizons only after equity workflow and data truth are solid.

- Reliable option chain/history, expected move, IV percentiles, payoff lab.
- HODL quality/valuation screens and thesis monitoring.
- LEAPS/collar state machines with scenario and lifecycle tests.

## 13. Success metrics

### Product

- Candidate-to-dossier open rate.
- Dossier-to-watchlist/thesis conversion.
- Median time from screen to documented decision.
- Percentage of decisions with explicit pass/track/act disposition.
- Weekly returning usage of Discover, Analyze, Portfolio, and Review.

### Evidence

- Forecast Brier skill versus base rate by horizon and universe.
- Strategy calibration error with confidence intervals and samples.
- Net expectancy after modeled and realized costs.
- Research/production trigger parity.
- Percentage of outputs labeled insufficient/unconfirmed when gates fail.

### Reliability and safety

- Stale/bad data reaching signal plane: zero.
- Unauthenticated state mutation: zero.
- Restart data loss: zero.
- Broker intent without valid evidence/portfolio/risk gate: zero.
- Alert-to-outcome audit completeness: 100%.

### UX

- Primary workflow keyboard completion.
- WCAG AA and screen-reader task completion.
- 375/768/1024/1440 layouts without horizontal page overflow.
- Discover table interaction latency and symbol dossier load time.

## 14. Recommended next implementation slice

Do not start with a large screener UI. The next cohesive slice should be:

1. owner authentication and protected budget mutation;
2. durable alerts/paper trades/runtime settings;
3. live data-quality/readiness wiring;
4. remove favorable missing-factor stubs and complete strategy parity tests;
5. normalized security/universe tables;
6. a small end-of-day screener with 12–20 fields;
7. saved watchlist -> unified symbol dossier linking the existing technical and
   forward-analysis output.

This slice converts IntelliDhan from a visually strong signal page into the
beginning of a trustworthy stock-picking terminal while preserving its main
differentiator: disciplined, explicit evidence rather than confident-looking
prediction.

## 15. Implementation status — 2026-07-13

The recommended slice now has a working vertical implementation:

- Invite-only accounts use salted scrypt password hashes, opaque HttpOnly
  SameSite session cookies, database-stored session digests, 12-hour expiry,
  and ADMIN/TRADER/VIEWER roles. Personal preferences, capital limits, saved
  screens, and watchlists are isolated by user; agent bearer-token endpoints
  remain separate. `INTELLIDHAN_OWNER_TOKEN` remains first-admin/emergency
  compatibility.
- `TerminalStore` supports PostgreSQL through `DATABASE_URL` and a local SQLite
  fallback. It persists alerts, paper trades, briefings, budgets, automation
  policy/intents, saved screens, watchlists, securities, and universe membership.
- The live loop restores operational state, boots daily history concurrently,
  applies the bar sentinel before the engine, exposes per-plane readiness, and
  returns HTTP 503 from `/api/health` until boot, provider, persistence, and
  data-quality requirements pass.
- Live engine and discovery share `config/universe.yaml`. Missing F6 flow and
  missing macro/volatility inputs are excluded with weight renormalization;
  ORB relative volume and Daily Breakout two-close contracts are enforced.
- `/api/discover` provides adjusted, settled-session EOD
  technical/liquidity/risk fields and four simple presets. Complete scans are
  cached; partial scans expose per-symbol failures and are not cached. It labels
  ranking `technical_score_v1` and marks Quality, Growth, Valuation, and
  Catalyst unavailable rather than fabricating them.
- `/api/dossier/{symbol}`, `/api/watchlists`, and `/api/screens` support the
  Discover -> Watch -> Analyze path. The card-first UI includes responsive task
  navigation, ranked evidence cards, saved screens, a durable Research queue,
  and a watch action inside the dossier.

Remaining limitations are intentional and visible:

- SQLite is restart-durable locally but not deployment-durable on an ephemeral
  container. Koyeb sets `INTELLIDHAN_REQUIRE_DURABLE_STATE=true`, so readiness
  fails until PostgreSQL is configured or a mounted SQLite path is explicitly
  declared with `INTELLIDHAN_PERSISTENT_STATE=true`. PostgreSQL is recognized as
  deploy-durable automatically.
- The screener covers the configured live universe, not yet the point-in-time
  S&P 500 + Nasdaq-100 universe.
- Fundamental, estimate, filing/news/event, peer, portfolio, and broker
  reconciliation feeds remain unconnected. The UI must continue showing those
  pillars as unavailable.
- The WebSocket and personal polling surfaces require an account (or the legacy
  administrator compatibility session). Health, calibration metadata, Discover,
  and on-demand analysis remain non-personal read APIs. Rate limiting is
  process-local until shared identity/IP limits are added. Per-user capital
  limits are durable review ceilings but do not yet resize shared signals or
  constrain shared auto-trade intents.

The next slice is Milestone B data breadth: point-in-time universe membership,
licensed fundamental/event ingestion with provenance, and cohort-relative
Quality/Growth/Valuation pillars. Do not add another strategy before that data
truth layer or promote any current strategy without its versioned evidence gate.
