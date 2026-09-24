# Beginner signal desk

Status: implemented and locally verified in the existing local-beta feature branch.
Owner request: make IntelliDhan understandable without finance expertise,
centered on trend and buy/sell/hold decisions for 0DTE, 2–5-session swings, and
LEAPS. This changes presentation and the research read-model, not trading
permissions, strategy calibration, risk limits, or evidence of profitability.

## Product contract

The default `/` route has three tasks: **Signals**, **Stock analyst**, and
**My journal**. The former terminal remains at `/advanced`; no existing module
or collaborator work is removed. Use the approved owl/lotus mark and restrained
navy/orange palette. Layout is card-first, with explanations and data provenance
under expandable sections rather than repeated chart panels.

Each opportunity answers these questions in order:

1. Which way is the observed trend moving: up, down, mixed, or unknown?
2. What does the research support: buy-side, sell-side, neutral/hold, or wait?
3. Is there a setup to review, or is the evidence incomplete?
4. Why, what price invalidates the idea, and what could go wrong?

Trend is not an entry signal. Buy-side research is not an order. Sell-side
research is not permission to short or an instruction to sell a holding we
cannot see. Hold describes a neutral research view, not portfolio-specific
advice. A bought put has a falling-price thesis but a buy-to-open transaction;
these must never be conflated. Missing data cannot become a bullish conclusion.

Signal cards show plain-language explanations, evidence status, timing, and
explicit stock-price versus option-premium units. Raw scores are not presented
as success probabilities. A failed readiness check produces **Wait** and a
reason. Even a qualified display summary has `execution_authorized: false`.

## Implementation boundaries

- `web/desk.html`, `web/assets/desk.js`, and `web/assets/desk.css` form an isolated
  default shell. Do not import legacy theme overrides into it. The advanced
  workspace retains its original source and behavior.
- `decision_summary.py` is a pure, typed adapter with an injected aware `now`.
  It reads existing alerts, calibration, data health, or dossiers; it cannot
  create intents, change risk policy, write trades, or call a broker.
- Authenticated `/api/state` adds `signal_desk` with version, generation time,
  and bounded summaries of the existing alert set. Existing response fields stay
  intact. Snapshot generation is not quote generation.
- `/api/dossier/{symbol}` adds `decision`. `include_review=false` skips the
  optional paid Claude review while retaining deterministic multi-brain analysis
  and cached provider enrichment. Explicit on-demand analysis retains the
  previous review default. AI opinion cannot override deterministic gates.
- Public `/api/playbooks` provides a static, research-only catalog with holding
  targets, option-expiry windows, setup explanations, evidence, and gaps. It
  remains available during database recovery and exposes no account data.
- Two-minute browser refresh is single-flight and stops in hidden tabs. Resuming
  checks again. Failed updates preserve last-known context with a warning;
  unavailable account storage must not silently log a user out. Verified session
  expiry clears private state. Older asynchronous responses cannot overwrite a
  newer ticker search or rehydrate data after sign-out.
- Account sessions remain HttpOnly. Do not store tokens/passwords in JavaScript
  storage. Watchlist writes remain user-initiated and account-scoped. The journal
  reads existing role-filtered endpoints; the beginner desk has no order button.

## Horizon honesty

| Requested experience | Current support | Do not imply |
| --- | --- | --- |
| 0DTE, same-session trading | Existing 9EMA multi-timeframe Simulation research; separate ORR underlying backtest | Proven option profitability, an executable contract from an equity fallback, or approval for Live |
| Swing, 2–5 trading sessions | Existing hourly/daily research, with no uniform filled-trade 2–5-session deadline | That alert expiry is a maximum holding period or a daily forecast validates a 2–5-day strategy |
| LEAPS | Long-term stock research and option-expiry filtering; no registered LEAPS entry/exit strategy | A ready LEAPS trade or a validated one-year return forecast |

The existing stock forecast covers 21 and 63 trading sessions, not the three
requested option holding periods. Research coverage remains explicit. Swing
option-expiry selection is 21–90 calendar days and LEAPS is 365–1095 days;
these are not promised holding periods or time-to-profit estimates.

The course's short-opening credit spreads are outside the current single-long-
option execution contract. Keep them educational and unsupported. Adding their
trade routing would require separate authorization, broker capability checks,
collateral/assignment/settlement handling, and multi-leg lifecycle validation.

## Source review, September 24, 2026

Reviewed through the user's authenticated **external Chrome browser**, using
visible lesson pages, course navigation, selected interactive video-caption
passages, and the embedded inspiration app. No cookies, private API endpoints,
member credentials, full course transcripts, or proprietary app assets were
copied into this repository. This is a targeted review, not a claim that all
80 general-course lessons or every video minute were studied.

User-supplied entry points:

- [0DTE Training](https://www.moneytalkrashad.com/products/communities/v2/tradetofreedomcommunity/resource/be68f768-9074-4d50-8e8d-b5ca9b2440fe)
- [Trade to Freedom](https://www.moneytalkrashad.com/products/communities/v2/tradetofreedomcommunity/resource/7b14fb3a-892f-402d-86ce-ef4c9194f84e)
- [TradeFormIQ inspiration](https://www.moneytalkrashad.com/products/communities/v2/tradetofreedomcommunity/resource/87872bc5-c8f8-4d28-9323-d56cf9f42692)

### Lessons applied

- The [simplified exit lesson](https://www.moneytalkrashad.com/products/0dte-training/categories/2157081468/posts/2199152591)
  advocates a predetermined invalidation instead of reacting emotionally or
  adding to a losing position. The catalog captures that educational principle;
  it does not promise a stop fill or import the author's loss estimates.
- The [paper-trading lesson](https://www.moneytalkrashad.com/products/0dte-training/categories/2157081468/posts/2191156411)
  emphasizes practicing the same rules and sizing before using real money.
  IntelliDhan retains stricter evidence/promotion requirements; a short practice
  period is not sufficient proof of an edge.
- Selected captions from [Long Calls & LEAPS](https://www.moneytalkrashad.com/products/trade-to-freedom/categories/2153430214/posts/2169901402)
  reinforce that option expiry and premium risk need plain-language explanation.
  Longer expiry does not eliminate loss of premium or validate a stock thesis.
- The [main 0DTE lesson](https://www.moneytalkrashad.com/products/0dte-training/categories/2157081468/posts/2185199605)
  is a credit-spread approach, not the existing long-option scalp. Selected
  captions reference stochastic direction and low-delta short-strike selection.
  Exact entry rules and the current pinned strategy amendments were not fully
  verified; no executable replica or implied success rate is claimed.
- The [strike-selection lesson](https://www.moneytalkrashad.com/products/trade-to-freedom/categories/2153434699/posts/2183493122)
  introduces contextual inputs such as Bollinger bands. These are research
  hypotheses, not an instruction to add correlated indicators until a backtest
  looks attractive.
- TradeFormIQ separates business overview, financial checks, market context,
  and option research. It labels feed delays and distinguishes filter matches
  from investment quality. Apply those transparency principles, not its stock
  figures, historical returns, proprietary assets, or opaque signal rankings.
  Its displayed Swing method can span up to 30 sessions, so it must not be
  relabeled as the requested 2–5-session method.

## Strategy work that remains

Keep existing results and live gates intact. The September later-period checks
did not establish a profitable intraday edge; a UI refactor cannot fix that.

1. Specify a separate causal 2–5-session Swing lifecycle: eligible next-session
   entry window, earnings/gap rules, earlier invalidation, maximum fifth-session
   exit, early closes, holidays, restart recovery, and unresolved-data handling.
   An audit found the current fixed eight-hour Swing entry validity can expire
   an end-of-day setup overnight before the next opening bar. Fix and test this
   in the strategy/lifecycle change, not by extending stale entry permissions in
   the display adapter.
2. Freeze a small intraday study: existing 9EMA control, a volume/trend filter
   variant, and the separate opening-range reversal. Compare after costs and
   drawdown using untouched later data. Do not sweep until a desired win rate
   appears, recycle known holdouts, or confuse underlying R with option returns.
3. Specify LEAPS quality/trend research using point-in-time filings, debt/cash
   generation, valuation sensitivity, weekly trend, and a defined review/exit
   policy. Backtest selection without survivorship or filing-date leakage before
   adding a signal badge or automated contract selection.
4. If spread research is authorized later, verify the complete current course
   rules and use time-aligned bid/ask/multi-leg data. A price touching a strike
   does not establish the price at which a spread could be closed.
5. Track coverage separately from model confidence: price source age, filing
   period/publication date, news publication/fetch time, social coverage, missing
   feeds, evidence sample, costs, and strategy version. A freshly assembled
   dossier must not rebrand cached fundamentals as newly observed data.

## Verification contract

Unit and API regressions cover unknown/stale/future timestamps, expired and
research alerts, call/put semantics, per-alert calibration evidence, missing
fundamentals, actual forecast horizons, no execution authorization, authenticated
state, additive dossier fields, and no paid review on automatic refresh. Browser
behavior tests cover overlapping requests, ticker races, hidden tabs, 401/503
handling, unsafe links, and private-state clearing. Visual QA should include
mobile and desktop, long content, empty states, account dialogs, and expanded
evidence. Synthetic fixture screens must be unmistakably marked DEMO and must
never feed the local research runtime or execution service.

Verified on this pass: **605 tests passed**, with six opt-in network tests
deselected. Pinned Ruff 0.4.10, shipped JavaScript syntax/behavior checks, and
diff checks passed. Independent agents reviewed the read-model, API integration,
source-age semantics, alert ordering, and preview isolation. Browser QA covered
Signals, populated stock analysis, Journal, expanded evidence/playbooks, and
the account dialog; 320/375/768/1024/1440px checks found no horizontal overflow.
The local server reported READY, and an actual public SPY dossier returned
WAIT for stale daily history and missing business enrichment. No personal
account was created. Signed-in filled layouts used the marked synthetic fixture,
not a claim of connected live option data or real performance.

For repeatable visual QA only:

```sh
.venv/bin/python scripts/preview_signal_desk.py
```

The preview binds only to `127.0.0.1:8322`, serves no production store or provider,
and rejects mutations. Search AAPL for its synthetic dossier. Stop it after QA.
It is not a substitute for the real local runtime at port 8321.
