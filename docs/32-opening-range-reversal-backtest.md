# Opening Range Reversal Backtest

**Status:** research-only / shadow candidate · **Strategy key:** `ORB_REVERSAL_15M`

This document translates the video description into deterministic rules that
can be replayed without looking into the future. It is deliberately separate
from `ORB_BREAKOUT`: a reversal is counter-trend by construction and must not
inherit the breakout strategy's calibration or live eligibility.

## Exact rule contract

1. Use completed 5-minute bars and group them by the exchange's New York
   trading calendar. Holidays are excluded and 13:00 ET half-days are allowed;
   sessions below the configured minimum bar count are skipped.
2. The opening range is the first three 5-minute bars (09:30–09:45 ET). Its
   high and low form the range box. A session with missing/non-contiguous first
   bars is skipped.
3. The manipulation gate is:

   ```text
   opening_range_width / prior_completed_daily_ATR14 >= 0.20
   ```

   “20%” therefore means 20% of the average daily range. It does **not** mean
   `ATR / price >= 20%`, which would suppress essentially every SPY/QQQ day.
   The ATR and prior-day high/low are point-in-time values; the current day's
   daily bar is never used.
4. The first qualifying 5-minute candle after 09:45 and no later than the
   configured cutoff must have an opposite body to the opening push. The
   baseline calls a candle “full” when its body is at least 50% of its range.
5. The immediately following candle must break the signal candle's low/high.
   A gap is filled at the next open; otherwise the stop-entry is filled at the
   broken level plus adverse slippage. No later candle is used to manufacture a
   fill.
6. For an opening push up, the reversal is short; for a push down, it is long.
   The initial stop is just outside the signal candle by `0.15 × 5m ATR`.
7. Take 50% off at the opposite opening-range boundary, move the stop to
   breakeven, and take the runner at `2R` or flatten at 15:55 ET. If stop and
   target occur in the same bar, the simulator assumes the adverse stop first.
   Costs are 2 bps per side plus `0.02 × 5m ATR` slippage per entry/exit.

The prior-day high/low “magic lines” are an optional confluence ablation in
this research module (`prior_level_filter=true`), not a mandatory entry rule:
the baseline records whether the opening range is near the relevant prior-day
level but does not reject trades on that basis. This keeps the video’s “further
increase the win rate” tip measurable without silently changing the core setup.

MAE/MFE are deliberately conservative: the simulator counts the full 5-minute
entry-bar range because OHLC data cannot establish whether the trigger happened
before or after that bar's excursion. They are not tick-level excursion claims.

The simulator reports underlying R-multiples only. It does not claim that an
underlying result transfers to 0DTE options; that requires point-in-time option
chains, bid/ask spreads, delta, gamma, and executable fill replay.
Each trade also records the executed partial-target fill and timestamp when a
partial is taken, so the net-R ledger can be reconstructed from execution legs.

## Reproduce the research run

Yahoo's free 5-minute history currently limits this experiment to roughly 60
calendar days. Intraday bars are replayed as supplied; the CLI fetches raw,
unadjusted daily context so ATR and prior-day levels share the same price basis
as the intraday bars. Vendor `auto_adjust` factors are not point-in-time safe;
long-horizon research must instead provide an action-aware, availability-timed
adjustment table and aligned archived bars. Run from the repository root:

```bash
PYTHONPATH=shared-schemas:services/analytics:services/engine:services/ingestor:services/learning \
  python -m intellidhan_learning.opening_range_reversal \
  --symbols SPY QQQ --days 59 --out /tmp/orr-baseline.json

PYTHONPATH=shared-schemas:services/analytics:services/engine:services/ingestor:services/learning \
  python -m intellidhan_learning.opening_range_reversal \
  --profile shadow_candidate --symbols SPY QQQ --days 59 \
  --out /tmp/orr-shadow-candidate.json

PYTHONPATH=shared-schemas:services/analytics:services/engine:services/ingestor:services/learning \
  python -m intellidhan_learning.opening_range_reversal \
  --symbols SPY QQQ --days 59 --tune --out /tmp/orr-tuned.json
```

## Latest diagnostic snapshot

Run date: **2026-07-24**. Data covered **2026-05-27 through 2026-07-24** with
3,198 regular-session 5-minute bars per symbol and 345 daily bars per symbol.
The results below are a short diagnostic, not a forward performance claim.

The `control` profile remains the default. `shadow_candidate` is an explicit
research profile containing the 25% gate / 25% reversal-body / 10:30 cutoff
settings described below. Neither profile is live-eligible or wired to broker
execution.

| Candidate | Trades | Win rate | Avg net R | Profit factor | Total net R | Max DD R |
|---|---:|---:|---:|---:|---:|---:|
| Baseline, no prior-level gate | 63 | 61.9% | +0.211 | 1.609 | +13.266 | 5.267 |
| Baseline + prior-day high/low confluence | 23 | 69.6% | +0.451 | 2.406 | +10.383 | 2.554 |
| Baseline by symbol: SPY | 27 | 74.1% | +0.380 | 2.356 | +10.270 | 4.142 |
| Baseline by symbol: QQQ | 36 | 52.8% | +0.083 | 1.211 | +2.996 | 3.666 |

The original narrow 54-variant tuner (body fraction `0.0`, stop buffer
`0.30 × ATR5`, runner `1.5R`, no level gate) produced 12 test trades, 75.0%
win rate, +0.279R average net R, PF 1.960. The later broad study below also
selected a 12-trade test slice with those same aggregate results, but by
selecting across a different grid (ATR10 and a 10:30 cutoff). This coincidence
does not make either result independent evidence; ticker and regime slices
must be reviewed separately.

## Permutation study (2026-07-24)

A predeclared permutation study varied the manipulation gate, daily ATR lookback,
entry cutoff, reversal-body requirement, stop buffer, runner target, and optional
prior-day confluence. It is reproducible with the committed runner:

```bash
PYTHONPATH=shared-schemas:services/analytics:services/engine:services/ingestor:services/learning \
  python scripts/opening_range_reversal_permutations.py \
  --symbols SPY QQQ --days 59 --out /tmp/orr-permutations.json
```

For the 2026-07-24 snapshot, the study used 41 sessions with exact chronological
boundaries of 24 train sessions (`<2026-07-01`), 8 validation sessions
(`2026-07-01` through `2026-07-13`), and 9 test sessions (`>=2026-07-14`).
It used net R and minimum sample sizes as the primary guardrails; win rate alone
is not a valid optimization target because taking smaller targets can increase
hit rate while reducing expectancy.

| Candidate | Trades | Win rate | Avg net R | PF | Untouched test |
|---|---:|---:|---:|---:|---|
| Baseline | 63 | 61.9% | +0.211 | 1.609 | 12 trades, 75.0%, +0.279R |
| Reversal body ≥25% | 65 | 63.1% | +0.356 | 2.081 | 12 trades, 75.0%, +0.333R |
| 25% gate + body ≥25% + 10:30 cutoff | 50 | 66.0% | +0.499 | 2.823 | 7 trades, 85.7%, +0.575R |
| 15% gate + body ≥75% + 10:30 + 0.30 ATR5 stop | 34 | 73.5% | +0.379 | 2.875 | 7 trades, 85.7%, +0.536R |
| Baseline + prior-day confluence | 23 | 69.6% | +0.451 | 2.406 | 5 trades, 100.0%, +0.896R |

The broad 1,152-variant selection with a minimum of 20 train and 10 validation
trades selected a more conservative configuration (20% gate, ATR10, no body
filter, 10:30 cutoff, 0.30 ATR5 stop, 1.5R runner). It scored 12 test trades at
   75.0% and +0.279R. This is an important anti-overfitting result: the higher
hit-rate candidates did not have enough validation observations to qualify under
the stricter selection rule. The older 24-trade confluence row in an earlier
snapshot was generated before the MarketClock holiday correction; 23 trades is
the corrected current result.

### Recommended shadow experiments

1. Keep the baseline as the control arm.
2. Shadow-test the 25% gate / 25% body / 10:30 cutoff candidate because it
   improves both hit rate and expectancy in this window, while requiring at
   least 100 additional sessions before promotion.
3. Keep prior-day confluence as an optional filter, not a default; it improves
   the short sample but removes roughly two-thirds of the trades.
4. Do not add a direction whitelist yet. Shorts were stronger in this window,
   but the candidate contained only 21 short and 29 long trades and the result
   is regime-sensitive.
5. Re-run the same locked parameter set on a new, untouched period and require
   positive expectancy, PF ≥1.25, bounded drawdown, and acceptable results in
   both SPY and QQQ before changing the default.

## Data required for a serious regression

The current Yahoo provider supplies only about 60 calendar days of 5-minute
history, which is insufficient to establish a durable intraday edge. A serious
promotion study should archive at least 2 years of point-in-time 5-minute bars
for SPY, QQQ, and a broader ETF cross-section, including regular/half-day
sessions, splits, halts, and bad-bar quarantine. The replay must then reserve a
final 6-month holdout that is never used for tuning. Daily or hourly history can
stress-test broad regime context, but it cannot substitute for 5-minute entry
and exit evidence. “Near-perfect” historical win rates are a regression failure
signal unless they survive this untouched holdout and realistic option-level
fills.

## Interpretation and promotion gate

- The 20% manipulation gate is not obviously too tight in this sample; moving
  it to 30% reduced the baseline to 30 trades, and 40% reduced it to 11.
- Restricting entries to 10:30 ET improved the no-level baseline to 57 trades,
  +0.243R average net R and PF 1.722, but this is another parameter choice that
  needs an untouched validation period before adoption.
- Prior-day level confluence improved the short-window metrics but cut the
  sample by roughly two-thirds. It should be treated as a hypothesis until it
  survives a predeclared, multi-year sample.
- The edge is asymmetric: the short reversals were stronger than long
  reversals in this window. Do not whitelist shorts or symbols from this small
  result.
- The 5-minute Yahoo sample is far below the platform's 0DTE evidence bar.
  Before adding this strategy to the engine, require at least 12 months of
  point-in-time intraday data, train/validation/test splits by session, a
  production-parity replay with lifecycle/concurrency controls, and a separate
  option-level fill simulation. Keep `live_eligible=false` and do not create a
  calibration file from this run.

The next implementation step is a dedicated session-range state in the engine
(`09:30–09:45`, not the existing 30-minute ORB), explicit prior-day H/L state,
and shadow-only alerts using the same evaluator as the backtest.
