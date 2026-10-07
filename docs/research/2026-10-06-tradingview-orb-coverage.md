# TradingView ORB research coverage

Updated: 2026-10-07. Status: SPY coverage metadata audited; SPX export pending.

The user supplied the TradingView `Temp` layout for this work. The main
`SPY0DTE_1:15` layout remains outside the edit scope. Premium access, standard
five-minute candles, an available CSV export dialog, and the Cboe One SPY feed
were verified in the browser. No broker orders, alerts, subscriptions, or
execution settings are part of this workflow.

## SPY CSV audit, October 7

The user supplied `IntelliDhan_Coverage_only_-_no_trading_results_AMEX_SPY_2026-10-06.csv`.
The existing reader accepted the real export without a schema change: UTF-8 BOM,
named `Type`/`Signal` columns, and 751 paired `Entry long`/`Exit long` markers
(1,502 data rows). Every source record identifies `BATS:SPY`; the AMEX filename
does not override the embedded identity. The original file was not edited.

| Check | Result |
| --- | --- |
| Study sessions | 751 expected and observed, October 6, 2023–October 5, 2026 |
| Complete regular-session metadata | 751; no missing or invalid days |
| Regular-session bars | 58,326 expected and reported; 744 full days at 78 bars and seven early closes at 42 bars |
| Complete 04:00–09:30 premarket metadata | 567 days (75.5%) |
| Incomplete premarket metadata | 184 days (24.5%); each has a count mismatch and gap flag |
| Premarket bar-count deficit | 393: 49,173 reported versus 49,566 expected |
| SPX CSV | Not supplied; prior UI marker count is not a completed audit |

The incomplete premarket windows contain 58–65 bars, rather than the expected
66. One day, August 19, 2026, also fails the first-open timestamp check. The
metadata does not identify the missing internal timestamps or establish why
bars are absent. Do not fill gaps, change the 04:00 boundary after seeing these
results, or assume a count deficit is necessarily a provider malfunction.

Coverage differs across the predeclared date splits:

| Split | Regular-session days passing | Premarket days passing | Premarket days excluded |
| --- | ---: | ---: | ---: |
| Development, 2023-10-06 to 2025-04-06 exclusive | 375 | 239 | 136 |
| Validation, 2025-04-06 to 2025-10-06 exclusive | 125 | 125 | 0 |
| Retrospective test, 2025-10-06 to 2026-10-06 exclusive | 251 | 203 | 48 |

This is an input-coverage result, not the final eligible strategy sample.
Price validation, prior-session levels, ATR warmup, corporate-action exclusions,
event availability, and ambiguous outcomes can reduce eligibility further.
Premarket-dependent patterns must exclude the 184 failed days. Their results
must retain excluded-day counts and matched-coverage comparisons because
missingness varies across periods. RTH-only patterns do not need to discard a
day solely because its premarket window failed.

The export contains daily marker metadata and dummy entry/exit prices, not each
five-minute candle's open, high, low, close, and volume. It cannot be passed to
the Python ORB price-path screen as an OHLCV archive. The next implementation
step remains the frozen Pine pattern port with parity checks, or a separately
validated raw archive. No ORB success rate or profitability is calculated here.

Provenance and reproducibility:

- Source SHA-256: `335a078388a2a410e283c46b983cb3f3c21ddfc5ca55423d2b9fb0d347ed0a83`.
- Calendar: `exchange-calendars==4.11.2:XNYS`; schedule hash is retained in the
  [aggregate evidence](tradingview-spy-coverage-evidence.v1.json).
- Full private audit: `data/research/tradingview/2026-10-07-spy-coverage-audit.json`.
  It retains each passing/failed day and the reasons. Source and report hashes
  are recorded in the aggregate evidence; raw CSV and detailed audit stay out of Git.
- Actual SPY export compatibility is verified. SPX compatibility and coverage
  remain unverified until its own file is supplied. Warmup remains unvalidated,
  and `three_year_test_complete`, `live_eligible`, and `execution_authorized`
  remain false.

## Browser findings, October 6

The private script `IntelliDhan Coverage only - no trading results` compiled
and ran in `Temp`. Deep calculation was set to September 1, 2023–October 6,
2026, while the probe retained its narrower frozen study bounds. Both symbol
runs displayed 751 completed marker trades; the first and last records were
October 6, 2023 and October 5, 2026. These are UI observations, not an independent
audit of all exported records.

| Instrument | Exact script source ID | Observed scope and limitation |
| --- | --- | --- |
| SPY | `BATS:SPY` | Cboe One chart feed, extended hours enabled; 751 markers shown. Some inspected premarket windows have gaps. |
| SPX cash index | `SP_DLY:SPX` | S&P delayed index feed; 751 markers shown. Premarket metadata is correctly `na`. This is not a realtime entitlement or a CFD proxy. |

For example, the SPY records for October 6, 2023 and October 5, 2026 each
reported 78 regular-session bars with no internal gap, but only 64 of the 66
expected premarket bars and a premarket-gap flag. October 2, 2026 reported all
66 premarket bars with no gap. These examples establish that premarket
completeness varies; they do not establish its frequency across the full study.
Missing bars must not be fabricated or counted as evidence against a pattern.

The chart pane's counter displayed 107 loaded study dates, while the separate
Deep report displayed 751 markers. This is expected: the chart calculation and
Deep report have different history scopes. Do not use the chart counter as
the full-study denominator.

During browser setup, the download interface did not return a Trades CSV or a
local path. The user-supplied SPY file subsequently resolved that blocker for
SPY only, as documented above. SPX still needs its full Trades CSV after updating
the Deep report. No raw OHLCV archive has been obtained. The reader deliberately
rejects unknown column/schema shapes rather than guessing them.

`Temp` was saved with SPY selected again. Existing Volume, AlphaTrend, hidden
VWAP, and MACD+RSI studies were preserved. Their displayed buy/sell labels are
not outputs of this probe. The main layout was not edited, and the probe was
not published or connected to alerts or execution.

## First establish what can be tested

The existing [frozen ORB protocol](orb-pattern-protocol.v1.json) requires three
years of sessions, not merely a report with a three-year date selector.
TradingView Deep Backtesting can calculate beyond chart-loaded history, but
can also return a report when only part of the requested interval exists.
Coverage depends on the symbol and timeframe. Premium supports the feature;
it does not establish a complete archive.
[Deep Backtesting](https://www.tradingview.com/support/solutions/43000666265-how-deep-backtesting-works/),
[historical coverage](https://www.tradingview.com/support/solutions/43000668210-how-much-data-is-available-for-deep-backtesting/).

`scripts/tradingview/orb_coverage_probe.pine` is a coverage probe, not an ORB
strategy. Its simulated one-unit marker trades carry session metadata through
TradingView's trade report. Their profit, win rate, drawdown, and prices must
never be described as ORB performance. It has no alerts, webhooks, production
imports, or broker connection. This uses the documented strategy trade-list
comment export, not hidden endpoints or browser session extraction.
[Pine strategy reports](https://www.tradingview.com/pine-script-docs/concepts/strategies/).

The probe counts observed five-minute regular-session bars and SPY premarket
bars from 04:00 to 09:30 New York time. It reports the first bar's open timestamp
and last bar's close timestamp, internal gaps, and how each session was
finalized. BOATS overnight bars do not become premarket observations. SPX
premarket fields are not applicable, never zero-volume substitutes for SPY.
Missing whole sessions cannot be found by counting the bars that happen to
exist; the local audit compares export records with the pinned historical
exchange calendar, including early closes.

## Reproduction and interpretation

1. Use the dedicated research layout, standard candles, and a five-minute
   interval. Preserve and record the exact provider symbol and session setting.
   Use extended hours for SPY. Run SPX cash-index data separately.
2. Add the private coverage probe through Pine Editor. Keep its default study
   bounds, 2023-10-06 inclusive through 2026-10-06 exclusive. Select a Deep
   Backtesting date range with additional earlier history and enough data after
   the final study session for deferred records. Keep the script's date bounds
   unchanged when expanding this calculation interval.
3. Export the full Trades CSV. Preserve raw downloads under ignored
   `data/research/tradingview/`. Ordinary chart CSV export is limited to loaded
   chart data and is not the full Deep Backtesting archive.
   [Chart export](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/).
4. Run `scripts/audit_tradingview_coverage.py` with one or more `--input` files
   and a new `--out` JSON path. Preserve source hashes and exact missing/invalid
   days. Do not relabel an absent premarket window as a failed hypothesis.
5. Only after coverage checks should the frozen pattern logic be ported and
   parity-tested. Retain the original development, validation, and retrospective
   test windows. Neither a larger dataset nor Pine's default fill emulator
   removes the need for causal labels, ambiguous-bar treatment, multiplicity
   controls, costs, and prospective confirmation.

Example command, from the repository root with the `research` dependencies
installed and real source files supplied:

```bash
PYTHONPATH=shared-schemas:services/learning python scripts/audit_tradingview_coverage.py \
  --input data/research/tradingview/SPY.csv \
  --input data/research/tradingview/SPX.csv \
  --out data/research/tradingview/coverage-audit-new.json
```

The audit requires exactly one matching `BEGIN1` entry and `COV1` exit per
source/day, rejects mixed feeds and duplicate dates, and compares metadata
with the pinned exchange calendar. It preserves source hashes and writes only
to a new private output file. Existing files and source/output aliases are
rejected. `SP_DLY:SPX` remains labelled as delayed; it is not normalized to
`SP:SPX`. Even a passing metadata audit leaves `three_year_test_complete`,
`live_eligible`, and `execution_authorized` false.

Coverage metadata does not independently validate OHLC values, corporate
actions, prior-ATR eligibility, original-time publication, or options quotes.
The Cboe One feed must not be silently equated with another exchange or
consolidated feed. TradingView research access is also not a blanket license
to redistribute market data or feed an external automated trading system;
review applicable data permissions before such an integration.

The full ORB strategy, three-year effectiveness analysis, and 0DTE options
replay remain separate, incomplete work. Live eligibility remains false.
