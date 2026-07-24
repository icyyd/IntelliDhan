# 25 — Signal Terminal Redesign and Research-Rank Contract

**Status:** Implemented first slice; agent-readable source of truth

**Date:** 2026-07-17

**Scope:** Today UI, focus ranking, external research enrichment, signal-card
minimums, AI curation boundary, strategy roadmap, tests, and deployment

## 1. Product decision

IntelliDhan is a **signal and research decision terminal**, not a charting clone.
The primary screen must answer, in order:

1. Is the market context current and healthy?
2. Which three configured names deserve attention now?
3. Is there a complete, risk-defined signal ready to evaluate?
4. Why did other candidates fail?
5. What deeper evidence or chart should the user open next?

The redesign is intentionally card- and alert-centric. Charts remain available
as optional context after a user selects a plan. A rounded, layered surface
language makes the product approachable, while market direction, data quality,
and strategy status always retain text labels and never rely on color alone.

The implementation took interaction cues from current platform patterns:

- [Robinhood Legend widgets](https://robinhood.com/us/en/support/articles/widgets-in-robinhood-legend/)
  support a configurable workflow in which scanners, watchlists, charts, and
  orders share symbol context.
- [TradingView watchlist alerts](https://www.tradingview.com/support/solutions/43000739708-watchlist-alerts-your-trading-edge/)
  treat a watchlist as an alertable research object rather than a passive list.
- [TradingView Stock Screener](https://www.tradingview.com/support/solutions/43000718866-tradingview-stock-screener-trade-smarter-not-harder/)
  demonstrates scan/filter/sort as a distinct task before chart inspection.

These are interaction references, not visual copies. IntelliDhan keeps its own
compact, calm, evidence-first identity.

## 2. Implemented information hierarchy

### 2.1 Today

The first screen contains:

- a decision hero with active, held-back, and next-scan status;
- current SPX, SPY, and QQQ anchor cards with provenance/freshness;
- a deterministic **Top 3 in focus**;
- an eight-name **Curated radar**;
- explicit market, SEC, news, and social provider status pills;
- 0DTE, Swing, and gated LEAPS research lanes;
- the daily setup artifact;
- rich signal cards and the designed held-back state;
- a selected-plan panel with optional chart context.

Mobile collapses to a single decision column. The top-three cards and signal
plans remain ahead of charts. Buttons meet a 40 px minimum target and all core
controls are keyboard reachable.

### 2.2 Focus API

Authenticated endpoint:

```text
GET /api/focus?refresh=false
```

It returns:

- anchor quotes for `SPX`, `SPY`, and `QQQ`;
- three candidates from the existing `smart-play-v1` deterministic order;
- eight curated candidates from the same order;
- universe completeness/errors and completed session;
- an explicit policy stating that AI cannot change rank or create a trade.

The scope remains the configured live universe. It is not a broad-market or
point-in-time-universe ranking. A partial scan returns no top-three or curated
rank because a missing constituent can change cross-sectional order.

### 2.3 Intelligence API

Authenticated endpoint:

```text
GET /api/intelligence/{symbol}?refresh=false
```

The symbol must exist in the configured research universe. The response is a
current `research-rank-v1` snapshot, never a profit probability or execution
instruction.

For a signed-in user, `GET /api/dossier/{symbol}` reuses the same cached
configured-universe snapshot and includes this intelligence object when the
symbol is covered. Analyze renders financial metrics, official filing links,
recent bounded news, social attention, provider state, and score coverage above
the historical technical outlook. Arbitrary tickers outside the configured
universe still receive the existing technical analysis and an explicit
enrichment-unavailable state.

| Pillar | Weight | Source | Meaning |
|---|---:|---|---|
| Technical | 50% | completed IntelliDhan daily scan | Current trend/setup evidence |
| Financial | 35% max | SEC EDGAR company facts | Absolute filing-quality screen; scaled by metric coverage |
| News | 10% | Alpha Vantage news sentiment | Current relevant headline tone |
| Social | 5% | Finnhub social sentiment | Capped attention/tone context |

Missing pillars are removed and the remaining weights are renormalized. The
financial pillar's effective weight is also scaled by its five-metric concept
coverage, so a score based on one available filing metric receives only 20% of
the pillar's maximum weight. The UI shows `coverage_weight`; an A tier with 50%
coverage is visibly not equivalent to an A tier with full coverage. Missing
inputs receive no neutral or positive placeholder.

### 2.4 Financial-strength v1

SEC XBRL facts produce an auditable `filing-quality-v1` score from available:

- annual revenue growth;
- gross margin;
- net margin;
- free-cash-flow margin;
- liabilities/assets.

This is deliberately small. It is based on absolute bands, is not
sector-relative, does not include analyst estimates or valuation, and has not
been validated as a return-forecast model. Latest 10-K, 10-Q, and 8-K links are
allowlisted and sent directly to the official SEC filing archive. SEC documents
and company facts are available through the official
[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

## 3. Rich signal-card minimum

A collapsed signal must contain enough information to decide whether to open
the complete plan. Bare minimum:

- symbol and current underlying price;
- horizon/module and strategy;
- action and age/validity;
- vehicle; for options: expiry, strike/type, and option entry price;
- for equity: share quantity and limit;
- no-chase entry zone;
- underlying invalidation/stop;
- every staged underlying target and trim percentage;
- maximum dollar risk;
- reward versus risk;
- evidence status, sample count/interval when available, and signal score;
- one-sentence thesis.

The expanded plan contains stop rule, risks, management steps, trend matrix,
budget note, and optional chart. A score is labeled as a score, not "chance of
profit." Research-only cards are unmistakably blocked from live use.

## 4. AI-curated watchlist boundary

AI curation is an explicit account action—never an automatic background rerank.
For each currently eligible top-three candidate it invokes the existing
closed-schema thesis endpoint using only server-supplied evidence. A result not
labeled `AVOID` may be added to the user's **Research** watchlist.

AI may:

- select which supplied evidence is most decision-relevant;
- return a bounded verdict/headline from the approved schema;
- help organize research candidates.

AI may not:

- invent prices, filings, events, or numerical scores;
- change deterministic candidate order;
- convert partial data into a positive claim;
- create an execution intent or place an order.

This keeps OpenAI analysis useful while preserving reproducibility. Broker
execution remains governed by `AGENTS.md`,
`docs/27-codex-robinhood-execution.md`, the intent claim/receipt loop, and the
official Robinhood Trading MCP.

## 5. Strategy refinement without false profitability claims

### 5.1 Current evidence reality

The repository's existing fixed-window research remains the governing truth:

- the latest documented production-path replay has negative average R and a
  profit factor below 1;
- trend variants can reduce drawdown without necessarily beating buy-and-hold;
- the MACD pullback variant is research-only with a small sample;
- no strategy is automatically promoted by this redesign.

The fixed diagnostics were rerun without parameter changes on 2026-07-17:

- smart momentum returned 153.34% versus SPY's 103.60% over the fixed window,
  but had lower Sharpe (0.880 versus 1.172) and much worse maximum drawdown
  (-43.41% versus -18.76%);
- none of the four trend methods beat buy-and-hold CAGR on any of 12 names;
- the trend methods usually reduced drawdown, but only 1 of 12 one-month
  conditional forecasts had positive walk-forward Brier skill and none of the
  three-month contexts cleared validation.

This supports momentum/trend as candidate-ranking and risk context. It does not
support a claim that the current signal stack is broadly predictive or ready
for live promotion.

The UI may show 9EMA, RVOL, opening range, VWAP, momentum, quality, earnings,
and trend as **evidence families**. It must not call their combination seamless
or profitable until frozen out-of-sample and forward-paper gates pass.

### 5.2 0DTE lane

Keep the rule set small and clock-aware:

1. directional regime from higher-timeframe and VWAP context;
2. opening-range break/reclaim with a completed-bar confirmation;
3. 9EMA location/slope as continuation evidence;
4. RVOL as a required participation check, not an optional bonus;
5. liquid underlying and option-chain quality gates;
6. no-chase entry, hard invalidation, staged exits, and a time stop;
7. block new risk around scheduled events and late-session gamma conditions.

Intraday momentum has peer-reviewed support as a phenomenon—see
[Gao et al., Market Intraday Momentum](https://profiles.wustl.edu/en/publications/market-intraday-momentum/)—but
that does not validate this implementation, its options fills, or 0DTE
profitability. Test the exact production path with spreads, slippage, latency,
and dependence-aware confidence intervals.

### 5.3 Swing lane

Prefer an interpretable quality-momentum continuation model:

- 12–1 and 6-month relative strength versus SPY and sector;
- above-SMA200 regime and non-extended pullback/breakout structure;
- volume participation and liquidity;
- recent earnings surprise/guidance or post-earnings drift as event context;
- financial-quality pillar kept separate from the technical trigger;
- volatility-scaled position risk and explicit earnings hold policy.

Gross profitability is a well-studied cross-sectional signal—see
[Novy-Marx, The Other Side of Value](https://www.nber.org/papers/w15940)—but the
current five-metric filing score is only a first research screen and must not be
represented as that published factor implementation.

### 5.4 LEAPS lane

LEAPS remains gated. It requires reliable options chains/history, portfolio
reconciliation, corporate-event handling, financial/valuation evidence, and
long-duration execution modeling. Candidate research may require supportive
trend plus filing quality, but no contract selection or live signal should be
enabled from the current rank.

### 5.5 Social sentiment

Social data is capped at 5% because attention is noisy, manipulable, and often
short-lived. Research reports both same-day effects/manipulation concerns
([Cary et al.](https://www.sciencedirect.com/science/article/pii/S0165176522001793))
and incremental information across platforms
([multi-platform evidence](https://www.sciencedirect.com/science/article/pii/S0304405X2400093X)).
IntelliDhan therefore treats social tone as a research annotation, never a
standalone signal.

## 6. Backtest and promotion contract

Every candidate refinement must run as a separately versioned model. Required
report:

1. frozen rules and timestamped code/data versions;
2. point-in-time universe and corporate-action handling;
3. train/validation/test or rolling walk-forward windows;
4. realistic fees, spreads, slippage, missed/partial fills, and next-bar entry;
5. benchmark/base rate and ablation versus the existing strategy;
6. expectancy, profit factor, max drawdown, turnover, exposure, sample count,
   and dependence-aware interval—not win rate alone;
7. regime and ticker concentration diagnostics;
8. forward `SHADOW` results before any live-eligibility proposal.

Promotion requires positive net expectancy and profit factor above 1 after
costs, acceptable drawdown, stable walk-forward behavior, adequate sample size,
and no material reliance on one ticker/regime. Thresholds must be registered
before the frozen test is opened. Failure leaves the strategy research-only.

## 7. Provider and deployment configuration

All calls are server-side, bounded by fixed hosts, response-size caps, timeouts,
no redirects, caches, authentication, and endpoint rate limits. Yahoo benchmark
fallback prices are labeled **indicative** because the provider exposes a
request-observation time here, not an exchange price timestamp; they must not be
described as real-time.

```dotenv
INTELLIDHAN_SEC_USER_AGENT=IntelliDhan research admin@example.com
ALPHA_VANTAGE_API_KEY=
FINNHUB_API_KEY=
```

- SEC requires a descriptive contact-bearing user agent and no API key. Follow
  the SEC [fair-access guidance](https://www.sec.gov/about/webmaster-frequently-asked-questions).
- Alpha Vantage provides `NEWS_SENTIMENT` and related documented endpoints in
  its [official API documentation](https://www.alphavantage.co/documentation/).
- Finnhub social sentiment is optional. Do not silently substitute scraped
  Stocktwits data; new Stocktwits API registrations are currently closed.
- Secrets never enter `web/index.html`, snapshots, reflected error messages, or
  logs. A provider failure returns a generic reason.

Koyeb must receive these as server environment variables. The application may
ship without the optional paid feeds; the UI will show setup needed and lower
score coverage. Provider entitlement and licensing must be confirmed before
commercial use.

## 8. Verification checklist

- Unit-test SEC financial parsing, safe filing links, news/social parsing,
  missing-pillar renormalization, focus order, and AI rank boundary.
- Run Ruff and the full test suite.
- Parse the inline JavaScript and load the app at desktop and mobile widths.
- Verify keyboard focus, no horizontal overflow, dark/light contrast, and
  reduced-motion behavior.
- Verify unauthenticated focus/intelligence calls fail closed.
- Verify disabled/missing providers remain visible and receive no score.
- Verify the API key never appears in HTTP errors, HTML, or logs.
- Obtain independent review before publishing the branch.

## 9. Deliberately deferred

- licensed consolidated real-time feed/router;
- broad point-in-time universe and peer/sector-relative rank;
- valuation, estimates, transcript change, and earnings calendar providers;
- durable research snapshots and ranking outcome calibration;
- compare view and portfolio-aware candidate conflicts;
- reliable historical option chains and LEAPS/0DTE execution backtests;
- live-qualified strategy or unattended trading.

Those gaps must remain visible in product language. This slice makes the
terminal more useful and current; it does not manufacture edge.
