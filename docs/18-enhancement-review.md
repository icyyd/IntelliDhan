# 18 — Architecture, Signal Accuracy, and UX Enhancement Review

**Status:** Agent-readable implementation brief  
**Review date:** 2026-07-12  
**Reviewed revision:** `a3f1170` plus current workspace trigger-bar parity changes
**Scope:** Current executable baseline, research/backtesting methodology, signal reliability, risk controls, and web-app experience.

## 1. Purpose and Interpretation

This document consolidates the enhancement review into an implementation-oriented brief. It is intentionally explicit about the difference between:

- what the specification intends;
- what the current runtime actually enforces;
- what the current research supports;
- what must be completed before confidence percentages can be treated as live trade-quality claims.

This is a planning and acceptance-criteria document, not a statement that the listed enhancements are already implemented. Agents must preserve the platform's central principles: human-governed, fail-closed execution under the mode-aware contract in doc 27; transparent uncertainty; conservative risk; deterministic replay; and a first-class no-trade state.

## 2. Executive Conclusion

The repository has a strong conceptual foundation:

- typed market and signal schemas;
- deterministic analytics and replay;
- explicit strategy, scoring, veto, composition, and paper-tracking stages;
- a documented calibration loop;
- a useful "silence is a feature" product philosophy;
- a coherent dark trading-workstation direction.

The specification is ahead of the executable runtime. The current application should remain analysis/shadow oriented until the following are closed:

1. research and production strategy behavior must be identical;
2. live data quality must be enforced at the signal gate;
3. option signals must be validated and composed using executable option data;
4. risk, cooldown, correlation, and daily-loss controls must be connected to real state;
5. calibration must expose uncertainty, sample size, provenance, and net expectancy;
6. the web app must distinguish research evidence from live forward evidence.

`PULLBACK_CONTINUATION` has promising direct-H1 research evidence, but the corrected production-path replay materially contradicts it. The strategy must be **disabled from gated/live delivery and returned to research/shadow** until the signal populations and lifecycle rules are reconciled. The historical 76.5% map is not currently valid for production-path alerts.

## 3. Verified Baseline

### 3.1 Test status

At the latest workspace state:

- 71 offline unit/replay tests pass, including trigger-bar identity and risk-wiring regression tests;
- 4 network integration tests are deselected by the default test configuration;
- no test failures were observed.

The suite verifies analytics mechanics well, but it does not yet prove live trade validity, option execution realism, or full risk-control wiring.

### 3.2 Refreshed SW15 research result

The fixed `PULLBACK_CONTINUATION` SW15 configuration was re-run without parameter retuning over 12 liquid symbols using approximately two years of 1H data.

| Split | n | TP1 before stop | Average R | Profit factor |
|---|---:|---:|---:|---:|
| Train | 247 | 75.7% | +0.037R | 1.15 |
| Validation | 122 | 77.9% | +0.047R | 1.21 |
| Test | 98 | 76.5% | +0.037R | 1.16 |

Interpretation:

- the point estimates are stable across the three chronological regions;
- the test result is encouraging, but the edge is economically thin;
- the approximate 95% Wilson interval for the test hit rate is 67.2%–83.8%;
- a 76.5% point estimate therefore does not establish, with high statistical certainty, that the true rate exceeds 75%;
- +0.037R average expectancy and PF 1.16 can be erased by options spreads, slippage, latency, fees, or implementation mismatch;
- the sample is cross-sectionally correlated because the symbols share market and technology-factor exposure;
- the period is predominantly bullish and the strategy is long-only.

### 3.3 Post-parity production-path replay — blocking result

Before the trigger-bar identity correction, the 55-day gated replay emitted only two alerts. That result was not representative because the strategy was reading the 5m rollup-trigger bar rather than the completed H1 bar.

After the trigger-bar correction but before lifecycle controls, the same 12-symbol, 55-day production-path replay processed 33,696 5m bars and produced:

- 29 emitted setups;
- 29 composed alerts;
- 24 decided paper outcomes;
- 41.7% positive outcomes;
- -0.455R average realized result;
- 0.22 profit factor;
- 14 initial stops, 4 full target sequences, and 6 TP1-then-breakeven outcomes.

The current workspace subsequently wires open/close lifecycle updates into the backtest/live loop and adds duplicate, correlation, and concurrency gates. With those controls active, the latest replay produced:

- 16 emitted/composed alerts;
- 12 decided outcomes;
- 58.3% positive outcomes;
- -0.262R average;
- 0.37 profit factor;
- 5 initial stops, 2 full target sequences, and 5 TP1-then-breakeven outcomes;
- 9 duplicate, 7 correlation, and 4 concurrency suppressions.

This remains a hard promotion blocker. The direct-H1 research population and the production-path population are not equivalent, so the current 76.5% flat calibration must not gate or label these production alerts. Lifecycle controls are necessary but do not explain the full discrepancy.

Remaining parity work must compare, setup by setup:

- provider-fetched H1 bars versus H1 bars rolled from the production 5m stream;
- direct daily context versus production-derived daily context;
- research one-open-trade lifecycle versus production alert lifecycle;
- exact decision and first-executable-entry timestamps;
- suppression/veto context;
- data adjustments, session boundaries, and partial final-hour bars.

## 4. Priority Work Queue

| Priority | Work item | Required outcome |
|---|---|---|
| P0 | Disable current PULLBACK gating | Remove the current 76.5% calibration from live/gated eligibility until full-path parity and revalidation are complete. |
| P0 | Full research/production parity | Trigger-bar identity is fixed in the current workspace; next reconcile direct-H1 and 5m-rollup setup sets, lifecycle, and executable entry timing. |
| P0 | Live data-quality gate | No alert can publish when a required input is stale, gapped, degraded, quarantined, or missing. |
| P0 | Remove silent instrument substitution | An option strategy cannot silently become an equity alert when the option chain/quote is unavailable. |
| P0 | Complete risk-state wiring | Open/close, duplicate, correlation, and concurrency wiring exists in the current workspace; add daily loss, outstanding dollar risk, cooldown outcomes, buying power, and restart recovery. |
| P0 | Honest evidence vocabulary | Historical base rate, calibrated confidence, forward-paper rate, and live-taken rate are separate fields and labels. |
| P1 | Locked research protocol | Calendar splits and candidate selection are frozen before test evaluation; the test set is evaluated once for one frozen candidate. |
| P1 | Net-expectancy gate | POP-based strategies require positive conservative expectancy after execution costs, not high hit rate alone. |
| P1 | MACD-resumption shadow experiment | Run the frozen positive-1H-MACD refinement in forward shadow; do not replace SW15 or alter its calibration yet. |
| P1 | Terminal trade horizon | Every research and paper trade resolves by stop, target, thesis exit, or configured time exit. |
| P1 | Reproducible research artifacts | Each result records data window, source, hashes, universe, parameters, analytics version, and code revision. |
| P1 | Decision-workstation UI | Replace the monitor page with chart-centered decision context, an evidence-aware alert rail, and complete degraded/empty states. |
| P2 | Option-level historical replay | Validate option selection, entry fills, exits, Greeks, spread, and slippage with historical quote/chain data. |
| P2 | Calibration and drift dashboard | Render claimed vs realized curves with intervals, sample sizes, versions, and forward/live segmentation. |

## 5. Signal Accuracy Enhancements

### 5.1 Trigger-bar identity fixed; full-path parity remains open

The original reviewed flow derived H1 state from 5m rollups but evaluated pullback geometry against `state.last_bar`, the 5m bar that caused the rollup. The current workspace now:

- stores the last completed bar in each `TrendEngine`;
- exposes `state.trigger_bar(timeframe)`;
- evaluates `PULLBACK_CONTINUATION` against the completed H1 bar;
- includes H1 and daily trigger-bar identity regression tests.

This closes the known OHLC identity defect. It does not establish full research/production parity: the corrected production replay performs far below the direct-H1 research result.

Required design:

```text
5m bar closes
  -> rollup emits completed H1 bar
  -> state stores completed bar by timeframe
  -> runner invokes H1 strategies with trigger_bar=completed_h1
  -> setup records decision timestamp and trigger-bar close
  -> execution model enters at first eligible quote after decision time
```

Acceptance criteria:

- every strategy receives an explicit `trigger_bar` and the current regression tests remain passing;
- `trigger_bar.timeframe == strategy.trigger_tf`;
- research, replay, backtest, shadow, and live paths call the same evaluation function;
- a multi-session parity artifact produces identical candidate timestamps and geometry from fetched-H1 and rolled-5m inputs within documented provider tolerances;
- the research and production harnesses enforce identical one-open/duplicate lifecycle rules;
- entry simulation does not use a price available before the signal decision time.

### 5.2 Make validation protocol structurally honest

The research process should be split into separate commands or stages:

1. **Development:** explore hypotheses using training data only.
2. **Selection:** rank frozen candidates on validation data.
3. **Freeze:** persist exactly one candidate, parameters, data boundaries, and code revision.
4. **Test:** evaluate only the frozen candidate once on the untouched test period.
5. **Forward shadow:** run for a configured number of sessions without alerting the user as production eligible.
6. **Promotion review:** require statistical, economic, execution, and regime evidence.

Required changes:

- define split dates from the market calendar before generating trades;
- do not derive boundaries from dates on which a strategy happened to trade;
- do not return test metrics for every candidate during parameter selection;
- require the frozen candidate to pass explicit test gates;
- preserve rejected candidates and negative results for auditability;
- use block bootstrap or clustered uncertainty estimates by date and symbol group;
- report results by year, symbol, regime, weekday, and entry time, not only aggregate totals.

### 5.3 Separate accuracy concepts

Do not overload one field named `confidence`. Use an evidence object such as:

```jsonc
{
  "evidence_status": "HISTORICAL_OOS",
  "probability_kind": "STRATEGY_BASE_RATE",
  "point_estimate": 0.765,
  "interval_95": [0.672, 0.838],
  "sample_size": 98,
  "profit_factor": 1.16,
  "avg_r_pre_cost": 0.037,
  "avg_r_net_cost": null,
  "forward_paper_n": 2,
  "live_taken_n": 0,
  "calibration_version": "...",
  "data_window": {"from": "...", "to": "..."},
  "regime_coverage": ["BULL_DOMINANT"],
  "limitations": ["UNDERLYING_ONLY", "LONG_ONLY", "OPTION_COSTS_NOT_MODELED"]
}
```

Recommended evidence statuses:

- `UNRESEARCHED`
- `IN_SAMPLE_ONLY`
- `HISTORICAL_OOS`
- `FORWARD_PAPER`
- `LIVE_LIMITED`
- `LIVE_VALIDATED`
- `DEMOTED`
- `DISABLED`

### 5.4 Replace flat calibration with staged calibration

The current `PULLBACK_CONTINUATION` map has one `0–100` bucket. This applies the same 76.5% estimate to every raw composite score. Until score-conditional evidence exists, call this a **strategy historical base rate**, not composite-calibrated confidence.

Progression:

1. historical strategy base rate with uncertainty;
2. forward-paper base rate;
3. score-conditional calibration only after sufficient samples across score regions;
4. regime-conditional or hierarchical calibration when effective sample size supports it;
5. live-taken calibration displayed separately from unbiased paper calibration.

Calibration acceptance criteria:

- every displayed estimate shows `n` and interval;
- data and analytics versions are visible;
- insufficient buckets are visibly marked and cannot imply precision;
- missing factors do not receive an unexplained positive neutral score;
- confidence does not increase merely because required timeframes or feeds are absent;
- drift checks use uncertainty and a minimum effective sample size.

### 5.5 Gate POP-based strategies on economic value

`pop_based` strategies may legitimately have less than 2:1 nominal reward/risk, but skipping the directional R:R gate must not mean that hit rate carries the entire decision.

Require:

```text
expected_net_R
  = P(win) * average_win_R
  - P(loss) * average_loss_R
  - spread_R
  - slippage_R
  - fees_R
  - adverse_selection_buffer_R
```

The strategy passes only when a conservative lower-bound estimate of `expected_net_R` is positive and above a configurable safety margin.

### 5.6 Resolve every trade

Research and paper trades must include a maximum strategy horizon. For Swing strategies this should reflect the documented 2–20 trading-day lifecycle.

Terminal states should include:

- `STOPPED`
- `TP_PARTIAL_BE`
- `TP_FULL`
- `THESIS_EXIT`
- `FLATTENED_TIME`
- `EXPIRED_UNFILLED`
- `CANCELLED_DATA`

Open trades at the dataset boundary must be disclosed separately and tested for sensitivity; they must not disappear silently from denominators.

### 5.7 Validate the traded instrument

Underlying OHLC outcomes are useful for signal research but insufficient for an options product.

Required option replay inputs:

- contract chain visible at decision time;
- bid, ask, quote age, volume, and open interest;
- IV and Greeks or reproducible estimates;
- first executable quote after signal time;
- entry and exit slippage;
- contract selection rule and fallback behavior;
- corporate actions and expiry settlement;
- no-fill and partial-fill outcomes.

An option strategy with unavailable executable option data should be suppressed or labeled analysis-only. It must not silently emit an equity order plan.

## 6. Risk and Runtime Architecture Enhancements

### 6.1 Enforce data quality in the live signal path

Each composite state should carry:

- source per input;
- event timestamp;
- fetched timestamp;
- age;
- quality status;
- missing required inputs;
- analytics version;
- provider disagreement status.

The veto wall should receive one `DataEligibility` verdict before scoring. No strategy may bypass it.

### 6.2 Connect controls to actual lifecycle events

Implement a risk ledger driven by alert, tracking, fill, milestone, and settlement events.

It must reserve and release:

- outstanding planned risk;
- taken-position risk;
- module capital;
- correlated-cluster exposure;
- daily loss utilization;
- buying power.

It must update:

- open counts;
- consecutive stop counts;
- cooldown expiration;
- daily kill switches;
- strategy and symbol duplicate guards;
- behavior-plane multipliers.

The UI and engine must read the same canonical risk state.

### 6.3 Publish a capability/status manifest

The architecture specification is a target architecture, while the runtime currently implements a subset. Add a machine-readable capability manifest that distinguishes:

- specified;
- implemented;
- tested offline;
- shadow enabled;
- live enabled;
- degraded;
- unavailable.

This manifest should drive UI labels and prevent aspirational documentation from becoming an implied live guarantee.

## 7. UX Product Direction

### 7.1 Design goal

The experience should feel like an **institutional decision workstation with consumer-grade clarity**.

It should not feel like:

- a generic KPI dashboard;
- a collection of equally weighted cards;
- a neon crypto terminal;
- a decorative glass marketing page;
- an automated prediction machine.

Visual sophistication should come from information hierarchy, alignment, typography, state transitions, and excellent chart integration.

### 7.2 Command-center layout

```text
┌ Market tape · Session · Next event · Data health · Command palette ┐
├──────┬───────────────────────────────┬─────────────────────────────┤
│ Nav  │ Primary chart + market state  │ Decision queue              │
│      │                               │ Eligible / Watch / Expired  │
│      ├───────────────────────────────┤                             │
│      │ Levels · Breadth · Profile    │ Active plans + risk usage   │
├──────┴───────────────────────────────┴─────────────────────────────┤
│ Suppression summary · Calibration health · Paper performance       │
└────────────────────────────────────────────────────────────────────┘
```

Rules:

- the primary chart owns the largest visual area;
- the decision rail stays visible beside chart context;
- risk state is persistent and cannot be hidden behind navigation;
- the bottom analysis drawer is collapsible;
- alert expansion must not remove the chart from view;
- manual order staging uses an explicit confirmation surface; automation-policy
  changes use a separate high-friction policy dialog with mode consequences;

### 7.3 Information architecture

Primary destinations:

- Command Center
- 0DTE
- Swings
- LEAPS
- HODL
- Trade Log
- Research & Calibration
- Briefings
- Settings

Global commands:

- symbol search;
- strategy search;
- jump to active alert;
- open command palette;
- change workspace;
- toggle density;
- inspect data health;
- activate focus mode.

Recommended saved workspaces:

- Command Center
- Intraday Focus
- Swing Review
- Research Review
- Weekly Performance

### 7.4 Visual design system

Recommended semantic direction:

| Role | Direction |
|---|---|
| Canvas | Deep blue-black, visually quieter than pure black |
| Primary surface | Opaque blue-gray-black panel |
| Elevated surface | Slightly lighter surface with clear 1px border |
| Selection | Cyan/blue, reserved for navigation and focus |
| Discipline/evidence | Muted gold, used sparingly |
| Bullish | Teal-green plus arrow/text label |
| Bearish | Warm red plus arrow/text label |
| Warning | Amber plus warning icon/text |
| Disabled/degraded | Desaturated slate with explicit reason |

Typography:

- Inter or Geist for interface text;
- JetBrains Mono for prices, timestamps, quantities, R multiples, and aligned statistics;
- tabular numerals everywhere live values align;
- compact type scale with strong weight and spacing hierarchy;
- avoid decorative serif typography inside the workstation.

Shape and elevation:

- 10–12px card radii;
- 6–8px controls and chips;
- hairline borders with visible dark-mode contrast;
- subtle shadow only for floating layers;
- glass/blur limited to sticky navigation, command palette, tooltips, and drawers;
- dense data cards remain opaque.

Icons:

- use one consistent SVG icon family such as Lucide;
- do not use emoji as core interface icons;
- icon-only buttons require accessible names and tooltips;
- use stable color/opacity hover feedback without scaling layout.

### 7.5 Decision queue

Replace the undifferentiated alert feed with a stateful queue:

- `WATCH`
- `ELIGIBLE`
- `EXECUTABLE`
- `TRACKING`
- `STALE`
- `EXPIRED`
- `SUPPRESSED`

Default sorting should consider:

1. execution eligibility;
2. validity remaining;
3. data health;
4. evidence tier;
5. risk availability;
6. strategy priority.

Filters should include module, symbol, strategy, status, evidence status, and risk fit.

### 7.6 Alert-card anatomy

Collapsed card—comprehensible in five seconds:

1. status and age;
2. symbol, strategy, direction, and vehicle;
3. entry zone, stop, first target, and current live price;
4. maximum dollar risk and available risk budget;
5. evidence status, point estimate, interval, and sample size;
6. one-sentence thesis;
7. validity countdown;
8. primary action.

Expanded card:

- scaled price ladder;
- factor/evidence attribution;
- trend matrix with component explanations;
- invalidation and management plan;
- regime match;
- data provenance and freshness;
- cost assumptions;
- historical and forward evidence;
- complete risk list;
- chart and log links.

For `PULLBACK_CONTINUATION`, render an explicit strategy-character chip:

> High hit rate · Modest payoff · Cost sensitive · Forward paper

Do not use a gold radial dial as the primary confidence representation. A radial dial visually implies more certainty than the evidence supports.

### 7.7 Evidence panel

Every strategy and alert should expose:

- evidence status;
- historical OOS point estimate;
- 95% interval;
- sample size;
- profit factor;
- average R before and after modeled costs;
- forward-paper sample and realized rate;
- live-taken sample and realized rate;
- last calibration date;
- data and analytics version;
- known regime limitations.

Recommended charts:

- bullet chart for claimed versus realized rate;
- line chart with confidence band for calibration through time;
- waterfall chart for gross edge to net expectancy after costs;
- small multiple by regime or calendar year;
- labeled data table alternative for accessibility.

### 7.8 Strategy-specific chart treatment

For the Swing pullback strategy show:

- completed 1H candles;
- daily trend state;
- 9, 21, and 50 EMA lines;
- shaded 21/50 EMA pullback zone;
- trigger candle annotation;
- decision timestamp;
- first executable entry quote;
- initial stop and target zones;
- maximum holding horizon;
- live thesis-health state.

Candles, annotations, levels, and alert cards must share the same price and timestamp data.

### 7.9 Progressive disclosure

Use three levels:

1. **Triage:** collapsed card for immediate decision context.
2. **Plan:** inline card expansion for execution, risk, and management.
3. **Research:** side drawer for methodology, calibration, and historical detail.

Avoid informational modals. Preserve the chart and current market context while inspecting details.

### 7.10 Modern interaction and motion

Motion must communicate state:

- alert arrival: 150–200ms fade/slide;
- live price update: brief background tint without layout movement;
- price entering the entry zone: marker transition and state-label update;
- data becoming stale: desaturation plus disabled actions and reason;
- drawer/palette entrance: 180–220ms ease-out;
- no blinking prices, persistent glows, parallax, or scroll-jacking;
- respect `prefers-reduced-motion` everywhere.

### 7.11 Command palette and keyboard workflow

Add `Cmd/Ctrl+K` for:

- symbols;
- strategies;
- alerts;
- settings;
- workspaces;
- common actions.

Preserve documented shortcuts and add a discoverable shortcut guide. All functionality must remain keyboard accessible with visible focus indicators.

### 7.12 System states

Implement polished states for:

- analytics warmup;
- no candidate;
- candidate suppressed;
- no executable instrument;
- reconnecting;
- stale/degraded data;
- provider disagreement;
- unavailable calibration;
- insufficient sample;
- risk budget exhausted;
- cooldown active;
- market closed;
- unexpected server error.

The no-trade state should summarize:

- how many candidates were evaluated;
- the top three rejection reasons;
- affected symbols/strategies;
- the next evaluation time;
- whether the limitation is market, evidence, data, or risk related.

### 7.13 Responsive/mobile experience

Mobile is a focused companion, not a compressed desktop terminal.

Priorities:

1. alerts and active plans;
2. briefing;
3. single-symbol chart;
4. trade log;
5. data/risk health.

Guidelines:

- bottom navigation for primary destinations;
- one chart at a time;
- card representations for wide evidence tables;
- sticky plan summary;
- track/pass gestures may be used;
- manual order staging and `SUPERVISED` intents require explicit confirmation;
  separately authorized, time-limited `ARMED` follows doc 27 and may execute an
  eligible intent without per-intent approval;
- test at 375px, 390px, 768px, 1024px, and 1440px;
- no unintended horizontal page scrolling.

### 7.14 Accessibility

Required:

- WCAG AA contrast minimum;
- state is never communicated by color alone;
- text/icon equivalents for bullish, bearish, warning, and degraded states;
- complete keyboard navigation;
- visible focus rings;
- ARIA-live for new alerts and material risk/data changes;
- accessible names for icon-only controls;
- chart data-table alternatives;
- reduced-motion support;
- semantic headings, landmarks, buttons, and tables;
- screen-reader-friendly error and reconnect messages.

### 7.15 Frontend performance

Retain or strengthen the documented budgets:

- initial JavaScript ≤ 350 KB gzipped where practical;
- charts loaded per route/workspace;
- WebSocket-to-paint target < 150ms;
- route transitions < 100ms after initial load;
- virtualize alert, suppression, and trade-log lists;
- avoid re-rendering full charts for every quote tick;
- no layout shift during font or live-data updates;
- Lighthouse ≥ 90 for performance and accessibility on the command center.

## 8. Recommended Delivery Sequence

### Phase A — Truth and parity

- fix trigger-bar identity;
- create cross-harness parity tests;
- freeze calendar-based research splits;
- add terminal holding horizons;
- separate historical base rate from calibrated confidence;
- add evidence/provenance schema.

### Phase B — Live eligibility and risk

- enforce live data quality;
- connect risk ledger and lifecycle controls;
- eliminate silent equity fallback;
- add cost-aware expectancy gating;
- add forward-paper eligibility state.

### Phase C — Workstation shell

- introduce React application shell and design tokens;
- implement command bar, navigation rail, chart workspace, and decision queue;
- add responsive layouts and system states;
- migrate the current monitor data into typed stores.

### Phase D — Decision components

- build evidence-aware alert cards;
- add price ladder and Swing chart treatment;
- implement suppression summaries;
- add risk/data-health surfaces;
- add command palette and keyboard workflow.

### Phase E — Research and calibration experience

- implement evidence panel and calibration charts;
- add strategy/regime/year breakdowns;
- show gross-to-net expectancy waterfall;
- expose research artifacts and version history;
- add demotion and drift state.

### Phase F — Option execution realism

- ingest historical option quote/chain data;
- replay selection and fills;
- calculate net expectancy;
- update promotion criteria;
- only then enable executable option alerts for qualified strategies.

## 9. Agent Implementation Rules

Agents working from this document must:

- inspect current code before assuming a spec item is implemented;
- keep research, replay, shadow, and live computations on the same strategy path;
- add tests for every new gate and lifecycle transition;
- never improve reported accuracy by weakening labels, excluding unresolved losses, or selecting on test data;
- preserve existing user changes and unrelated worktree edits;
- avoid silent fallbacks that change instrument or risk;
- expose missing evidence in both schema and UI;
- implement risk/data-disabled states before order-stage actions;
- use consistent SVG icons rather than emoji UI controls;
- maintain accessibility and reduced-motion behavior while adding visual polish;
- update docs and capability status whenever implementation state changes.

## 10. Definition of Done

The enhancement program is complete only when:

1. the same recorded data produces identical raw signals in research and production paths;
2. every alert contains an evidence object with provenance, interval, and sample size;
3. no stale/degraded required input can produce an executable alert;
4. no option strategy silently falls back to equity;
5. every trade resolves to a documented terminal state;
6. cost-aware expected net R is available for POP-based strategies;
7. risk and correlation controls update from real lifecycle events;
8. the command center presents chart, decision, risk, evidence, and data health together;
9. degraded, insufficient-evidence, no-trade, and reconnect states are fully designed;
10. responsive, keyboard, screen-reader, and reduced-motion checks pass;
11. performance budgets and deterministic replay tests pass;
12. forward-paper evidence is accumulated before any strategy is labeled live validated.

## 11. Primary Code and Specification Touchpoints

- Architecture target: `docs/01-architecture.md`
- Signal and calibration contract: `docs/03-signal-engine.md`
- Swing strategy specification: `docs/05-module-swings.md`
- Strategy governance: `docs/08-strategy-library.md`
- Alert and sizing contract: `docs/09-alerts-and-sizing.md`
- Trade outcomes and metrics: `docs/10-trade-log.md`
- Existing UI specification: `docs/11-ui-ux.md`
- Guardrails: `docs/13-risk-and-compliance.md`
- Roadmap: `docs/14-roadmap.md`
- Current dashboard: `web/index.html`
- State and rollups: `services/engine/intellidhan_engine/state.py`
- Strategy runner: `services/engine/intellidhan_engine/runner.py`
- Strategy implementations: `services/engine/intellidhan_engine/strategies.py`
- Factor model: `services/engine/intellidhan_engine/scoring.py`
- Veto wall: `services/engine/intellidhan_engine/veto.py`
- Calibration map: `services/engine/intellidhan_engine/calibration.py`
- Swing research harness: `services/learning/intellidhan_learning/research_swing.py`
- Production-path backtest: `services/learning/intellidhan_learning/backtest.py`
- Paper executor: `services/learning/intellidhan_learning/paper.py`
- Live host: `services/gateway/intellidhan_gateway/live.py`
- Current SW15 evidence: `config/calibration/PULLBACK_CONTINUATION.json`

## 12. Profitability Refinement Research

> **Production-path blocker:** After the trigger-bar identity and lifecycle-control fixes, the latest 55-day production replay returned 58.3% positives, -0.262R average, and PF 0.37 on 12 decided trades. The refinement results below establish research hypotheses only. Neither the baseline nor the MACD variant may be promoted until direct-H1 and production-rollup signals reconcile setup by setup.

### 12.1 Objective and decision rule

The refinement objective is **higher net expectancy with controlled drawdown**, not a cosmetically higher win rate. A change is considered production-improving only if it:

- preserves a sufficient sample in training and validation;
- does not conceal a lower hit rate behind wider reporting tolerances;
- improves expectancy after an explicit cost stress;
- remains stable across chronological partitions;
- has confirmation evidence outside the symbol set used to select it;
- does not depend on a single symbol or correlated cluster;
- uses the same trigger bar and execution assumptions as production.

The controlled screen used:

- 482 calendar sessions;
- the existing 12-symbol development universe;
- fixed calendar boundaries, independent of trade occurrence;
- 55% training, 25% validation, and 20% test partitions;
- a maximum holding horizon of 140 H1 bars, approximately 20 trading days;
- pessimistic stop-first resolution when stop and target occur in the same H1 bar;
- a 0.05R per-trade execution-cost stress;
- 27 purpose-driven variants;
- test evaluation only for the baseline and the frozen selected candidate.

Development eligibility was predeclared as:

```text
training n >= 75
validation n >= 50
training TP1-before-stop >= 75%
validation TP1-before-stop >= 75%
training average R after 0.05R cost > 0
validation average R after 0.05R cost > 0
```

No tested refinement satisfied every eligibility condition. The later corrected production-path replay also failed decisively. Therefore, this research does **not** authorize a production strategy, alert, or calibration change.

### 12.2 Refinements tested

The screen tested isolated changes to:

- maximum initial risk distance in ATR units;
- minimum H1 ADX;
- positive H1 MACD histogram confirmation;
- RSI ceilings;
- relative-volume floors;
- candle close-location value;
- bullish candle requirement;
- maximum extension above the 9EMA;
- T1/T2/T3 ATR targets;
- stop distance;
- tranche allocation;
- breakeven timing;
- locking +0.1R after T1;
- combinations of risk-distance and momentum filters.

The screen intentionally avoided changing many dimensions at once. Large combinations can manufacture attractive historical results without identifying a stable edge source.

### 12.3 Target and stop experiments

The existing research harness showed the expected trade-off:

| Variant | Main effect | Test observation | Decision |
|---|---|---|---|
| Baseline `0.8/1.6/2.8`, stop `1.0 ATR` | Highest consistency with the 75% product goal | Approximately 78.1% TP1, +0.044R, PF 1.20 in the original fixed split | Retain as baseline |
| Targets `0.85/1.70/3.00` | Slightly more payoff | Test expectancy improved modestly, but training TP1 fell below 75% | Shadow research only |
| Targets `0.90/1.80/3.15` | More payoff per winner | Test average R and PF improved, but training TP1 was about 72% and cost-stressed development expectancy was not robust | Reject for current high-confidence class |
| Targets `1.0/2.0/3.5` | Highest payoff among the simple target sets | Higher test expectancy, but training/validation TP1 were about 72% | Separate lower-hit strategy hypothesis, not a SW15 refinement |
| Stop `0.75 ATR` | Better nominal reward/risk | Validation/test improved in some partitions, but training stability and hit-rate requirements failed | Do not promote |
| Stop `0.5 ATR` | Strong nominal R multiples | Unstable across partitions; materially lower training hit rate | Reject |

Conclusion: widening targets or tightening the stop can make the historical payoff distribution look better, but the improvement is purchased with lower or less stable signal accuracy. Do not change SW15's production targets or stop based on this evidence.

### 12.4 Exit-management experiments

Increasing the runner allocation from 34% to 50%, changing the first two tranches, delaying breakeven until T2, and locking +0.1R after T1 did not produce a robust improvement after the 0.05R cost stress.

Observed pattern:

- a larger runner did not compensate for the strategy's small gross expectancy;
- locking +0.1R helped validation but remained negative after cost in training;
- delaying breakeven increased giveback risk;
- front-loading T1 improved emotional smoothness more than economic expectancy.

Decision: retain the current one-third management plan for the baseline. Treat alternative exit policies as separate shadow variants with independent evidence, not as live tuning knobs.

### 12.5 Positive H1 MACD histogram — research improvement potential

The most promising refinement was requiring:

```text
H1 MACD histogram > 0 on the completed trigger bar
```

This is economically coherent with the strategy thesis: the pullback has touched value, price has reclaimed the 9EMA, and short-term momentum has resumed in the direction of the daily trend.

On the original 12-symbol development universe:

| Partition | n | TP1 rate | Gross avg R | Avg R after 0.05R | Gross PF | PF after 0.05R |
|---|---:|---:|---:|---:|---:|---:|
| Train | 70 | 80.0% | +0.1338R | +0.0838R | 1.669 | 1.399 |
| Validation | 47 | 80.9% | +0.1365R | +0.0865R | 1.713 | 1.430 |

The direction and magnitude were consistent, but the candidate missed the predeclared sample floors by 5 training trades and 3 validation trades. The threshold was not relaxed after seeing the result.

The filter was then frozen and tested on a separate, more sector-diverse confirmation universe:

```text
IWM, DIA, XLF, XLE, XLV, XLI, COST, JPM, UNH, AVGO
```

Test-partition comparison on that separate universe:

| Strategy | n | TP1 rate | Gross avg R | Avg R after 0.05R | Gross PF | PF after 0.05R |
|---|---:|---:|---:|---:|---:|---:|
| Baseline | 85 | 68.2% | -0.0173R | -0.0673R | 0.938 | 0.770 |
| Positive H1 MACD | 32 | 68.8% | +0.0798R | +0.0298R | 1.336 | 1.118 |

The frozen MACD filter improved test net expectancy by approximately +0.097R per trade relative to the baseline on the confirmation universe. Across the entire confirmation-universe history, it improved cost-stressed expectancy from -0.0492R to -0.0021R and PF from 0.818 to 0.991.

This supports **research improvement potential**, not a confirmed profitable edge:

- confirmation test `n=32` is small;
- the day-block bootstrap interval for the candidate's test average net R was approximately -0.1884R to +0.2325R;
- the interval includes zero by a wide margin;
- full-period confirmation performance was approximately breakeven after 0.05R cost;
- the candidate became negative under a 0.10R cost stress;
- the baseline itself did not generalize well to the diverse confirmation universe.

### 12.6 Recommended new shadow variant

Only after full-path parity is restored, create a separate research/shadow strategy identity, tentatively:

```text
PULLBACK_CONTINUATION_MACD_SHADOW
```

Frozen initial definition:

```yaml
module: SWING
direction: LONG_ONLY
trigger_tf: 1H
daily_trend_min: 45
ema_zone: 21_50
rsi_min: 52
require_close_above_ema9: true
require_macd_histogram_positive: true
stop: ema50_minus_1.0_h1_atr
targets_h1_atr: [0.8, 1.6, 2.8]
tranches: [0.33, 0.33, 0.34]
ratchet: breakeven_after_t1
max_holding_days: 20
status: FORWARD_SHADOW
```

Do not reuse `PULLBACK_CONTINUATION` calibration for this variant. It has a different conditional population and requires its own evidence record.

Forward-shadow acceptance criteria:

- full fetched-H1 versus rolled-5m signal parity is demonstrated first;
- production and research enforce the same one-open-trade and duplicate rules;
- at least 60 settled forward candidates;
- candidates span at least six symbols and more than one correlation cluster;
- no single symbol contributes more than 25% of total realized R;
- TP1 rate, positive-trade rate, average R, PF, MAE/MFE, and time-in-trade are all reported;
- average R remains positive after observed execution cost and a 0.05R stress;
- net PF target is at least 1.20;
- day-block bootstrap lower bound is reported and is no longer materially negative;
- p90 observed option slippage does not exceed the modeled allowance;
- performance is broken down by broad-market regime;
- parameters remain frozen throughout the sample.

These are research-promotion gates, not performance guarantees.

### 12.7 Universe and regime refinement

The confirmation results show that the baseline does not transfer uniformly across sectors and instrument types. Do not create a symbol whitelist from small per-symbol backtests. Instead:

1. define instrument classes before evaluation, such as broad index ETF, leveraged ETF, semiconductor/high-beta growth, defensive sector, and cyclical sector;
2. estimate strategy evidence hierarchically by class;
3. add broad-market context, including SPY/QQQ daily trend and volatility regime;
4. require minimum evidence before enabling a class;
5. choose at most one alert from a highly correlated cluster when several trigger together;
6. forward-test excluded classes rather than permanently discarding them from historical results.

Prospective regime hypotheses, which remain unconfirmed and must be tested separately:

- broad market daily trend must agree with the symbol;
- avoid new long entries during a rising high-volatility risk-off transition;
- distinguish advancing-trend pullbacks from late-stage distribution;
- condition on market breadth for index and semiconductor clusters.

### 12.8 Risk-distance refinement

Restricting initial risk to approximately 1.5–1.75 H1 ATR produced large nominal expectancy in some partitions, but sample size collapsed and validation stability failed. This is not suitable as a hard production gate.

Recommended use:

- record `initial_risk_atr` on every setup;
- display and analyze it as a continuous feature;
- use it as a tie-breaker between simultaneous correlated candidates;
- do not fit a hard cutoff until forward data establishes a monotonic relationship;
- report expectancy by predeclared risk-distance bins.

### 12.9 Execution improvements may be more valuable than signal tuning

The baseline gross expectancy is small enough that reducing execution drag may produce more value than adding indicators.

Required execution experiments:

- compare long calls with defined-risk debit spreads using historical chains;
- reject contracts whose spread exceeds a configured percentage of mid;
- model first executable quote after the signal timestamp;
- use limit-price ladders rather than immediate marketable orders;
- expire the entry instead of chasing outside the modeled zone;
- measure fill probability as a separate model;
- calculate theta, IV, delta, and gamma effects through the trade lifecycle;
- rank candidate structures by conservative net expectancy and liquidity;
- retain an analysis-only state when no contract clears execution requirements.

No option-structure refinement should be promoted using underlying-only OHLC results.

### 12.10 Portfolio-level profitability

Profitability must be evaluated after portfolio interactions:

- reserve risk across active and pending alerts;
- cap QQQ/TQQQ/SMH/NVDA/AMD-style correlated exposure;
- when correlated candidates trigger together, select the candidate with the best cost-adjusted expectancy and data quality;
- report portfolio drawdown and concurrent-risk utilization, not only per-trade PF;
- do not multiply historical edge by issuing several versions of the same market bet;
- test volatility-scaled position sizing with strict maximum loss caps;
- do not use Kelly sizing until robust live distributions and tail behavior are available.

### 12.11 What should not be changed yet

Do not currently:

- replace the baseline strategy with the MACD variant;
- raise the displayed confidence percentage;
- update the production calibration table from these experiments;
- tighten the stop or widen targets in production;
- remove losing symbols from historical results after inspection;
- relax sample requirements to qualify the candidate;
- combine several favorable filters into a new optimized rule;
- treat a higher TP1 rate as proof of higher net profitability;
- enable option execution before option-level replay exists.

### 12.12 Research-governance references

The refinement process should include explicit multiple-testing controls. Relevant primary research includes:

- Halbert White, [A Reality Check for Data Snooping](https://onlinelibrary.wiley.com/doi/abs/10.1111/1468-0262.00152), *Econometrica* 68(5), 2000;
- Bailey et al., [Backtest Overfitting in Financial Markets](https://escholarship.org/uc/item/4hn4t174), on detecting selection-driven historical performance;
- Bailey and López de Prado, [The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), on adjusting performance significance for non-normal returns and multiple trials.

Recommended governance additions:

- record the number of attempted variants with every result;
- apply a White Reality Check, SPA-style test, PBO estimate, or equivalent multiple-testing procedure before promotion;
- report a Deflated Sharpe Ratio only after a reproducible return series exists;
- preserve all tested variants, including failures;
- require a new untouched forward window after any rule change prompted by test results.
