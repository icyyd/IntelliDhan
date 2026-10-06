# TJ The Wheel Deal: source review and testable research plan

Reviewed: 2026-10-06. Status: **research only; no performance validation**.

Companion machine-readable handoff:
[`wheel-deal-candidates.v1.json`](wheel-deal-candidates.v1.json).
This file and that manifest are not strategy configuration. They are not loaded
by the engine, calibration system, signal desk, or broker bridge. Active
[execution](../27-codex-robinhood-execution.md) and
[data-integrity](../28-platform-safety-and-data-integrity.md) contracts take
precedence. No new trade type, allocation, order, or execution mode is enabled.

## 1. What was actually reviewed

The [public site](https://tjtherealwheeldeal.com/) was accessible in external
Chrome. Every slide in its 12 linked public lesson decks was read: **151 slides**.
The linked four-page position PDF was text-extracted and visually reviewed on
all four pages. The 24 YouTube broadcasts were inventoried, **not watched or
transcribed**. This is comprehensive coverage of the linked written curriculum,
not a claim to have learned every spoken lesson. The book, private portfolio
records, and historical broker fills were not accessed. Do not reproduce whole
decks or transcripts in the repository; retain concise paraphrases and links.

| ID | Source and coverage | Useful idea; not proof of an edge |
|---|---|---|
| HOME | [Curriculum and doctrine](https://tjtherealwheeldeal.com/) | Separate collected cash from earned profit; write the objective and acceptable outcomes before entry. |
| RTM01 | [Business Plan](https://tjtherealwheeldeal.com/rtm/ep01), 11/11 slides | Funding, position limits, governance, and a monthly total-account review. Its wealth target includes substantial deposits, not just trading returns. |
| EP24 | [Back to the Basics](https://tjtherealwheeldeal.com/episodes/ep24), 14/14 | Delta/gamma/theta/vega interact; source practice includes buying short options back after 50–75% premium capture. |
| EP16 | [VIX / LEAPS review](https://tjtherealwheeldeal.com/episodes/ep16), 15/15 | Index volatility is not single-stock IV. Review a long option's purchase price, expiration, and thesis separately. |
| EP14 | [Report Card](https://tjtherealwheeldeal.com/episodes/ep14), 13/13 | Assess concentration, directional exposure, volatility exposure, collateral, and correlated losses together. |
| EP17 | [PNR Trap](https://tjtherealwheeldeal.com/episodes/ep17), 14/14 | Single-name broker stress figures can miss simultaneous portfolio shocks and uncovered call obligations. |
| EP18 | [Model Account](https://tjtherealwheeldeal.com/episodes/ep18), 14/14 | Label each position's purpose and lifecycle; low current delta or margin usage does not eliminate assignment obligations. |
| EP19 | [Swap](https://tjtherealwheeldeal.com/episodes/ep19), 12/12 | Compare the whole position before and after an adjustment, including cash flows, realized losses, and new commitments. |
| EP20 | [De-Risk](https://tjtherealwheeldeal.com/episodes/ep20), 12/12 | Reducing shares without addressing outstanding calls can uncover them. Compare exposure and liquidity, not premium alone. |
| EP21 | [Riddle](https://tjtherealwheeldeal.com/episodes/ep21), 14/14 | AI can critique explicit scenarios. Its illustrated option values rely on pricing-model and volatility assumptions. |
| EP22 | [Human Override](https://tjtherealwheeldeal.com/episodes/ep22), 9/9 | Record why an objective changes. Psychological comfort is not evidence that added exposure is safe. |
| EP23 | [Buy. Hold. Hope.](https://tjtherealwheeldeal.com/episodes/ep23), 11/11 | Compare an option overlay with simply holding the stock, including capped upside, turnover, and behavioral costs. |
| EP13 | [ENPH Deep Dive](https://tjtherealwheeldeal.com/episodes/ep13), 12/12 | A business review should include competitive position, management, demand, finances, price behavior, and portfolio fit. |
| MU_PDF | [Micron Complex, October 5](https://tjtherealwheeldeal.com/highlights/mu-2026-10-05.pdf), 4/4 pages | Scenario cards and precommitments are useful presentation patterns; its position and projected outcomes are not validated inputs. |

The site mixes long-term share accumulation, short options, discretionary
adjustments, and leveraged examples. It is **not a documented 0DTE entry system**.
None of its stated account results establishes a portable win rate, causal
advantage, or expected return for IntelliDhan.

## 2. Source-quality corrections before reuse

These are our audit conclusions, not accusations about trading records we have
not seen. Keep source snapshots separate from verified provider observations.

1. **Count coverage across all expirations, not one rung at a time.** EP13's
   slide 10 describes four simultaneous 650-contract calls against 75,000
   shares. With standard 100-share contracts, that is 260,000 shares promised,
   not four separately covered positions. Future repurchases do not provide
   present coverage. EP17 and the PDF also disclose uncovered upper rungs.
   Reject that construction; never borrow coverage from a future roll or rebuy.
2. **Cash earmarked at a call strike is not a covered call.** EP18's planned
   purchase of shares near higher strikes can fail after a gap. A stock can
   rise beyond the reserved purchase amount. Our candidate calls require
   unencumbered shares already owned, with exclusive lot reservations.
3. **Theta is a sensitivity, not a daily paycheck.** EP14 annualizes a current
   theta figure; PDF page 2 adds current theta across future months. Do not
   use either as a return forecast. Reprice each leg as price, IV, time, rates,
   dividends, and exposure change; losses do not become harmless by being open.
4. **Recompute arithmetic.** EP16 slide 10 lists a $27 long call bought at
   $11.64 but displays $30.59 as breakeven. From those stated inputs, expiry
   breakeven is $38.64 before fees; an unstated adjustment would need evidence.
   Its displayed sizing and probability figures are not transferable either.
5. **A new credit does not erase a previous loss.** Treat a roll as a close and
   a new open. Record the old realized P&L and new liability separately.
   A reduced economic share outlay is not automatically a tax-basis adjustment,
   and neither a low basis nor received premium makes current exposure risk-free.
6. **Do not promote narrative assumptions.** IV contraction after earnings,
   eventual price recovery, available credit rolls, fixed correlations, or
   favorable future assignment prices are scenarios, not guarantees. Source
   corporate/macro figures, tax generalizations, and current prices require
   independent primary-source checks before appearing in an analysis card.
7. **Resolve conflicting rules before any experiment.** The decks variously
   avoid earnings, trade into earnings, accept assignment, and close early.
   Those are different strategies. Do not let an AI pick whichever rule would
   have worked after seeing the outcome.

Official mechanics reinforce the boundary: a cash-secured put reserves cash
for assignment and can lose most of that commitment; a covered call caps upside
while retaining substantial stock downside. Early assignment and ex-dividend
timing matter. See [OIC cash-secured puts](https://www.optionseducation.org/strategies/all-strategies/cash-secured-put),
[OIC covered calls](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write),
and [FINRA assignment mechanics](https://syndication.finra.org/content/trading-options-understanding-assignment).
SPX cash-settled European options need a different model from a physically
settled equity/ETF wheel; see [Cboe SPX specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications).

## 3. Candidate experiments, not trading recommendations

The manifest contains proposed research parameters, not source-author rules or
optimized settings. Its unresolved fields must be resolved and the protocol
frozen before running a backtest. **It is not runnable as written.** Start with
three simple candidates; do not
combine all indicators or optimize hundreds of permutations.

### A. Cash-secured acquisition put

Purpose: test whether waiting for a predeclared purchase price through a short
put improves risk-adjusted acquisition outcomes over stock ownership or cash.
Begin with separate SPY and QQQ research sleeves, not today's winning stocks.
Sell only when the full strike obligation plus costs fits cash reservations and
the approved concentration limit. Long-option debit sizing is inapplicable.

Proposed first test: 30–45 calendar DTE, nearest 35 DTE, a target absolute delta
of 0.20 within 0.15–0.25, and a strike no higher than the predeclared acquisition
ceiling. Evaluate on the first exchange session of the week, 15 minutes before
its scheduled close; use only previous completed daily bars for trend context.
First specification work must define how that ceiling is generated causally,
the cash reserve/concentration caps, quote requirements, and exit priority.
No trade when those inputs are missing or one standard contract is unaffordable.

Compare 50% versus 75% net premium capture, changing only that rule. Assignment
is permitted only with reserved funds and must create a real share lot in the
research ledger. A thesis failure closes the obligation at an available modeled
price; accepting assignment is not a substitute for a defined failure rule.
No automatic roll, martingale, or borrowing to rescue the position.

### B. Fully covered call overlay

Purpose: test whether an overlay improves the outcome of an existing share
holding, including the upside it gives away. Candidate expirations and delta
window initially match A. The sale price must meet a predeclared acceptable
liquidation price, not an AI's unverified target. A rung reserves its own shares;
all outstanding calls together must fit available standard lots.

Compare no overlay, 50% lot coverage, and 100% lot coverage at identical initial
capital. Round down to whole contracts; a one-lot account cannot implement 50%
coverage. Close/expire/reconcile a call before making its shares available to
another call or a share sale. Do not force a call sale on a strong rally just
to collect premium. The optional rally filter needs a completed-bar definition
and must be tested separately, not silently included in the baseline.

### C. Fully funded wheel lifecycle

Combine A and B only after each component and their accounting pass separately:

```text
cash -> reserved put cash -> short put -> close/expire OR assigned shares
assigned shares -> reserved share lots -> covered call -> close/expire OR delivery
delivery -> reconciled cash -> next eligible cycle (never same-observation re-entry)
```

An unknown assignment, failed close, or unreconciled settlement leaves exposure
open/unresolved. It never resets the strategy to cash or invents a profitable
cycle. Already-reserved cash cannot also fund a dip purchase. Already-reserved
shares cannot cover another expiry. Treat ETF share ownership as potentially
months-long; this is not the requested 2–5-session Swing product or a LEAPS call.

### Transfer into stock analysis and existing horizons

For the stock analyst, adapt the business checklist into six evidence sections:
competitive advantages; management/capital allocation; market/demand; financial
strength/valuation; price behavior/liquidity; portfolio fit. Each needs dated
evidence, a bear case, and an explicit unknown state. Do not average missing
fundamentals into a favorable rank. Portfolio fit cannot be assessed without
authorized current holdings, collateral, and risk ceilings.

For LEAPS, add a research check comparing the same thesis expressed as shares
versus a long call: debit, intrinsic/extrinsic cost, expiry breakeven, liquidity,
IV context, catalyst timing, and total-loss exposure. Premium previously earned
is still capital at risk, not free funding. This check is not a new entry model;
the LEAPS strategy and point-in-time inputs remain unimplemented.

For 0DTE and 2–5-session Swing, retain existing entry identities. Transfer only
explicit pre-entry objectives, event awareness, scenario loss review, and
post-trade attribution into future separately tested changes. A high VIX is
neither a directional signal nor permission to increase buying-power usage.
The source's short-option early-profit rule must not silently replace the
9EMA long-option trend-break exit.

## 4. Required research infrastructure

The current seven-strategy registry contains no wheel/CSP/covered-call strategy.
Generic paper trades grade underlying R, not option execution, assignment,
collateral, or stock lots. Historical option performance **cannot** be inferred
from those outcomes or fabricated from today's chain.

Before tests, provide:

- Licensed, timestamped historical bid/ask chains, quote sizes, trades/volume,
  open-interest publication time, Greeks or reproducible model inputs, and
  contemporaneous underlying quotes. A midpoint is not a guaranteed fill.
- Contract identity, standard multiplier/deliverable, exercise and settlement
  type, exchange calendars/cutoffs, corporate actions, dividends/ex-dates, rates,
  fees, assignment processing, and settlement availability. Initially reject
  adjusted contracts; use raw execution prices with split-consistent histories.
- Point-in-time earnings/calendar vintages and filings for single-stock
  extensions. Never inject today's fundamentals into old observations. Include
  delisted names when broadening the universe; archived membership is required.
- An event-driven, no-broker simulator with cash/share reservations, expiry and
  early-assignment scenarios, forced-liquidation/gap cases, and restart-safe
  identifiers. Unknown lifecycle observations stay unresolved; publish coverage.
  Assignment modeling is an assumption, not a claim to know a historical
  writer's actual assignment; report sensitivity to plausible alternatives.
- Version the lifecycle scenario table before coding: early put assignment,
  ex-dividend call assignment, ITM expiration, after-hours exercise/gap risk,
  settlement availability, failed/partial closes, and halts/missing quotes.
  Each case needs an event clock, cash/share availability rule, source, and
  deterministic expected ledger state. These cases are required but unresolved
  in the manifest, not hidden defaults or claimed simulation support.
- A separate run manifest recording data hashes, as-of boundaries, code commit,
  costs, all tried variants, discarded candidates, and untouched holdout dates.

Total account equity must reconcile cash + share value + long-option value -
short-option liabilities. Reservations constrain availability without being
subtracted twice from equity. Report contributions separately. Track realized
and open P&L, dividends, fees, capital-days, assigned lots, delivery, and the
complete campaign result. Mark short liabilities conservatively at ask and
shares at executable liquidation assumptions; disclose stale/unavailable marks.
Do not turn premiums received, daily theta, or option win rate into account ROI.
Report pre-tax results: dividends and eligible cash interest are included only
from dated inputs; taxes are excluded and no after-tax superiority is claimed.
Freeze the fee/slippage schedule, stress multipliers, cash-yield methodology,
interest on reserved cash, and reinvestment timing across strategy and benchmark.
Cash-secured experiments do not borrow or earn interest twice on the same funds;
if those assumptions cannot be populated, comparisons remain blocked.

## 5. Evaluation and promotion gates

1. **Freeze a small protocol.** Resolve every `unresolved_protocol` field,
   candidate unresolved item, cost assumption, and lifecycle scenario. Keep
   `performance: null` until an actual completed run supplies evidence. Choose
   exact timing/filters/limits and version before observing results. A and B first;
   C only after ledger tests. Add a volatility or rally filter in a later
   one-factor ablation, not a Cartesian parameter search.
2. **Use chronological data.** Obtain at least five years plus indicator warmup,
   spanning bull/bear/volatile/quiet conditions where available. Reserve the
   final 12 months untouched. On the earlier period use rolling 24-month
   development / 6-month validation windows. Keep position campaigns intact;
   purge overlapping outcome windows, use only observations known at decision
   time, and embargo by maximum campaign horizon. Define that maximum before
   testing; extending expiry through rolls would invalidate the split.
3. **Use realistic execution.** Short entries at bid and buybacks at ask plus
   fees; include stressed spreads, gaps, partial/unfilled orders, dividends,
   assignment and cash funding. No fills at stale marks or on a bar whose close
   generated the signal. Missing data may lower coverage, never manufacture an
   exit or a win. A model-only price study stays separate from quoted replay.
4. **Compare total return and risk.** Same-capital buy-and-hold with dividends,
   cash/T-bill alternatives, and the component baselines. Report CAGR, drawdown,
   downside-tail loss, time underwater, capital utilization, turnover, missed
   upside, net return and complete-cycle expectancy. Use portfolio/calendar
   block confidence intervals; overlapping trades are not independent samples.
   [Cboe BXM](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf)
   and [put-write methodologies](https://cdn.cboe.com/api/global/us_indices/governance/WPTR_Methodology.pdf)
   provide transparent comparison designs, not directly comparable single-stock
   results or return promises.
5. **Predeclare success and failure.** Before running, set minimum independent
   campaign counts, acceptable drawdown, data coverage, and a benchmark/risk
   objective. Reject a result driven by one ticker/regime or surviving only at
   optimistic costs. Insufficient samples means inconclusive, even with a high
   win rate. No threshold has been passed in this review.
6. **Separate software validation from financial validation.** Unit tests can
   enforce a disabled manifest and conservation of shares/cash. They cannot
   establish an edge. Historical testing, later holdout, forward Simulation,
   independent review, capability/account checks, and an explicitly authorized
   execution-contract expansion would all precede any real short-option order.

## 6. Safe implementation sequence

First build and test the cash/share/assignment ledger without a broker. Then
audit data coverage, freeze one candidate protocol, and run the component
comparisons. Only afterward consider analyst presentation or a strategy engine.

The existing optional Claude reviewer may challenge sourced research assumptions
inside its bounded evidence packet. It must not choose live orders, loosen
limits, rewrite deterministic rules, or override a failed test. A human changing
the objective creates a new version requiring review; it does not retroactively
make an earlier test successful.

**Result of this pass:** source knowledge and test design captured; no backtest
run, no success-rate estimate, no supported short-option executor, and no live
profitability claim. The current Simulation-only local posture is unchanged.
