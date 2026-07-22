# IntelliDhan Agent Context

# 2026-07-21 — Two-mode 9EMA option lifecycle

- Independent review of checkpoint `bcbd8fd` found bearish-plan, kill-switch,
  migration, claim-time risk, entry-zone/sellout, receipt-schema, Simulation
  evidence, durable-journal, and ordering defects. The corrective pass supports
  bearish signals only as long puts; preserves the underlying entry zone;
  requires authoritative sellout time; rechecks fresh option and underlying
  quotes plus current capital/risk caps at claim; bounds claim leases; and
  revokes placement authority on Live expiry or a switch to Simulation.
- Simulation entries now require a fresh healthy two-sided official-MCP quote
  that still passes spread, volume, open-interest, sellout, selection-age, and
  underlying entry-zone gates. Broker receipts use an allowlisted schema,
  bind fill identity/quantity to the selected option, normalize prices, and
  compute exit P&L server-side. Full receipt and trade details are copied into
  immutable event rows and the UI sorts the combined journal globally newest
  first.
- Re-review then closed a stale-selection refresh dead end, kept failed exit or
  protection attempts in `EXECUTED` exposure until a `CLOSED` broker receipt,
  bound Simulation quotes to the selected option ID, reran current allowlists,
  option permissions, calibration, and concurrency policy at claim, allowed a
  data-quality-triggered Simulation exit to be recorded, and sourced the trade
  journal from immutable events so orphaned replica events remain visible.
- Continued draft PR #17 on `codex/9ema-0dte-autotrader`; no merge, deploy,
  execution-mode change, or broker order was performed.
- Auto-trade contract v2.0 now exposes only `SIMULATION` and time-limited
  `LIVE`. All pre-v2 policies migrate fail-closed to Simulation. Legacy
  in-flight broker states retain only their receipt-reconciliation path.
- Default policy is Simulation, SPY/QQQ, `EMA9_MTF_0DTE`, 0DTE module, and
  long options. Live requires `live_for_minutes` and still enforces explicit
  calibration, allowlist, health, risk, fresh-capital, claim, broker-review,
  confirmation, protection, and reconciliation gates.
- Added official-MCP option-candidate attestation. The app validates 0/1DTE,
  direction, tradability, quote age, two-sided market, ≤10% spread, volume ≥100,
  open interest ≥500, and one-contract affordability. It selects the highest
  absolute delta among eligible contracts, with spread/OI tie-breakers.
- Position size is the maximum whole-contract quantity inside the minimum of
  80% of fresh buying power, per-order dollar risk, and remaining daily dollar
  risk. Long-option debit is treated as maximum order risk. No equity fallback,
  short opening, averaging down, or market-order substitution is allowed.
- Simulation receipts log real observed option and underlying prices, selected
  contract, size, timestamps, entry/exit reason, return, and realized P&L. The
  authenticated trade log and compact Automation journal expose these events.
- The automated management plan holds the full scalp until a completed
  5-minute 9EMA break, opposing 15-minute trend, hard stop/data-quality failure,
  or broker sellout deadline; after +1R its risk reference moves to breakeven.
- Runtime Robinhood schemas were inspected. Current tools expose chain dates,
  contract IDs/tradability/sellout time, real-time option delta/spread/volume/OI,
  and single-leg review. The review tool requires the preview to be shown and
  explicitly confirmed before any placement, even when broker alerts are empty.
- `EMA9_MTF_0DTE` remains `live_eligible=false` because the existing historical
  evidence is sparse and unstable. Simulation may collect forward option-price
  evidence; Live cannot override this gate.

## 2026-07-21 — SPY/QQQ 9EMA 0DTE SHADOW monitor

- Active isolated branch/worktree: `codex/9ema-0dte-autotrader` at
  `/Users/dhanvin/Documents/IntelliDhan-9ema`, based on `origin/main` commit
  `2ce39a5`. The shared checkout has unrelated collaborator edits and was not
  modified.
- New strategy `EMA9_MTF_0DTE` watches completed 5-minute SPY/QQQ 9EMA reclaims
  only when 15-minute, 1-hour, and daily context agree; VWAP, RSI, relative
  volume, candle quality, time-of-day, profile, extension, concurrency,
  duplicate, and correlation gates remain active.
- The strategy is structurally `live_eligible=false` and
  `shadow_monitor=true`. Qualified setups persist as `SHADOW` alerts and
  underlying-level paper trades through separate research controls. They never
  reach Telegram or the Robinhood intent queue.
- The 55-day / 37-session chronological research pass tested 12 parameter
  variants on SPY/QQQ. The production candidate recorded train n=14, TP1 win
  rate 7.1%, average −0.768R; validation n=11, TP1 win rate 63.6%, average
  +0.498R. No variant met n≥30, 75% win rate, positive expectancy, and
  train/validation stability. Test remained sealed. Calibration has no buckets
  and explicitly denies live eligibility.
- Auto-trade policy adds an 80% maximum fraction of fresh Robinhood buying
  power. It is a ceiling only: risk controls may size lower and the system never
  upsizes to consume it. The app stores no broker balance or credentials.
- Execution intents now retain append-only lifecycle events. Authenticated
  `GET /api/trade-log` returns signals and paper trades; global broker events
  are ADMIN/legacy-owner-only and stored as immutable individual database rows.
  Claim now requires a fresh, machine-checked official-MCP buying-power review;
  stale, wrong-account, or over-80% reviews fail closed. Exact rules are in
  `docs/30-ema9-0dte-shadow-autotrader.md`.
- Independent review of checkpoint `6b45943` found four blockers: global broker
  event disclosure, a global-SHADOW research marker bypass, a descriptive-only
  capital ceiling, and replica-unsafe embedded audit history. The corrective
  pass redacts non-admin broker events, preserves `research_only` through every
  runner mode plus a delivery-side guard, adds the capital-review claim gate,
  and moves events to immutable unique database rows.
- Re-review found two additional edge cases: expired-lease reclaim reused an old
  capital observation, and an event from a replica-lost intent snapshot could
  be hidden by the API join. Reclaims now require another fresh review; every
  immutable event embeds bounded intent identity so orphan history remains
  visible to administrators without relying on the mutable snapshot.
- Corrective local verification: 305 tests passed (6 integration tests
  deselected), Ruff passed, and `git diff --check` is clean. PostgreSQL CI also
  exercises immutable event-row insertion and idempotency.
- Broker execution remains `OFF`. No live order, policy arming, merge,
  deployment, or branch removal is authorized by this checkpoint.

## 2026-07-18 — Codex becomes the sole Robinhood execution agent

- Claude is retained as an optional, server-side multi-brain research reviewer,
  not an execution agent. `ANTHROPIC_API_KEY` enables a structured-output review
  of the signed-in dossier's bounded evidence packet with no tools, browsing,
  MCP servers, broker data, or automation state. Claude cannot change the
  deterministic specialists, posture, rank, sizing, or execution; failures
  leave deterministic research intact. No API key is stored in the repository.
- Claude-review verification covers strict structured output, bounded evidence
  IDs, provider request shape, no tools or MCP servers, cache behavior, missing
  configuration, malformed output, invented citations, truncation, and
  endpoint-level failure isolation. The full local baseline is 284 tests passed
  (6 deselected); Ruff, both inline browser scripts, and diff checks pass.
- Independent review of `b95036f` found that the first packet selector included
  the analysis risk object wholesale, which could disclose a user-supplied risk
  budget and derived reference quantity. The selector now allowlists only
  public market-risk observations, and sentinel regressions prove personal
  capital and quantity values are excluded before the Anthropic request.
- The same review found the compact Claude disclosure had hidden its native
  marker without a replacement and referenced an undefined color token. It now
  has a visible theme-token chevron with an open-state rotation, retaining
  native summary semantics and keyboard operation.
- Repository maintenance is now explicit and tested: every system-changing pass
  must update `README.md` in the same commit, including its last-system-pass
  marker and affected capability, setup, deployment, safety, and document-index
  truth. Context and PR notes cannot substitute for the README change. CI
  enforces the same-commit rule for future changes once this bootstrap policy is
  present on the base branch. The checker fails closed on unavailable history,
  covers build/tracking and agent configuration, excludes explicitly archived
  docs plus test/context-only commits, and has temporary-repository tests for
  bootstrap, invalid-base, regular-commit, merge, and path-scope behavior.
  Rename collapsing is disabled so moving a system file into an excluded path
  cannot hide the source-path change.
- Active isolated branch/worktree: `codex/codex-robinhood-primary` at
  `/Users/dhanvin/Documents/IntelliDhan-codex-robinhood`, based on merged
  `main` commit `7620024`. The collaborators' dirty checkout is untouched.
- Active agent contract: `AGENTS.md` and
  `docs/27-codex-robinhood-execution.md`. The project-scoped, credential-free
  MCP declaration is `.codex/config.toml`.
- Auto-trade contract v1.1 requires a new
  `AUTOTRADE_CODEX_AGENT_TOKEN` bearer plus an exact
  `{"agent":"codex"}` body. The retired variable is never read; missing,
  different, or extra body fields fail closed. The default policy remains
  `OFF`.
- Loading any policy without contract version 1.1 normalizes it to Codex,
  disarms it, clears the arming window, increments its revision, and persists
  the safe state—even when a legacy policy's arbitrary agent label already
  said `codex`. SQLite and PostgreSQL persistence paths are covered.
- Existing non-Codex claims retain broker-receipt reconciliation but receive
  `cancel_requested` and `revoked_at`; they cannot be used for a new placement.
- The retired contract moved to
  `docs/decommissioned/claude-robinhood-agent-contract.md`. Historical,
  read-only daily-brief provider labels remain isolated from execution.
- Deployment cutover requires creating a new
  `AUTOTRADE_CODEX_AGENT_TOKEN` (the retired variable is ignored),
  authenticating Codex to the official Robinhood MCP, and validating `SHADOW`
  before any separately authorized supervised or armed use.
- Active platform-wide safety rules that are unrelated to Claude were retained
  in `docs/28-platform-safety-and-data-integrity.md`; the decommissioned file is
  now historical only. `GO-LIVE.md` and `ARCHITECTURE.md` describe the existing
  bridge and its gated cutover rather than calling it future or manual-only.
- Initial independent review of commit `e8d8fef` found five blockers: a reusable
  legacy bearer, incomplete persisted-policy migration, over-broad contract
  archival, a loose claim schema, and stale architecture/go-live claims. The
  follow-up hardening at `c7d64d0` cleared all five. Re-review then found one
  remaining documentation inconsistency: the core vision and risk guardrail
  still claimed every order required per-order confirmation. Docs 00, 13, and
  20 now accurately distinguish default `OFF`, non-executing `SHADOW`,
  per-intent `SUPERVISED`, and separately authorized, time-limited `ARMED` use.
  A wider follow-up sweep applied the same truth to the architecture, data
  source, UI, roadmap, and enhancement-review docs; doc 27 is authoritative,
  IntelliDhan holds no broker credentials/tools, and runtime MCP schemas are
  never assumed. Final reconciliation wording now uses durable Codex receipts
  rather than assigning a named Robinhood polling tool to an IntelliDhan
  service; tax-lot coverage follows the same runtime-discovery boundary. A
  final independent review of implementation head `129679f` found no actionable
  issues and approved the change. The reviewer confirmed the complete active
  documentation set has no remaining authoritative hard-coded Robinhood tool or
  IntelliDhan-held MCP credential/write/poller claim.
- Local verification baseline after README-policy hardening: 277 tests passed
  (6 deselected), including 25 focused contract/policy tests. Ruff passed, both
  inline scripts parsed, project MCP TOML parsed, and `git diff --check` is
  clean. Independent review of exact system head `de1a57c` found no actionable
  README-enforcement, Codex-cutover, security, or documentation issue. An
  isolated-state smoke test returned liveness 200, rejected the retired bearer
  with 401, accepted the new bearer, reported
  contract v1.1 with effective mode `OFF`, and returned 422 for a non-Codex
  claim. The PostgreSQL migration test is included for CI.
- Draft PR #13 tracks the branch:
  https://github.com/icyyd/IntelliDhan/pull/13
- GitHub CI at independently reviewed system head `de1a57c` is green: `test` and
  `account-postgres` both passed. The PR is mergeable but remains draft and
  unmerged pending explicit user confirmation.
- No merge, deployment, secret rotation, MCP authentication, policy arming, or
  broker action is authorized by this checkpoint.

## 2026-07-18 — UI decluttering pass implemented

- Active isolated branch/worktree: `codex/ui-declutter` at
  `/Users/dhanvin/Documents/IntelliDhan-ui-declutter`, based on merged `main`
  commit `3208730`. The original checkout remains untouched and contains other
  contributors' uncommitted work.
- Detailed agent-readable contract: `docs/26-ui-declutter-pass.md`.
- The Today screen was reduced from ten visible sections to a calmer
  signal-first hierarchy: combined welcome/search command surface, compressed
  SPX/SPY/QQQ strip, Top 3 with collapsed radar, signals, and conditional detail.
- Static strategy lanes, the duplicate market matrix, duplicate held-back
  drawer, empty context, and empty risk surfaces are removed or hidden.
- Automation remains visible but compact. No scoring, rank, provider,
  authentication, Robinhood, or execution behavior is in scope.
- Browser verification passed at 390, 768, 1024, 1280, and 1440 px with no
  horizontal overflow. Real SEC-backed autocomplete and AAPL levels were
  exercised; a clipped-suggestion defect was found and corrected by keeping the
  results in the hero's normal flow. Light/dark themes were checked and the
  console was clean.
- Full verification after independent review: 246 tests passed (5 deselected),
  Ruff passed, both inline
  scripts parsed, DOM IDs are unique, and `git diff --check` is clean.
- Draft PR #12 received a changes-requested engineering pass. Corrections keep
  budget-fetch failures visibly fail-closed, route command-palette tickers to
  on-demand analysis, require explicit alert selection before detail reveal,
  and make autocomplete keyboard/race behavior deterministic. The corrected
  mobile flow and pending-request cancellation were browser-tested with a clean
  console. A re-review accessibility finding now preserves the risk warning and
  Retry node across unchanged 15-second refreshes; a behavior test verifies
  focus-safe node identity.
- Final implementation head `a83f099` received an independent clean re-review
  with no actionable findings. PR #12 was merged into `main` as `7620024`; both
  PR and post-merge GitHub CI jobs (`test` and `account-postgres`) passed.

## 2026-07-18 — Multi-brain stock analysis work in progress

- Active isolated branch/worktree: `codex/multibrain-stock-analysis` at
  `/Users/dhanvin/Documents/IntelliDhan-multibrain`; the shared checkout remains
  untouched because it contains other agents' work.
- New implementation contract:
  `docs/15-multibrain-stock-analysis.md`.
- The daily-brief pattern is being adapted as three evidence-isolated specialist
  passes plus a deterministic reconciler. AI remains analysis-only and cannot
  rank, create an intent, or touch execution.
- Planned product work: dynamic SEC-backed ticker/company search, richer company
  dossier, auditable research posture, homepage welcome/search/key levels, and
  chronological no-lookahead validation.
- Historical testing must use completed adjusted bars only. Current SEC/news/
  social snapshots are explicitly excluded until point-in-time archives exist.
- No merge or deployment is authorized by this checkpoint.

### Independent review corrections

- Attention/news/social can no longer create a directional posture: `BUY` and
  `SELL` require matching technical and filed-business stances.
- An unvalidated forward context is now a critical blocker, so it cannot appear
  as a prominent directional posture.
- Regression promotion now requires held-later Brier improvement over both the
  expanding unconditional base rate and the fixed-vote benchmark.
- Alpha company overviews must match the requested symbol; SEC identity remains
  authoritative. Finnhub rows must match the symbol and fall inside a bounded,
  nonfuture seven-day window.
- Homepage company submissions resolve through SEC search, with direct-ticker
  fallback plus combobox labeling, expanded state, Arrow navigation, Enter,
  and Escape behavior.
- Re-review verification baseline: `240 passed, 5 deselected`; Ruff and inline
  script parsing clean; `git diff --check` clean. Live SEC-backed “Apple” search
  resolved to AAPL, keyboard behavior passed, and the browser console was clean.

### Backend checkpoint `multibrain-research-v1`

- Added `research_consensus.py`: isolated price/risk, business-quality, and
  catalyst/attention specialists plus deterministic `BUY` / `HOLD` / `SELL` /
  `INSUFFICIENT_EVIDENCE` research posture. Sentiment cannot override price and
  business evidence; the result cannot rank or execute.
- Trend analysis is now `trend-analysis-v3` and exposes completed-daily 9EMA,
  SMA50/200, 55-day confirmation, 20-day invalidation, 52-week range, and
  two-ATR reference levels.
- Added SEC registrant ticker/company search and SEC business identity fields.
- Signed-in arbitrary ticker dossiers now receive current SEC/news/social
  enrichment instead of restricting enrichment to the configured scan universe.
- Focused verification: 27 tests passed; ruff passed. The shared repository
  virtual environment was used read-only with this worktree's packages supplied
  through `PYTHONPATH`.

### Regression checkpoint `technical-panel-regression-v1`

- Added a chronological pooled regression harness with matured-label admission,
  non-overlapping outcomes, training-only standardization, ridge regularization,
  development-only feature-set selection, and a later validation window.
- Live 10-year configured-universe results are recorded in
  `docs/16-multibrain-validation-results.md` and JSON evidence artifacts.
- 21-session validation: core regression Brier 0.22974 vs fixed vote 0.22759
  (-0.94% relative); 2/12 symbols improved.
- 63-session validation: core regression Brier 0.22147 vs fixed vote 0.21928
  (-1.00% relative); 5/12 symbols improved.
- Both horizons are `NO_BENCHMARK_EDGE`; no regression promotion is authorized.
  Transparent fixed votes remain production-authoritative.

### Product checkpoint `dynamic-dossier-v1`

- The Today screen now has a personalized welcome, ticker/company search,
  compact on-demand levels, and a direct path to the full dossier.
- The dossier presents completed-bar trend posture separately from validated
  forward edge, key levels, company/business context, SEC financial and filing
  evidence, news, social attention, and the three specialist opinions.
- Alpha Vantage `OVERVIEW` adds a bounded business description, sector,
  industry, market cap, and valuation context when configured. These fields are
  display-only and cannot alter specialist scores or posture.
- Desktop (1280 px) and mobile (390 px) browser checks show no horizontal
  overflow. Homepage key-level search and the full AAPL dossier were exercised.
- Browser testing exposed a Yahoo adjusted-price floating-point boundary defect
  in a 10-year AAPL row. The provider now repairs only epsilon-scale OHLC noise
  and rejects materially invalid rows; regression tests cover both paths.
- Signed-out research enrichment correctly remains unavailable while technical
  analysis and levels remain usable. Authenticated provider rendering is
  covered by API/static tests; no credentials were invented for visual QA.
- No merge or deployment is authorized by this checkpoint.

**Last updated:** 2026-07-18

This is the concise handoff file for agents working on IntelliDhan. It
summarizes current implementation state and does not replace `AGENTS.md` or the
detailed documents under `docs/`.

## 1. Git and review state

- Production branch: `main`
- Merged implementation commit: `7620024` (PR #12 merge commit)
- The dependency stack landed in order on 2026-07-18: [#4](https://github.com/icyyd/IntelliDhan/pull/4),
  [#5](https://github.com/icyyd/IntelliDhan/pull/5),
  [#6](https://github.com/icyyd/IntelliDhan/pull/6),
  [#7](https://github.com/icyyd/IntelliDhan/pull/7), then
  [#9](https://github.com/icyyd/IntelliDhan/pull/9).
- The original shared checkout may contain collaborator changes. Never discard,
  reset, stage, or rewrite changes that are outside the current agent's isolated
  feature worktree.
- Every landed PR passed GitHub `test` and `account-postgres`; the cumulative
  merged tree also received an independent runtime review with no code blocker.
- Execution remains `OFF`. No merge changed the Robinhood trading boundary.
- Continue to use a fresh `codex/*` feature branch and pull request for new work.
  Do not remove branches, overwrite collaborator work, or bypass review without
  explicit user confirmation.

## 2. Product direction

IntelliDhan is a card- and alert-first stock research and signal terminal. The
Today screen prioritizes:

1. market/data state;
2. indicative SPX, SPY, and QQQ context;
3. a fail-closed deterministic Top 3 focus rank;
4. an eight-name curated radar;
5. complete risk-defined signal cards;
6. held-back reasons and source freshness;
7. optional chart detail after a plan is selected.

Charts are supporting context, not the primary home-screen hierarchy. The UI is
rounded, modern, dark-first with a verified light theme, responsive at 390 px,
keyboard accessible, and free of horizontal overflow in the tested layouts.

## 3. Implemented APIs and data contract

### `GET /api/focus`

- Requires an authenticated personal session.
- Returns indicative SPX/SPY/QQQ prices plus deterministic focus candidates.
- Ranking version: `smart-play-v1` over the configured live universe only.
- A partial configured-universe scan returns **no** Top 3 or curated rank because
  a failed constituent can change cross-sectional order.
- Yahoo fallback prices are `INDICATIVE`; its request-observation timestamp is
  not presented as an exchange price timestamp.
- Manual UI refresh forces one universe scan, then intelligence calls reuse that
  cached technical snapshot.

### `GET /api/intelligence/{symbol}`

- Requires authentication and a configured-universe symbol.
- Returns `research-rank-v1`; this is a research ordering score, not a
  probability of profit, forecast, signal, or order instruction.
- Current maximum weights:

| Pillar | Maximum weight | Source |
|---|---:|---|
| Technical | 50% | completed IntelliDhan daily scan |
| Financial | 35% | SEC EDGAR company facts |
| News | 10% | Alpha Vantage `NEWS_SENTIMENT` |
| Social | 5% | Finnhub social sentiment |

- Missing pillars are excluded and remaining weights are renormalized.
- Financial effective weight is additionally multiplied by its available count
  out of five filing metrics. One metric therefore receives only 20% of the
  maximum financial weight.
- SEC financial v1 uses annual revenue growth, gross margin, net margin,
  free-cash-flow margin, and same-period liabilities/assets.
- News is restricted by request and parser to the last seven days. Malformed,
  stale, and materially future-dated articles are excluded.
- Social is capped at 5% and never triggers a trade.

### `GET /api/dossier/{symbol}`

- Historical technical analysis remains available for arbitrary supported
  tickers.
- Signed-in configured-universe dossiers also reuse and render the intelligence
  snapshot: score coverage, financial metrics, official SEC links, recent news,
  and social-attention context.
- Fundamentals, events, estimates, and point-in-time research must remain
  separately labeled; the current filing screen is not sector-relative or a
  survivorship-safe historical factor model.

## 4. Signal-card unit contract

An alert card must make units unambiguous:

- options show expiry, strike/type, premium entry, option entry zone, and the
  estimated option-premium stop;
- stop and target levels derived from the underlying are labeled **Underlying
  stop** and **Underlying T1/targets**;
- equity cards show equity size and limit;
- every card shows action, age, validity, max dollar risk, reward versus risk,
  staged targets/trim percentages, evidence status, score, and thesis;
- score labels must not imply a guaranteed probability;
- research-only or unvalidated strategies remain visibly blocked.

## 5. AI and trading boundary

- AI curation is an explicit user action using the existing closed-schema thesis
  endpoint.
- Allowed thesis verdicts are `RESEARCH`, `WATCH`, and `AVOID`; only explicit
  `RESEARCH` or `WATCH` results can be added to the Research watchlist.
- AI cannot change deterministic rank, invent evidence, create an execution
  intent, or place an order.
- Execution defaults to `OFF`. Robinhood execution must follow `AGENTS.md` and
  `docs/27-codex-robinhood-execution.md`, use only the official Trading MCP, run
  pre-trade review, and preserve the required claim/receipt/reconciliation loop.

## 6. Latest frozen evidence

The fixed diagnostics were rerun on 2026-07-17 without parameter tuning:

- Smart momentum: 153.34% total return versus SPY 103.60%, but Sharpe was lower
  (0.880 versus 1.172) and maximum drawdown was materially worse (-43.41% versus
  -18.76%).
- SMA200, 12-month time-series momentum, Donchian 55/20, and two-of-three trend
  consensus beat buy-and-hold CAGR on 0 of 12 configured names.
- Only 1 of 12 one-month conditional forecasts had positive walk-forward Brier
  skill; no three-month context cleared validation.

Interpretation: momentum and trend remain useful candidate-ranking and risk
context. They are not evidence that the combined signal stack is live-qualified
or reliably profitable. ORB, 9EMA, RVOL, VWAP, earnings, and filing quality are
evidence families awaiting exact-path, cost-aware, out-of-sample and forward
`SHADOW` validation. LEAPS remains gated.

## 7. Configuration

Server-only optional research settings:

```dotenv
INTELLIDHAN_SEC_USER_AGENT=IntelliDhan research admin@example.com
ALPHA_VANTAGE_API_KEY=
FINNHUB_API_KEY=
```

SEC needs a descriptive contact-bearing user agent but no API key. Missing
optional providers stay visibly unscored and reduce displayed coverage. Never
expose provider secrets to the browser, reflected errors, snapshots, logs, or
this file.

## 8. Validation baseline

At merged `main` commit `7c8e2a4`:

- `222 passed, 5 deselected`;
- Ruff clean;
- both inline scripts parsed;
- `git diff --check` clean;
- desktop, 390 px mobile, light theme, and dark theme inspected;
- authenticated Today and Analyze evidence flows exercised locally;
- no browser console warnings/errors;
- GitHub `test` and `account-postgres` jobs passed for every landed PR;
- independent post-merge runtime review approved;
- Koyeb promoted the exact merge SHA and reported both deployment and service
  `HEALTHY`; public liveness, protected-route 401 boundaries, desktop/mobile
  overflow, and browser console checks passed against the live URL.

Re-run proportionate checks after each material change and update this section
only with observed results.

## 9. Important files

- `docs/25-signal-terminal-redesign.md` — detailed design, research, scoring,
  safety, backtest, and rollout contract.
- `web/index.html` — current single-file terminal UI.
- `services/gateway/intellidhan_gateway/research_feeds.py` — bounded SEC, news,
  and social clients plus research scoring.
- `services/gateway/intellidhan_gateway/app.py` — focus, intelligence, dossier,
  authentication, and gateway APIs.
- `tests/test_research_feeds.py` — data parsing, score, privacy, fail-closed rank,
  and dossier integration tests.
- `.env.example` and `koyeb.yaml` — deployment configuration contract.

## 10. Remaining priorities

1. Move any remaining literal Koyeb credentials to secret-backed references,
   rotate them through the owning services, and configure the optional research
   providers needed for multi-feed coverage. Never copy secret values into this
   file, logs, issues, or pull requests.
2. Add a licensed real-time provider router and field-level exchange timestamps.
3. Add point-in-time broad-universe, sector/peer, earnings-calendar, estimates,
   transcript-change, and valuation datasets before claiming one-stop coverage.
4. Persist research snapshots and evaluate ranking realization over time.
5. Add reliable historical options chains and production-path 0DTE/LEAPS fill
   modeling before any strategy-promotion proposal.
6. Build compare, portfolio-conflict, and durable review/journal workflows.

## 11. Checkpoint protocol

For each coherent work unit:

1. fetch and inspect current remote/working-tree state;
2. preserve collaborator changes and work only in the feature worktree;
3. update this file when context materially changes;
4. run proportionate tests and inspect the diff;
5. commit the coherent checkpoint with explicit paths;
6. push the feature branch and keep the draft PR current;
7. request independent review for material or risk-sensitive changes;
8. never merge or deploy without the user's confirmation.

## 2026-07-19 — 4A brand refresh under draft-PR review

- Branch: `codex/brand-refresh` in isolated worktree
  `/Users/dhanvin/Documents/IntelliDhan-brand-refresh`.
- Base: fetched `origin/main` at `f5ad24d`.
- The original worktree's unrelated uncommitted Workspace Agent changes were not
  modified, staged, or moved.
- Locked identity: soft-flared 4A wordmark; ID/lotus/signal monogram; burnt-orange
  bindu `#E56F2D`; midnight `#071B36`; marigold `#F6A800`.
- Brand stack: `web/assets/brand/`; design contract:
  `design-system/intellidhan/MASTER.md`; human guide:
  `docs/29-brand-system.md`.
- Product theme uses Newsreader for restrained display moments, Manrope for UI,
  and JetBrains Mono for market data. Orange is limited to brand, focus,
  selection, and primary actions; labeled green/red remain market semantics.
- Static assets are exposed through FastAPI at `/assets`.
- Independent review found and the branch now fixes three production blockers:
  the lotus geometry is closed without a color notch/cusp at 16–256px;
  light-mode small brand and semantic text meets AA on flat and tinted card
  surfaces; and external wordmark lettering is outlined so SVG/social rendering
  is font-independent.
- `social-card.png` is the deterministic 1200×630 delivery export and the web
  shell publishes it through Open Graph and Twitter metadata.
- Strategy, data, auth, multi-brain analysis, and Robinhood behavior remain out
  of scope and unchanged.
- Adobe was requested but the connector required reauthentication. Native SVG
  and CSS sources remain Adobe/Illustrator-ready when authentication is restored.
- Verification after the semantic-contrast follow-up: focused brand/UI tests
  passed (`15`); the full non-integration suite passed (`293 passed, 6
  deselected`); Ruff and diff checks passed; dark/light browser passes had no
  console or overflow regressions; and the logo was raster-inspected at 16, 24,
  34, 256, lockup, and social sizes.
- Draft PR: `#14`. The semantic badges passed the second review; the final
  follow-up darkens `text-faint` and tests it over tint-a through tint-e.
  Remaining: commit/push, wait for CI, and obtain final independent re-review.
  No merge or deployment is authorized by this pass.

## 2026-07-21 — D4.2 owl-and-lotus identity pass

- Branch: `codex/owl-lotus-logo` in isolated worktree
  `/Users/dhanvin/Documents/IntelliDhan-logo-d42`, based on `origin/main` at
  `dcb49fd`.
- The primary worktree's unrelated in-progress changes remain untouched.
- User-selected identity: D4.2 from the second owl/lotus concept board—a calm,
  slightly wider midnight oval containing a balanced parchment owl and
  three-petal lotus, anchored by the existing burnt-orange bindu.
- The 4A outlined editorial wordmark and the navy, parchment, marigold, and
  orange palette remain unchanged. The legacy `ID`/signal monogram is retired
  from production assets; its old concept board is archived reference only.
- Updated production mark, inverse mark, favicon, horizontal lockup, social SVG,
  deterministic social PNG, brand documentation, README, and regression tests.
- Independent review rejected the first vector translation for sharp owl
  wedges, a narrow enclosure, excess favicon padding, and weak raster parity
  coverage. The corrected master uses the selected D4.2 rounded brow/cheek
  contours and wider oval; the responsive favicon fills the tile at 16px; and
  the social PNG has an approved SHA-256 regression assertion.
- Strategy, data, authentication, multi-brain analysis, and Robinhood execution
  behavior remain out of scope and unchanged.
- Verification: focused brand/terminal checks passed (`43`); the full
  non-integration suite passed (`293 passed, 6 deselected`); SVG XML parsing,
  deterministic 1200×630 social export, dark/light browser rendering, and
  diff checks passed. The mark was visually inspected at 16, 24, 34, 256,
  lockup, and social sizes.
- PR status is recorded at handoff. No merge or deployment is authorized by
  this pass.
