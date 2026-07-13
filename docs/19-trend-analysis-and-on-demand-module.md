# Trend Analysis Research and On-Demand Stock Module

**Status:** Implemented analysis module; methodologies remain `RESEARCH_ONLY`.
**Frozen analytics version:** `trend-analysis-v2`
**Research date:** 2026-07-12

## 1. Objective

Add a small set of transparent trend methods that:

- use the same parameters for every ticker;
- require only corporate-action-adjusted completed daily OHLCV bars;
- are easy to reproduce without machine learning or discretionary labels;
- can be evaluated with next-period execution and transaction costs;
- complement the existing intraday ORB, VWAP, and EMA pullback strategies;
- explain disagreement instead of hiding it inside a composite model.

“Historically robust” does not mean guaranteed or universally profitable. Trend
methods tend to exchange some upside participation for lower exposure and lower
drawdown, and they can whipsaw badly in sideways names.

## 2. Selected methodologies

### 2.1 Rising 200-day moving-average regime

**Rule:** bullish only when price is above the 200-day average and the average
has risen over the last 20 sessions. The backtest uses the simpler long/cash
rule `close > SMA200` so its behavior is directly comparable with published
moving-average timing research.

**Why retained:** it is transparent, slow, low turnover, and difficult to
overfit. Brock, Lakonishok, and LeBaron tested moving-average and trading-range
rules on the Dow from 1897–1986 and found return behavior inconsistent with
several common null models. This is historical evidence, not proof that a
modern implementation will outperform after costs.

Primary source: [Brock, Lakonishok, and LeBaron (1992)](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1992.tb04681.x)

### 2.2 Time-series momentum

**Analysis rule:** bullish when at least two of the trailing 3-, 6-, and
12-month returns are positive. The backtest uses the canonical long/cash sign
of the trailing 12-month return.

**Why retained:** Moskowitz, Ooi, and Pedersen documented one-to-twelve-month
return persistence across 58 liquid equity-index, currency, commodity, and
bond-futures instruments. The mechanism and horizon are deliberately simple.

**Important limitation:** the strongest published evidence is diversified
across futures and asset classes, not a promise for every individual stock.

Primary source: [Moskowitz, Ooi, and Pedersen (2012)](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf)

Long-history context: [Hurst, Ooi, and Pedersen (2017)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026)

### 2.3 55-day breakout with 20-day exit

**Rule:** enter long when the adjusted close exceeds the prior 55-session high;
return to cash when it closes below the prior 20-session low.

**Why retained:** trading-range breaks are one of the two simple rule families
examined by Brock et al. The asymmetric 55/20 channel also stays interpretable:
slow confirmation, faster invalidation, and no symbol-specific parameters.

This is different from the platform’s intraday opening-range breakout. It uses
completed daily bars and is intended for medium-horizon trend context.

### 2.4 Proximity to the 52-week high

**Rule:** at least 95% of the trailing 252-session high is positive momentum
context; below 80% is defensive context.

**Why retained:** George and Hwang found that nearness to the 52-week high
explained a large part of cross-sectional momentum profits in their sample.

**Why it is not independently backtested here:** the published methodology is
cross-sectional—it ranks a universe. Turning it into a single-ticker entry rule
would silently change the hypothesis. It remains a displayed context vote.

Primary source: [George and Hwang (2004)](https://onlinelibrary.wiley.com/doi/full/10.1111/j.1540-6261.2004.00695.x)

### 2.5 Volatility as risk context, not an alpha vote

The module reports ATR14, ATR as a percentage of price, 20-day annualized
realized volatility, the prior 20-day low, and an optional risk-budget quantity.
It does not add a fifth bullish/bearish signal.

Moreira and Muir found benefits from volatility management in several factor
portfolios, but later work found weaker direct out-of-sample performance for
reasonable real-time implementations. Therefore volatility is used here for
risk normalization only.

Primary sources: [Moreira and Muir (2017)](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513), [Cederburg et al. (2020)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X)

### 2.6 Forward-looking probability layer

The trend state now produces separate 21- and 63-trading-day outlooks. These
are not extrapolated price targets. For each horizon the module:

1. reconstructs the fixed trend state at historical dates using only data then
   available;
2. uses non-overlapping forward-return windows to reduce serial dependence;
3. selects outcomes whose broad state (`UP`, `DOWN`, or `MIXED`) matches today;
4. shrinks the conditional positive-return rate toward that ticker's
   unconditional base rate with a fixed ten-observation prior;
5. reports the median return, interquartile range, sample count, and Wilson
   probability interval;
6. evaluates sequential predictions with walk-forward Brier skill against an
   expanding unconditional base-rate forecast.

The result remains `UNCONFIRMED` unless there are at least 12 matched,
non-overlapping observations and the historical walk-forward forecasts beat the
base-rate Brier score. Parameters and thresholds are identical for every ticker.

This conservative benchmark requirement reflects the poor out-of-sample
stability documented for many return predictors by [Goyal, Welch, and Zafirov
(2024)](https://academic.oup.com/rfs/article/37/11/3490/7749383). It also avoids
selecting a more complex model from repeated backtests, a known source of
overfitting discussed by [Bailey et al.
(2015)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253).

## 3. What was deliberately excluded

- per-ticker optimized moving-average lengths;
- indicator voting stacks with RSI, MACD, stochastic, and Bollinger duplicates;
- black-box price prediction or LLM-generated targets;
- cross-sectional winner selection without a survivorship-safe comparison universe;
- volatility timing presented as a guaranteed return enhancer;
- automatic promotion from an on-demand analysis result into a live alert.

## 4. Fixed-parameter diagnostic

Command:

```bash
.venv/bin/python -m intellidhan_learning.research_trend --years 5 --cost-bps 10
```

Universe: `QQQ SPY SMH TQQQ AAPL NVDA MSFT AMZN META GOOGL AMD TSLA`.

Data: 1,256 corporate-action-adjusted completed daily bars per symbol, ending
2026-07-10.
Signals are formed at the close and applied to the next close-to-close return.
Positions are long or cash, without leverage or shorting. Every position change
is charged 10 basis points. Parameters are identical for all symbols.

| Method | Positive CAGR | Beat buy/hold CAGR | Smaller drawdown | Median CAGR | Median Sharpe | Median max DD | Median exposure |
|---|---:|---:|---:|---:|---:|---:|---:|
| SMA200 | 11/12 | 0/12 | 12/12 | 24.45% | 1.078 | -27.31% | 72.93% |
| 12m time-series momentum | 12/12 | 0/12 | 9/12 | 20.93% | 0.988 | -33.86% | 75.18% |
| Donchian 55/20 | 11/12 | 0/12 | 11/12 | 12.68% | 0.711 | -26.66% | 48.05% |
| Two-of-three consensus | 11/12 | 0/12 | 12/12 | 24.62% | 0.990 | -27.56% | 72.28% |

### Interpretation

- The five-year, technology-heavy sample strongly favored continuous long
  exposure. No trend method beat buy-and-hold CAGR on any symbol.
- SMA200 and the two-of-three consensus reduced maximum drawdown for all 12
  symbols, which supports their use as regime/risk context.
- Donchian produced the lowest median exposure and lowest median drawdown, but
  sacrificed the most return.
- TSLA demonstrates the failure mode: SMA200 and consensus had negative CAGR
  despite lower exposure. “Works across tickers” cannot mean “wins on every
  ticker.”
- This is a diagnostic, not a promotion backtest. The universe is current and
  survivorship-biased, the window is short, and taxes/borrow/opportunity cost are
  excluded.

### Forward-forecast diagnostic

The v2 forecast layer was run without parameter search across the same 12
symbols. A ticker/horizon counts as validated only when matched-state samples
clear the minimum and its sequential Brier score improves on the expanding
ticker base-rate forecast.

| History | Horizon | Positive walk-forward skill | Validated current context | Median Brier skill | Median matched samples |
|---|---:|---:|---:|---:|---:|
| 5 years | 21 sessions | 1/12 | 1/12 | -1.85% | 33.5 |
| 5 years | 63 sessions | 0/12 | 0/12 | insufficient | 10.5 |
| 10 years | 21 sessions | 1/12 | 1/12 | -1.32% | 80.0 |
| 10 years | 63 sessions | 1/12 | 1/12 | -2.47% | 26.5 |

The evidence does **not** support relabeling the four-method trend vote as a
generally reliable return predictor. The correct production behavior is to show
the state, historical forward distribution, and uncertainty, while returning
`UNCONFIRMED` for most ticker/horizon combinations. Ten years is the on-demand
default to improve effective sample size; users can still inspect shorter
regimes, but shorter windows are more likely to fail the evidence gates.

## 5. On-demand module

### Web app

Open **Analyze a stock** from the left navigation or search for it with the
command palette. The result view uses progressive disclosure:

1. consensus, price, as-of date, and bullish/neutral/bearish vote counts;
2. 1- and 3-month forward odds with base-rate comparison, uncertainty, matched
   sample count, and walk-forward skill;
3. one card per method with the exact observed values and rule;
4. ATR, realized volatility, invalidation references, and optional size;
5. a fixed-rule backtest table against buy-and-hold when requested.

The global execution rail and calibration drawer are hidden in this view so an
analysis result is not visually confused with a live signal or Robinhood
intent. Dark and light themes use the same semantic states, and the form stacks
to one column on small screens.

### Web API

```text
GET /api/analyze/{symbol}
    ?years=10
    &risk_budget=500
    &include_backtest=true
    &cost_bps=10
```

The endpoint accepts 2–15 years, validates ticker syntax, fetches adjusted
daily bars, excludes the current possibly incomplete daily bar, caches bars for
five minutes, and times out after 30 seconds. Provider work is capped at four
concurrent requests and the process retains at most 128 ticker/history cache
entries, preventing an arbitrary-symbol public endpoint from growing memory
without a bound.

### CLI

```bash
.venv/bin/python scripts/analyze_stock.py AAPL --years 10 --risk-budget 500
```

### Output contract

- transparent vote counts and consensus label;
- conditional 21/63-day outlooks with uncertainty and benchmarked walk-forward
  calibration;
- every methodology’s values, signal, and literal rule;
- ATR/volatility and optional reference quantity;
- fixed-rule historical comparisons with costs and buy-and-hold;
- data source, as-of time, bar count, analytics version, and limitations.

The output is analysis only. It does not create a `Setup`, `Alert`, Robinhood
execution intent, or live-eligibility record.

### Strategy-confidence policy

A composite technical score is not a forward probability. The live engine now
caps strategy confidence below the normal 0.75 gate unless calibration metadata
explicitly declares `HISTORICAL_OOS`, `FORWARD_PAPER`, or `LIVE_VALIDATED`
evidence. Thin or unclassified calibration buckets can still collect shadow
outcomes but cannot claim live-ready forward confidence. The stock outlook and
strategy calibration remain separate: a favorable ticker trend forecast never
replaces the strategy-specific win-rate calibration.

## 6. Required validation before any strategy promotion

1. Use a survivorship-safe broad equity universe including delisted names.
2. Freeze train/validation/test calendar windows before comparing candidates.
3. Test 5/10/25 bps costs and next-open as well as next-close execution.
4. Segment results by broad index, sector, single stock, and leveraged ETF.
5. Verify corporate actions and missing-bar behavior against a second vendor.
6. Run the exact frozen logic through production replay.
7. Complete at least 30 trading days in shadow/forward paper mode.
8. Promote a distinct strategy identity only if it passes the existing doc 08
   governance gates; never reuse this analysis consensus as calibration.
