# Beta strategy validation — 2026-09-24

## Decision

The current evidence does **not** support profitable automated 0DTE trading.
Fresh data after the July parameter freeze reverses the earlier attractive ORR
results: both unchanged ORR profiles and the unchanged 30-minute 9EMA crossover
lose after costs on SPY and QQQ. None is promoted, and no calibration is changed.
The appropriate business behavior is to abstain from automated entries when
strategy-specific evidence is missing, stale, or negative.

The longer daily methods show a useful risk/return tradeoff, not a superior
profit engine: the 200-day trend filter reduced drawdown but trailed buy-and-hold
return for SPY, QQQ, and SPX. Those are underlying diagnostics, not LEAPS or
swing-option results. SPX is an index context series, not an executable stock.

## New frozen-parameter check

Runner: `services/learning/intellidhan_learning/beta_validation.py`.

- Real Yahoo OHLC bars fetched on 2026-09-24; no synthetic observations.
- 42 completed sessions: **2026-07-27 through 2026-09-23**. Today's open session
  is excluded. Every 5m/30m regular-session bar must exist exactly once; partial,
  duplicate, or gapped sessions are excluded. This run excluded none.
- Per symbol: 3,276 5m bars, 546 30m bars, and 344 raw daily context bars.
- Pins every parameter from the July `control`/`shadow_candidate` ORR profiles
  and EMA9 crossover defaults as literal frozen configurations. Changes to live
  profiles cannot silently change the controls. No search or winner selection.
- Evaluation starts after the prior documented 2026-07-24 tuning snapshot.
  This is a later historical check; no claim is made that it is forward paper.
- Costs are 2, 5, and 10 basis points **per side** on underlying notional.
  ORR also retains its existing 0.02 ATR5 adverse entry/exit slippage.
- Net wins require positive final R after costs, not merely reaching TP1.
- 95% intervals use 2,000 circular five-session block resamples with both
  symbols kept together in each sampled day; fixed seed `24092026`.
- Total R and drawdown in R are trade diagnostics, not account returns. These
  replays do not apply a shared cash balance or concurrent-position budget.

### Base-cost result: 2 bps per side

| Frozen strategy | Trades | Net win rate | Mean net R | Profit factor | Max drawdown R | 95% interval for mean R |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| ORR control | 48 | 27.08% | -0.4843 | 0.4062 | 23.2466 | [-0.7596, -0.2420] |
| ORR July shadow candidate | 36 | 22.22% | -0.6321 | 0.3101 | 23.4811 | [-0.9684, -0.3637] |
| EMA9 crossover, 30m defaults | 43 | 32.56% | -0.2896 | 0.3013 | 13.9736 | [-0.4850, -0.0722] |

Neither symbol rescues the aggregate result. Mean net R at the base cost is
-0.6723 SPY / -0.3611 QQQ for ORR control, -0.6059 / -0.6488 for the ORR
candidate, and -0.2667 / -0.3160 for the EMA9 crossover.

### Cost sensitivity: mean net R per trade

| Frozen strategy | 2 bps/side | 5 bps/side | 10 bps/side |
| --- | ---: | ---: | ---: |
| ORR control | -0.4843 | -0.8413 | -1.4363 |
| ORR July shadow candidate | -0.6321 | -0.9670 | -1.5250 |
| EMA9 crossover, 30m defaults | -0.2896 | -0.4863 | -0.8140 |

The earlier ORR July window was profitable, but the next window did not retain
that result. A higher historical win rate from another permutation of this
same new window would be another fitted result, not independent confirmation.
This window is now consumed for future selection decisions; a redesigned
candidate needs a separate later holdout and measured paper fills.

## Longer-term context: five years of daily data requested

The existing fixed-method runner fetched 1,258 adjusted daily bars for SPY and
QQQ and 1,257 for SPX, ending 2026-09-23. The first 252 bars are indicator
warmup, so scored returns cover approximately four years, **not five full
years**. Costs below are 10 bps per position change, charged by the existing
implementation; its nested `round_trip_cost_bps` label is misleading and must
not be read as the cost of both entry and exit combined.

| Underlying | Buy-and-hold CAGR | 200-day-filter CAGR | Buy-and-hold max DD | 200-day-filter max DD |
| --- | ---: | ---: | ---: | ---: |
| SPY | 20.53% | 13.82% | -18.76% | -10.38% |
| QQQ | 27.42% | 22.64% | -22.77% | -13.56% |
| SPX context | 18.98% | 12.63% | -18.90% | -10.50% |

All four fixed methods (200-day filter, 12-month momentum, 55/20 Donchian,
majority consensus) had positive CAGR but lower CAGR than buy-and-hold in each
of these three series. Three of the four reduced drawdown for all three;
12-month momentum did not. SPY and SPX are closely related exposures, so these
are not three independent market replications.

All one-month forecasts remained `UNCONFIRMED`: walk-forward Brier skill versus
the expanding base rate was -2.07% SPY, -2.65% QQQ, and -0.43% SPX. Three-month
forecasts had only seven evaluated non-overlapping observations and remained
insufficient. A high directional probability in a rising market is not evidence
of predictive improvement over the market's base rate.

These daily diagnostics use today's vendor-adjusted history and a close-based
exposure model; no historical option premiums, implied volatility, earnings
availability, funding, or executable option fills are modeled. They therefore
cannot validate swing options or LEAPS profitability, and no live signal should
inherit their return figures.

## Existing evidence audit and next work

1. **0DTE:** the current frozen tests fail even underlying costs. Keep ORR and
   raw EMA cross as research; a trend/exit indicator can still provide context.
   Test one predeclared setup change at a time, using a new holdout, measured
   spreads, latency, protective fills, and simultaneous SPY/QQQ exposure.
2. **Swing:** `research_swing.py` publishes test results for every candidate,
   assumes signal-close entries, omits costs, and does not model adverse stop
   gaps. Its TP1 hit rate is not a net profitable-trade rate. Existing
   `PULLBACK_CONTINUATION` metadata already blocks live use for production-path
   mismatch. Preserve that block; reconcile the research path with the real
   entry/exit lifecycle before another profitability claim.
3. **LEAPS:** no historical option-chain profitability test exists in the
   audited evidence. Require point-in-time bid/ask, liquidity, IV/Greeks and
   corporate-action handling; present current selections as candidates for
   research until that replay is available.
4. **Multi-brain:** the recorded July regression did not beat its simple
   benchmark. AI agreement can organize a thesis, but should not upgrade the
   deterministic trading gate or be labeled a calibrated win probability.
5. **Data:** preserve provider provenance and hashes, archive complete future
   sessions within the provider's terms, and reserve future periods before
   fitting. A 59-day free intraday window cannot establish multi-regime 0DTE
   readiness. Historical option-level coverage remains missing.

## Reproduction and verification

Executed from `/Users/dhanvin/Documents/IntelliDhan-beta-optimize` using the
existing shared virtual environment:

```sh
PYTHONPATH=shared-schemas:services/ingestor:services/analytics:services/engine:services/learning:services/gateway \
  /Users/dhanvin/Documents/IntelliDhan/.venv/bin/python -m intellidhan_learning.beta_validation \
  --symbols SPY QQQ --days 59 --out /tmp/intellidhan-beta-validation-2026-09-24.json

PYTHONPATH=shared-schemas:services/ingestor:services/analytics:services/engine:services/learning:services/gateway \
  /Users/dhanvin/Documents/IntelliDhan/.venv/bin/python -m intellidhan_learning.research_trend \
  --symbols SPY QQQ SPX --years 5 --cost-bps 10

PYTHONPATH=shared-schemas:services/ingestor:services/analytics:services/engine:services/learning:services/gateway \
  /Users/dhanvin/Documents/IntelliDhan/.venv/bin/python -m pytest tests/test_beta_validation.py -q

/Users/dhanvin/Documents/IntelliDhan/.venv/bin/ruff check \
  services/learning/intellidhan_learning/beta_validation.py tests/test_beta_validation.py
```

Result: **9 tests passed; Ruff passed**. The broader new/existing strategy
regression selection also passed (**28 tests**). Tests verify frozen-parameter
independence, net-win accounting,
negative-cost outcomes, same-day symbol pairing in bootstrap samples,
active-session counts, zero-trade and too-small sample behavior, exclusion of
open/off-session/gapped/duplicate bars, and data fingerprint sensitivity.

The derived trade ledgers, scenario summaries, and input fingerprints are
archived in `docs/evidence/beta-validation-2026-09-24.json`. Raw OHLC bars are not
redistributed. The full local source report includes per-trade ledgers and costs. Its SHA256
is `c067b2aa21593102b49136837e16b4c532dea2b6188f7c0a4e4fc0b47aa622cd`.
The source `/tmp` artifact is not a durable raw-data archive. Future vendor fetches
may differ; retain a licensed immutable raw snapshot before claiming exact
reproducibility of future campaigns.

| Input | Canonical-bar SHA256 |
| --- | --- |
| SPY 5m | `5fcce5e8322c2ddc521ff2953bb413a2b61bb6ef0e7ecd95014fe449cfb63b11` |
| QQQ 5m | `2a61e4f04b94b6a4b0baeffc7d633d0a9de8cf24ae2d20d37854715001d591ff` |
| SPY 30m | `cf2639da051a7cda0acf218aaee3ee4b55c9f12264071db7806c72cf7d2d2ee6` |
| QQQ 30m | `8306e450fd9981a22df023ef2bb03ce3372502d742798070d31a2eaca5d8f111` |
| SPY raw daily context | `2e7cb80484665c79c4a7107b7545e2e8e4ea9577760a145c3a4b8239dc13ec8e` |
| QQQ raw daily context | `189761f29e5bedbf9155128a8ae1b5a3cb21c612c020e98c32b40bc1ff601086` |
