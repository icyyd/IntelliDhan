# ORR Five-Year Fine-Tuning Model

**Status:** design / shadow research only  
**Scope:** SPY and SPX underlying signals; no option or broker promotion

This document defines how to tune the Opening Range Reversal strategy over a
five-year, point-in-time intraday archive without turning the backtest into a
parameter-mining exercise. The goal is robust expectancy and calibrated trade
selection. A near-perfect historical win rate is not the objective; on a live
market it is usually evidence of leakage, a tiny sample, or overfitting.

## Data acquisition options

There is no reliable, free source currently identified that supplies five years
of consolidated 5-minute SPY **and** SPX history. The practical choices are:

| Source | Free coverage | Use |
|---|---|---|
| [Massive Stocks Basic](https://massive.com/pricing?product=stocks) | Up to 2 years of stock history, minute aggregates, 5 API calls/minute | Best free starting point for SPY; rate-limit the downloader and archive the result |
| [Massive Indices Basic](https://massive.com/pricing?product=indices) | 1+ year of index history, minute aggregates, limited index tickers | Check whether the required S&P index ticker is included before relying on it for SPX |
| [Alpaca historical market data](https://docs.alpaca.markets/us/docs/market-data-faq) | Free account with IEX-focused free access and historical endpoints | Good for forward capture and a recent cross-check, not a substitute for five years of consolidated tape |
| [Alpha Vantage intraday](https://www.alphavantage.co/documentation/#intraday) | The 20+ year 5-minute endpoint is documented, but historical intraday access is marked **premium** | Not a genuinely free five-year solution |

For a five-year study, the cleanest route is to obtain a licensed archive (or a
personal export from an eligible provider) and place a ZIP/Parquet set in
`data/market/orr/` in this workspace. Do not paste an API key into chat. If API
access is used instead, add it as a local environment/Koyeb secret and provide
only the variable name and provider; the loader should download into the local
archive and then run without live credentials.

The minimum handoff is:

```text
data/market/orr/
  spy_5m_2021-01-01_2025-12-31.parquet
  spx_5m_2021-01-01_2025-12-31.parquet
  daily_context.parquet
  corporate_actions.csv       # SPY only, with effective/as-of timestamps
  manifest.json                # provider, query, timezone, hash, gaps, license
```

CSV is acceptable for a first pass, but Parquet is preferred for repeatable
five-year replays. The importer will normalize timestamps to UTC, validate the
MarketClock session, and reject missing/duplicate/bad bars before tuning.

## 1. Data contract

Use a licensed or internally archived feed because Yahoo's free 5-minute
endpoint only exposes roughly 60 calendar days. The minimum research archive is:

- five years of completed 1-minute or 5-minute regular-session bars for `SPY`
  and `SPX` (about 1,250 sessions and about 97,500 5-minute bars per symbol);
- raw OHLCV plus source timestamp, ingestion timestamp, provider, and adjustment
  status;
- an exchange calendar covering holidays, half-days, DST, halts, and late opens;
- raw daily OHLC for ATR and prior-day levels plus a versioned corporate-action
  table. Any SPY adjustment factor must be effective and observable no later
  than the signal timestamp; never use today's vendor `auto_adjust` factors to
  rewrite an old trade. Store the action effective date, publication/availability
  timestamp, and the lag policy used by the feature;
- a data-quality manifest: missing bars, duplicates, out-of-order rows, bad
  OHLC, halts, and the exact content hash of each partition.

`SPY` and `SPX` are related but must not be pooled as if they were the same
instrument. Normalize all price features by ATR or percentage, keep an asset
indicator, and report asset-specific results. SPX index bars have no corporate
actions; SPY does. Option-level conclusions require a separate historical chain,
quote, and spread archive.

### Required row semantics

```text
symbol, timeframe, ts_close_utc, open, high, low, close, volume,
source_ts, adjusted, session_date, quality_status
```

Only rows with `quality_status=VALID`, completed close timestamps, and a valid
MarketClock session may enter the replay. The feature builder must reject a
session with a missing opening bar instead of filling it. Every cross-asset
feature must be joined with `other.ts_close <= signal_ts` and the source's
availability timestamp must also be no later than `signal_ts`.

## 2. Two-stage model

Keep the deterministic ORR rule as Stage A. Add a transparent, calibrated
meta-labeler as Stage B rather than replacing the rule with a black box.

### Stage A — candidate generator

For each symbol/session:

1. Build the first three 5-minute bars (`09:30–09:45 ET`).
2. Apply the daily-ATR manipulation gate.
3. Find the opposite-colour reversal candle and next-bar break.
4. Simulate stop, partial target, runner, flatten, fees, and slippage.
5. Emit exactly one candidate per symbol/session with an immutable feature
   snapshot at signal time.

### Stage B — meta-label and calibration

Train a regularized logistic model first. It is intentionally interpretable and
works with a few thousand session-level observations. A monotonic gradient
boosting model may be tested only as a challenger, never as the default.

Candidate features, all known at entry:

| Group | Features |
|---|---|
| Opening range | width / daily ATR, width / ATR5, opening gap / ATR, first-range CLV, initial-push direction |
| Reversal quality | signal body fraction, signal range / ATR5, close location, break distance, time since open |
| Liquidity | SPY 5-minute RVOL, spread/quote quality if available, bar count and gap flags; SPX has no dependable consolidated volume, so use range/quote-quality features and mark RVOL unavailable |
| Context | prior-day H/L distance, overnight return, prior close location, day-of-week, expiration/holiday flag |
| Cross-asset | SPY/SPX normalized return, relative range, VIX/regime bucket, market trend state |
| Risk | stop distance / ATR5, target-one distance / risk, modeled cost / risk |

The label is `1` only when the complete simulated trade has positive **net R**
after fees and adverse slippage. Keep a second label for `target_one_hit` so the
model does not confuse a partial win with a profitable completed trade.

Calibrate the model on a validation fold using isotonic regression or Platt
scaling. Store the calibration curve, Brier score, reliability bins, and
coverage at each probability threshold. Do not call the calibrated probability
“confidence” unless it passes the evidence gates below.

## 3. Fine-tuning search

Use a predeclared, bounded search. The current 1,152-variant runner is the
starting grid; the five-year search may add only these controlled dimensions:

```text
manipulation_fraction:       0.10, 0.15, 0.20, 0.25, 0.30, 0.35
daily_atr_period:            10, 14, 20
entry_cutoff_et:             10:00, 10:15, 10:30, 11:00, 11:30
reversal_body_fraction:      0.00, 0.25, 0.50, 0.75
stop_buffer_atr5:            0.10, 0.15, 0.25, 0.35
partial_fraction:             0.50, 0.75
runner_target_r:              1.5, 2.0, 2.5
prior_level_filter:           false, true
```

The Cartesian product of every row above is 17,280 combinations and must **not**
be exhaustively replayed across every fold. Use this deterministic budget:

1. **Stage 1 — coarse rule grid:** run the existing 1,152 combinations once per
   fold; retain at most the top 32 profiles per fold after minimum-trade and
   positive-expectancy gates.
2. **Stage 2 — stability neighborhood:** deduplicate those profiles, keep the
   8 most frequent parameter clusters, and evaluate only the one-step neighbors
   of each cluster (maximum 256 profiles per fold).
3. **Stage 3 — meta-labeler:** fit one logistic model per fold and test at most
   12 predeclared regularization/threshold settings on validation. A challenger
   model may be run once, not searched freely.

The hard upper bound is therefore 1,420 profile evaluations per fold, with the
same deterministic ordering and random seed. Log discarded profiles and the
reason for rejection.

Do not tune all features and parameters simultaneously. Run in stages:

1. Tune the rule grid on train folds.
2. Freeze the top stability cluster, not just the single winner.
3. Tune the meta-label threshold on validation only.
4. Freeze the profile and score the test fold once.
5. Repeat the complete walk-forward window, never recycling test results.

The selected profile should be the median member of a stable neighborhood. If a
single exact value wins while adjacent values fail, reject it as brittle.

## 4. Five-year walk-forward design

Use session-grouped, purged chronological folds. A practical 60-month design is
five rolling evaluations with a six-month untouched test in each window:

```text
Window 1: train months  1–24 | validate 25–30 | test 31–36
Window 2: train months  1–30 | validate 31–36 | test 37–42
Window 3: train months  1–36 | validate 37–42 | test 43–48
Window 4: train months  1–42 | validate 43–48 | test 49–54
Window 5: train months  1–48 | validate 49–54 | final holdout 55–60
```

Apply a one-session embargo around fold boundaries. Group SPY and SPX rows by
`session_date` during bootstrap and split calculations so the same market day
cannot leak from one asset into another fold. Keep the final six-month holdout
sealed until all profile and threshold decisions are complete.

## 5. Objective and robustness score

Do not rank by win rate. Rank candidates using a multi-objective score computed
on validation folds:

```text
score = median(avg_net_R)
      + 0.10 * median(log(profit_factor))
      - 0.05 * median(max_drawdown_R)
      - 0.10 * fold_instability
      - 0.10 * cost_sensitivity
```

Definitions are fixed before the run: clip profit factor to `[0.25, 10]` before
taking the log; treat a zero-loss fold as `PF=10` and a zero-win fold as
`PF=0.25`; `fold_instability = stdev(fold_avg_net_R) / (abs(mean(fold_avg_net_R)) + 0.10)`;
and `cost_sensitivity = max(0, base_avg_net_R - stressed_avg_net_R)` where the
stressed result uses 1.5× modeled slippage and fees. Report the unclipped values
separately.

Hard eligibility gates:

- at least 200 out-of-sample candidate trades combined and at least 75 per asset;
- every one of the five test folds must contain at least 30 combined trades and
  at least 10 trades per asset. An empty or undefined fold is an automatic
  promotion failure, never a skipped statistic;
- positive expectancy in at least 4 of 5 test folds;
- median test profit factor ≥ 1.25 and no test fold below 0.95;
- test win rate at least 3 percentage points above the strategy's empirical base
  rate, with the margin declared before the final holdout;
- stress-test expectancy remains positive at 1.5× and 2× modeled slippage;
- no more than a 10% degradation when each selected parameter is perturbed one
  grid step in either direction;
- calibrated meta-labeler improves Brier score by at least 10% versus the
  train-only empirical base-rate predictor (fit from the training fold only)
  while retaining at least 30% of candidates. The baseline is not the Stage-A
  0/1 label itself.

Report point estimates with session-block bootstrap intervals, probability of
backtest overfitting, maximum drawdown, time-under-water, turnover, and the
worst asset/regime fold. A result above 80% win rate with a small sample or a
large trial count is a warning, not a promotion signal.

## 6. Promotion sequence

1. **Research:** generate an immutable manifest, grid, code commit, data hash,
   fold definitions, and full trade ledger.
2. **Shadow:** run `control` and the selected candidate side by side for at least
   90 trading days using live bars, recording every suppressed candidate and
   every quote/fill assumption.
3. **Option replay:** map only the surviving underlying entries to historical
   SPY/SPX option chains with bid/ask, delta, gamma, theta, liquidity, and
   execution latency.
4. **Paper execution:** use the existing Simulation mode; no broker orders.
5. **Review:** require independent code/data review and explicit approval before
   any live eligibility change.

The selected artifact must remain `live_eligible=false` until every gate passes.
No model output may bypass the existing claim, risk, capital, and broker-review
controls.
