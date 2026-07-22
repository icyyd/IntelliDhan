# 9EMA Crossover — Backtest and Stop-Trail Research

**Status:** `FORWARD_SHADOW_ONLY` · not eligible for live alerts or automated
Robinhood orders

This is a separate strategy from `EMA9_TREND_PULLBACK`. Its exact signal rule
is intentionally simple:

- after a completed bar, a close moving from at/below the 9EMA to above it
  arms a buy;
- a close moving from at/above the 9EMA to below it arms a sell/exit;
- a two-close confirmation is available as a noise-reduction setting;
- entry is simulated at the next bar open, so the signal close is never used as
  a fill;
- a protective stop is checked before any close-cross exit on the same bar;
- after every completed bar, the stop may move in the profitable direction only:
  `long max(previous stop, 9EMA − trail ATR × ATR14)` and the mirrored short
  formula. Once the trade reaches the activation threshold, the stop cannot
  fall below (long) or rise above (short) breakeven.

The simulator also models a stop-limit guard. A gap beyond the configured limit
offset is recorded as a protection failure and conservatively filled at the
opening price (a market fallback). This prevents a backtest from hiding the
capital risk of an unfilled stop-limit order.

## Reproducible research command

```text
PYTHONPATH=shared-schemas:services/ingestor:services/analytics:services/learning \
  .venv/bin/python -m intellidhan_learning.ema9_crossover \
  --symbols SPY QQQ --timeframe 30m --days 55 --cost-bps 2 \
  --out /tmp/ema9-crossover-30m.json
```

The command records the bar window, symbols, cost assumption, every attempted
variant, and the selected validation winner. Test is evaluated only once for
that selected winner. Yahoo intraday history is a short exploratory window;
it is not the 12-month 0DTE evidence required for promotion.

Selection requires at least 30 train trades and 20 validation trades, with
positive net expectancy in both. A trade's entry timestamp is the next bar's
actual open time (not the OHLC bar's close timestamp). For coarse bars that
straddle the 15:55 ET 0DTE flatten deadline, the simulator exits at the bar
open conservatively; a production executor must use a lower execution
timeframe and an exchange-calendar-aware close.

## Latest run (SPY + QQQ, 30-minute bars)

The run fetched 481 bars per symbol (962 total) and tested 144 combinations of
initial stop distance, trailing ATR distance, trail activation, two-close
confirmation, and optional EMA21 alignment. With 2 bps per-side underlying
costs, no variant was positive in both train and validation, so there was no
winner and no test promotion result.

The closest validation result without EMA21 alignment was (it still failed the
30-trade train floor):

| Split | Trades | Win rate | Avg net R | Profit factor |
|---|---:|---:|---:|---:|
| Train | 42 | 31.0% | −0.1131R | 0.76 |
| Validation | 13 | 53.8% | +0.1217R | 1.31 |
| Test (not scored; train floor failed) | — | — | — | — |

The default, EMA21-aligned configuration (`1.25 ATR` initial risk,
`1.50 ATR` trail, activation at `0.75R`, two closes) was negative in all three
partitions. This is useful evidence: a raw 9EMA cross is too noisy as a
standalone long signal in the current sample. Widening the trail protects
against whipsaws but does not create a durable edge after costs.

### Same-model timeframe check

The exact same walk-forward and cost model was also run on 5m and 15m bars.
For each timeframe, the row shown is the best validation row; it was then
scored once on the untouched test split. None met the positive-train and
positive-validation promotion rule.

| Signal bars | Train avg net R | Validation avg net R | Diagnostic test avg net R* | Diagnostic test PF* |
|---|---:|---:|---:|---:|
| 5m | −0.3862R | +0.0144R | −0.4328R | 0.30 |
| 15m | −0.3958R | +0.3828R | −0.4430R | 0.32 |
| 30m | −0.1131R | +0.1217R | −0.3543R | 0.40 |

The attractive 15m validation result is a clear example of why validation
selection alone is unsafe: it failed badly in the next window. The current
evidence supports using 30m as the least noisy research timeframe, not
deploying it as a live edge.

\* These test columns are diagnostic comparisons for the validation-best row;
the harness withholds test scoring unless the row clears the train/validation
sample and expectancy gates.

## Promotion gates

Do not add this strategy to the live registry or enable options automation until
all of the following are true:

1. at least 12 months of point-in-time 1m/5m data (and historical option
   chains for 0DTE claims);
2. a frozen walk-forward candidate has positive net expectancy in train,
   validation, and a new untouched test window, with minimum sample floors;
3. performance survives realistic bid/ask, latency, stop-gap, and option
   delta/gamma cost stress;
4. results are reported by SPY/QQQ, market-volatility regime, time of day, and
   correlated exposure;
5. live execution has an idempotent protective-order lifecycle and blocks or
   flattens when a broker cannot confirm protection.

Until then, use this module for paper tracking and research only. A sensible
product design is to use the crossover as a trend/exit confirmation alongside
the existing ORB/VWAP setup families, rather than presenting it as a profitable
signal by itself.
