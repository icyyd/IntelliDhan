# Multi-brain stock analysis and daily research desk

Status: implementation contract for `codex/multibrain-stock-analysis`

## Goal

Turn IntelliDhan into a faster research desk without turning a language model,
current filing snapshot, or social feed into an execution authority. The landing
page should answer three questions in order:

1. What matters today?
2. Which prices would change the thesis?
3. Which ticker should I research next?

The stock page should then explain the business, price structure, financial
quality, catalysts, attention risk, validation quality, and a clear research
posture. A posture is not an order and never creates a Robinhood intent.

## Daily-brief logic adapted from `icyyd/intellidhan-daily-brief`

The source project uses a useful separation of duties:

- deterministic rules build one immutable evidence packet;
- independent reviewers see the same packet without seeing each other;
- a reconciler summarizes agreement and conflict but is not a fourth opinion;
- missing facts cannot be invented;
- agreement can raise conviction, while conflict must reduce it;
- deterministic eligibility and rank remain authoritative.

IntelliDhan applies the same pattern to on-demand stock research with three
bounded specialists:

| Specialist | Evidence it may use | It may not use |
| --- | --- | --- |
| Price and risk | completed adjusted bars, trend votes, levels, volatility, walk-forward results | filings, headlines, social tone |
| Business quality | SEC identity, filings, auditable XBRL facts | chart direction, social tone |
| Catalyst and attention | recent timestamped news and social aggregates | chart direction, unprovided financial facts |

The deterministic reconciler receives only their structured outputs. It emits
`BUY`, `HOLD`, `SELL`, or `INSUFFICIENT_EVIDENCE` as a **research posture**.
It records agreement, conflicts, source coverage, blockers, and the exact rule
version. It cannot rank the configured universe, create an execution intent, or
change auto-trade state.

An optional LLM explanation may later restate this closed evidence packet. If
enabled, each specialist call must use the same immutable packet, strict
Structured Outputs, evidence IDs, no browsing, no invented values, and no
visibility into the other calls. The server remains the reconciler. This follows
OpenAI's current guidance to provide relevant context, clear outcome-level
instructions, structured output, and evals before prompt iteration.

## Manager-style evidence model

The system intentionally adds only parameter-stable evidence with a durable
research basis:

- medium-term momentum and 3/6/12-month agreement;
- price above a rising 200-day average;
- 55/20-day breakout structure and distance to confirmation;
- proximity to the 52-week high as context, not a standalone time-series trade;
- gross margin, revenue growth, net margin, free-cash-flow margin, and balance-
  sheet burden from filed SEC facts;
- market-stress and volatility context to avoid treating momentum as equally
  safe in every regime;
- timestamped news tone and social attention as low-weight context only.

Primary evidence reviewed:

- Moskowitz, Ooi and Pedersen, [Time Series Momentum](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463)
- George/Hwang follow-on international evidence, [The 52-week high momentum strategy in international stock markets](https://www.sciencedirect.com/science/article/abs/pii/S0261560610001099)
- Novy-Marx, [The Other Side of Value: Good Growth and the Gross Profitability Premium](https://www.nber.org/papers/w15940)
- Asness, Frazzini and Pedersen, [Quality Minus Junk](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2312432)
- Daniel and Moskowitz, [Momentum Crashes](https://www.nber.org/papers/w20439)
- Bailey et al., [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

These papers motivate features; they do not prove that IntelliDhan's specific
implementation is profitable. Promotion depends on IntelliDhan's frozen,
out-of-sample evidence.

## Research-posture rules

The first production version is transparent and conservative:

- `BUY`: constructive technical specialist, acceptable business-quality
  specialist, no bearish specialist, minimum technical/fundamental coverage,
  and no critical validation blocker.
- `SELL`: defensive technical specialist plus weak/deteriorating business
  quality, adequate coverage, and no contradictory bullish specialist.
- `HOLD`: sufficient evidence but no qualified agreement, or a meaningful
  conflict between specialists.
- `INSUFFICIENT_EVIDENCE`: missing technical history, inadequate business
  coverage for a fundamental claim, or unavailable validation.

Sentiment can reduce confidence or flag crowding. It cannot turn `HOLD` into
`BUY` or `SELL`. A negative current headline can flag risk but cannot become a
historical backtest feature without a point-in-time archive.

## Dynamic stock dossier

The on-demand flow supports arbitrary valid US ticker searches rather than only
the configured scanner universe:

1. Debounced ticker/company lookup from the cached SEC registrant index.
2. Corporate-action-adjusted daily analysis and key levels.
3. SEC company identity, fiscal profile, recent supported filings, and XBRL
   financial-quality facts when configured.
4. A plain-language company overview, sector/industry, market capitalization,
   and valuation context from Alpha Vantage when configured. These fields are
   display-only; provider estimates and valuation multiples cannot affect the
   posture.
5. Recent provider news and social aggregates when configured.
6. Three specialist cards and one deterministic reconciled posture.
7. Source timestamps, missing coverage, validation results, and limitations.

Key levels must be computed from completed adjusted bars and labeled as
references: last close, rising/falling 200-day average, 55-day breakout,
20-day invalidation, 52-week high/low, ATR-based risk reference. They are not
option strikes or guaranteed support/resistance.

## Homepage information hierarchy

The visual system is a soft, rounded, dark bento desk with accessible type and
card-first alerts. Search is the primary action.

1. Personalized time-aware welcome and current/stale brief status.
2. Large ticker/company search with debounced suggestions and keyboard support.
3. Today's summary, risks, and economic events.
4. Compact on-demand key-level cards for the selected ticker.
5. SPX, SPY and QQQ pulse.
6. Top three deterministic focus candidates and curated radar.
7. Signal cards; price charts remain optional detail.

Required states: loading, empty, unavailable, stale, partial coverage, keyboard
focus, reduced motion, mobile stacking, and provider-rate-limit messages.

## Validation and regression protocol

The effectiveness test is intentionally narrower than the live dossier:

- adjusted completed daily bars only;
- fixed features that existed at each historical timestamp;
- chronological expanding-window predictions;
- non-overlapping 21- and 63-session outcomes;
- regularization fit only on the training history;
- scaler parameters fit only on the training history;
- probability calibration fit only on prior predictions/outcomes;
- benchmark against the expanding unconditional base rate and the existing
  fixed vote;
- report Brier score, log loss, directional accuracy, calibration error,
  sample count, and bootstrap uncertainty where practical;
- record every tried model/threshold in the report to expose selection risk;
- no per-ticker tuning, no present-day fundamentals, no current sentiment, no
  overlapping-label leakage, and no claim of profitability from classification
  accuracy alone.

Adjusted-feed rows with only floating-point OHLC boundary noise may be clamped
to the observed open/close boundary. Materially invalid rows are rejected and
left as observable gaps instead of crashing the whole dossier or inventing a
range.

The candidate regression is promotable only if the frozen out-of-sample result
improves Brier score across the pooled universe, does not depend on one ticker,
and retains acceptable turnover/cost and drawdown behavior. Otherwise the UI
must label it `RESEARCH_ONLY` or `NO_BENCHMARK_EDGE` and the current production
vote remains authoritative.

## Non-goals and safety

- No LLM-generated watchlist membership.
- No historical fundamental backtest until point-in-time filing availability is
  represented correctly.
- No earnings-revision factor until a timestamped estimates history exists.
- No broker order, Robinhood intent, auto-trade mode change, or sizing decision.
- No promise of profit, accuracy, real-time completeness, or universal ticker
  coverage.
