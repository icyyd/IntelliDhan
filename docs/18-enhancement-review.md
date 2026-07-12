# 18 — Architecture, Signal Accuracy, and UX Enhancement Review

**Status:** Agent-readable implementation brief  
**Review date:** 2026-07-12  
**Reviewed revision:** `a3f1170`  
**Scope:** Current executable baseline, research/backtesting methodology, signal reliability, risk controls, and web-app experience.

## 1. Purpose and Interpretation

This document consolidates the enhancement review into an implementation-oriented brief. It is intentionally explicit about the difference between:

- what the specification intends;
- what the current runtime actually enforces;
- what the current research supports;
- what must be completed before confidence percentages can be treated as live trade-quality claims.

This is a planning and acceptance-criteria document, not a statement that the listed enhancements are already implemented. Agents must preserve the platform's central principles: human-confirmed execution, transparent uncertainty, conservative risk, deterministic replay, and a first-class no-trade state.

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

`PULLBACK_CONTINUATION` is the first genuinely promising research candidate, but it should be classified as **historical out-of-sample evidence; forward paper required**, not as a fully validated live edge.

## 3. Verified Baseline

### 3.1 Test status

At the reviewed revision:

- 58 offline unit/replay tests pass;
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

### 3.3 Refreshed production-path replay

A current 55-day gated replay over the 12-symbol universe processed 33,696 5m bars and produced:

- 2 emitted setups;
- 2 composed alerts;
- 2 decided paper outcomes;
- both outcomes reached TP1 and later stopped at breakeven;
- 100% TP1 win rate on `n=2`;
- +0.172R average realized result.

This demonstrates that the gated path can emit and settle the strategy. It is not a statistically meaningful live-performance sample.

## 4. Priority Work Queue

| Priority | Work item | Required outcome |
|---|---|---|
| P0 | Research/production trigger parity | The production strategy evaluates the exact completed trigger-timeframe bar used by research. |
| P0 | Live data-quality gate | No alert can publish when a required input is stale, gapped, degraded, quarantined, or missing. |
| P0 | Remove silent instrument substitution | An option strategy cannot silently become an equity alert when the option chain/quote is unavailable. |
| P0 | Risk-state wiring | Daily loss, outstanding risk, cooldowns, concurrency, buying power, correlation, and duplicate controls affect the live gate. |
| P0 | Honest evidence vocabulary | Historical base rate, calibrated confidence, forward-paper rate, and live-taken rate are separate fields and labels. |
| P1 | Locked research protocol | Calendar splits and candidate selection are frozen before test evaluation; the test set is evaluated once for one frozen candidate. |
| P1 | Net-expectancy gate | POP-based strategies require positive conservative expectancy after execution costs, not high hit rate alone. |
| P1 | Terminal trade horizon | Every research and paper trade resolves by stop, target, thesis exit, or configured time exit. |
| P1 | Reproducible research artifacts | Each result records data window, source, hashes, universe, parameters, analytics version, and code revision. |
| P1 | Decision-workstation UI | Replace the monitor page with chart-centered decision context, an evidence-aware alert rail, and complete degraded/empty states. |
| P2 | Option-level historical replay | Validate option selection, entry fills, exits, Greeks, spread, and slippage with historical quote/chain data. |
| P2 | Calibration and drift dashboard | Render claimed vs realized curves with intervals, sample sizes, versions, and forward/live segmentation. |

## 5. Signal Accuracy Enhancements

### 5.1 Fix trigger-bar identity before further promotion

Current production flow derives H1 state from 5m rollups. When a new H1 bucket is detected, the prior H1 bar is emitted and the H1 indicators update. The strategy then reads `state.last_bar`, which is the new 5m bar that caused the rollup, not the completed H1 bar used by the research simulator.

This can change:

- pullback-zone touch detection;
- reclaim confirmation;
- entry price;
- stop and target geometry;
- setup timestamp;
- downstream outcome and calibration.

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

- every strategy receives an explicit `trigger_bar`;
- `trigger_bar.timeframe == strategy.trigger_tf`;
- research, replay, backtest, shadow, and live paths call the same evaluation function;
- a parity fixture produces identical raw signals in the research and production harnesses;
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
- order staging is the only flow that requires a modal.

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
- order staging always requires explicit confirmation;
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

