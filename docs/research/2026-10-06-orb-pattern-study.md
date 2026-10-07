# SPY/SPX opening-range pattern study

Date: 2026-10-06. Status: research only; three-year validation incomplete.

## What we are testing

The question is whether information visible during the opening range changes
the odds of what happens next. This is not a search for a chart that explains
the day after it has finished. The requested period is October 6, 2023 through
October 5, 2026, with additional prior-session history for indicators.

The frozen, agent-readable specification is
[`orb-pattern-protocol.v1.json`](orb-pattern-protocol.v1.json). The first pass
is a descriptive screen, not a tuned strategy or confirmatory significance
test. It cannot create signals, intents, orders, or calibration promotions.

### Fixed questions

| Pattern | Information available at decision time | Question over the next hour |
| --- | --- | --- |
| First opening-range break | First completed five-minute close beyond the range | Does price reach the favorable distance before the adverse distance? |
| Break beyond yesterday's range | The same first break also clears the prior-session high/low | Does clearing that boundary change continuation outcomes? |
| Break beyond premarket | The same first break also clears SPY premarket high/low | Does clearing premarket change continuation outcomes? |
| Narrow opening range | Range no more than 15% of prior daily ATR | Does the first break continue? |
| Wide opening range | Range at least 30% of prior daily ATR | Does the first break continue or fail? |
| Failed opening-range break | A completed close outside, then a completed close back inside | Does movement away from the failed side follow? |
| Yesterday's boundary rejection | A candle exceeds a prior high/low and closes back inside | Does a reversal follow? |
| Premarket boundary rejection | The same rejection at SPY premarket high/low | Does a reversal follow? |
| Yesterday's low → premarket high | Price is strictly between these levels at the range close | Does price remain strictly between them for the next hour? |
| Premarket low → yesterday's high | Price is strictly between these levels at the range close | Does price remain strictly between them for the next hour? |
| Yesterday's low → yesterday's high | Price is inside yesterday's range | Does that range contain the next hour? |
| Premarket low → premarket high | SPY is inside its premarket range | Does that range contain the next hour? |

Use 15 minutes as the primary opening range. Five- and 30-minute alternatives
are exploratory sensitivity checks, not extra chances to declare a winner.
Twelve patterns × three ranges × two assets creates 72 reporting cells; these
are strongly overlapping observations, not 72 independent trading edges.
SPX premarket-dependent cells are explicitly ineligible.

For a directional observation, favorable and adverse distances are each 25%
of the **previous session's** ATR14, anchored at the completed trigger close.
This threshold is a frozen diagnostic choice, not an optimized stop or target.
Only later bars determine the label. Both barriers reached in one bar means
ambiguous unless that bar's open has already crossed one barrier, establishing
its precedence. Neither reached means neither. No event is not a losing trade.
Containment counts a touch of either boundary as a breach. A wide container
will naturally contain more price paths; a high containment rate alone is not
evidence of a profitable entry or an advantage over comparable-width ranges.

## Data and integrity rules

The current local repository has no three-year SPY/SPX intraday archive. Its
four-session golden fixture is software test data, not a substitute. Direct
three-year requests to the existing Yahoo five-minute feed were rejected as
outside its recent-history limit. The moomoo skill's prerequisite check found
no SDK in the project environment, and no OpenD listener was available on the
default port. No installation, subscription purchase, or broker action was made.

The new loader uses a pinned `exchange-calendars==4.11.2` XNYS schedule instead
of the production clock's incomplete pre-2026 tables. Its schedule is a
versioned research dependency, not proof of provider/index publication hours.
The cash-session study expects 751 scheduled sessions in the requested window;
SPX rows still need validation against the actual cash-index source. See the
[calendar project's documentation](https://github.com/gerrymanoim/exchange_calendars/blob/master/README.md).

- Canonical bars use **close** timestamps: the 09:30 close is premarket; the
  first regular five-minute close is 09:35 ET. DST is timezone-aware.
- Validate complete regular-session grids, including early closes. Do not fill
  missing bars, silently deduplicate, merge symbols, or accept a missing opening
  candle. Report missing/invalid sessions against the entire requested calendar.
- SPY premarket requires the complete 04:00–09:30 window. A sparse or absent
  premarket window makes those features unavailable, not zero. This strict
  policy can reject legitimate sparse vendor aggregates; do not fabricate bars
  to pass it. A later source-specific no-trade attestation policy needs review.
- Yesterday means the immediately preceding exchange session, not the last row
  that happened to download. ATR14 needs 15 contiguous prior sessions.
  Here ATR14 is the arithmetic mean of 14 completed daily true ranges, not
  Wilder-smoothed ATR. The method and thresholds are frozen in the protocol
  and checked against executable constants before every run.
- SPY and SPX are separate instruments. Never use SPX volume as ETF volume or
  invent a cash-index premarket from options/futures. SPX cash-index movement
  itself is not an executable stock fill. Cboe distinguishes SPX option trading
  sessions in its [contract specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications).
- Keep raw intraday and prior-session levels on the same basis. Supplied SPY
  corporate-action dates are excluded, and ATR windows crossing them are blocked
  conservatively. Missing action history prevents validation claims. Do not
  combine retrospectively adjusted prices with raw historical levels.
- Provider-revised history is not guaranteed point-in-time data. Record the
  availability assumption explicitly; a current download cannot prove the
  exact values published at the original signal time.
- Hash the source archive, schedule, protocol, and observations. Keep raw bars
  and per-event ledgers in ignored local `data/`, not in the repository.

### A practical free-data lead

Alpaca's current FAQ distinguishes its free **live IEX** feed from historical
SIP requests: historical queries can use SIP without a subscription when their
end time is at least 15 minutes old. This makes a free account worth checking
for SPY consolidated history, with `feed=sip` explicitly selected. Entitlement,
three-year completeness, extended hours, and actual returned coverage must be
tested, not assumed. This does **not** establish SPX cash-index access.
[Alpaca Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).

Provide a local licensed archive or a provider name and configured environment
variable names. Never put an API key in chat, a URL, a report, or Git. No new
account or paid data subscription has been created.

## Evaluation plan

The fixed chronological windows are:

- Development: 2023-10-06 through 2025-04-05.
- Validation: 2025-04-06 through 2025-10-05.
- Retrospective out-of-time evaluation: 2025-10-06 through 2026-10-05.

The last window is **not a pristine holdout**: July–September 2026 observations
already informed previous ORR research. New prospective observations after
this protocol freeze are required for fresh confirmation. SPY and SPX on the
same day must stay together in any future folds or paired bootstrap.

The implemented report retains every cell, eligible/no-event/ineligible counts,
outcome counts, ambiguity bounds, and exploratory five-session block intervals.
It does not rank winners, fit a prediction model, calculate trading win rates,
or declare statistical significance. Small or constant samples can produce
misleadingly narrow intervals; overlapping cells cannot be treated as independent.

Once adequate history exists, freeze at most one candidate and a meaningful
baseline using development/validation only. Predeclare effect sizes and all
primary contrasts before confirmatory analysis; apply Holm correction across
that complete family. Compare filtered breakouts with their unfiltered parent,
not an arbitrary 50% success rate. Containment needs distance/range-matched
baselines. Check quarter/regime stability, skipped-day frequency, and realistic
uncertainty rather than selecting the largest headline percentage.

Only a surviving price-path result proceeds to execution testing: next-observed
entry, latency, adverse gaps, spreads/fees/slippage, conservative intrabar exits,
capital limits, and drawdown. SPY/SPX options then require historical contracts,
bid/ask, expiry, liquidity and settlement. A directionally correct prediction
can lose money on a 0DTE option. No forced daily trade and no daily-profit promise.

## Reproduce

Install the isolated research extra, leaving runtime strategy configuration
unchanged:

```sh
pip install -e '.[dev,research]'
python scripts/fetch_orb_diagnostic.py --as-of 2026-10-06 --days 59 --out-dir data/research/orb
```

```sh
python scripts/orb_pattern_study.py \
  --input data/research/orb/orb-diagnostic-2026-10-06-59d.bars.jsonl \
  --manifest data/research/orb/orb-diagnostic-2026-10-06-59d.manifest.json \
  --out data/research/orb/orb-pattern-report-2026-10-06.json \
  --ledger data/research/orb/orb-pattern-observations-2026-10-06.jsonl \
  --evidence docs/evidence/orb-pattern-diagnostic-2026-10-06.json
```

The source manifest must declare `price_basis=raw`, provider,
instrument mapping, `bars_sha256`, and supplied SPY action dates. The loader
expects canonical `Bar` JSONL. It does not auto-convert an unlabeled external
archive; normalize timestamp and instrument semantics explicitly first.
The downloader and study refuse to overwrite earlier artifacts by default.
Use a new output directory/path to retain another run. The study accepts an
explicit `--overwrite-results` for reports only; inputs, the frozen protocol,
and output aliases remain protected.

The protocol deliberately keeps `three_year_test_complete=false`: data coverage
and a descriptive screen alone do not complete the confirmatory or execution
study. Full-study blockers and archive coverage are reported separately.

## Current findings

The fixed 59-calendar-day download returned 7,549 SPY bars including extended
hours and 3,120 SPX RTH bars, spanning August 10 through October 5. There were
40 complete regular sessions per symbol, versus 751 requested: 711 are missing
for each. No malformed returned rows were observed; that does not imply
complete coverage. SPY's September 18 dividend session was excluded from
observations, and ATR windows crossing it were blocked. That leaves 39 SPY
session rows, only 13 with eligible directional context; SPX has 25 sessions
with eligible directional context after warmup.

None of the downloaded SPY premarket windows passed the strict full-window
check. Consequently **the user's premarket-high/prior-low hypothesis could
not be tested**, rather than being counted as unsuccessful. SPX has no
premarket counterpart in this study.

Primary 15-minute descriptive outcomes:

| Pattern | Symbol | Events | Favorable first | Adverse first | Neither barrier in 60 minutes |
| --- | --- | ---: | ---: | ---: | ---: |
| First OR close break | SPY | 13 | 4 | 6 | 3 |
| First OR close break | SPX | 23 | 10 | 6 | 7 |
| Failed OR break reversal | SPY | 8 | 3 | 0 | 5 |
| Failed OR break reversal | SPX | 11 | 3 | 1 | 7 |

When price was inside yesterday's range at 09:45, that range contained the
entire next hour on 6 of 17 SPY observations and 9 of 17 SPX observations.
These are neither profitable trades nor stable estimated probabilities. For
example, the narrow-OR SPX subgroup contains just one event; its 1/1 result is
not evidence of near-perfection. The full aggregate artifact retains every
primary and exploratory cell, including unavailable and weak results:
[`orb-pattern-diagnostic-2026-10-06.json`](../evidence/orb-pattern-diagnostic-2026-10-06.json).

All available observations are in the retrospective-test window; development
and validation have no data. No three-year pattern, reliable entry rule,
trading win rate, or options-profitability claim has been established. The
next requirement is an adequate SPY premarket/RTH archive plus SPX cash-index
history, not more parameter permutations on this small recent sample.
