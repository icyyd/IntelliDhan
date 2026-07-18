# IntelliDhan Agent Context

## 2026-07-18 — Codex becomes the sole Robinhood execution agent

- Repository maintenance is now explicit and tested: every system-changing pass
  must update `README.md` in the same commit, including its last-system-pass
  marker and affected capability, setup, deployment, safety, and document-index
  truth. Context and PR notes cannot substitute for the README change. CI
  enforces the same-commit rule for future changes once this bootstrap policy is
  present on the base branch.
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
- Local verification baseline after hardening: 256 tests passed (6 deselected),
  Ruff passed, both inline scripts parsed, project MCP TOML parsed, and
  `git diff --check` is clean. An isolated-state smoke test returned liveness
  200, rejected the retired bearer with 401, accepted the new bearer, reported
  contract v1.1 with effective mode `OFF`, and returned 422 for a non-Codex
  claim. The PostgreSQL migration test is included for CI.
- Draft PR #13 tracks the branch:
  https://github.com/icyyd/IntelliDhan/pull/13
- GitHub CI at independently reviewed head `129679f` is green: `test` and
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
