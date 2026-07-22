# SPY/QQQ 9EMA 0DTE Auto-Trader

**Status:** real-quote Simulation lifecycle implemented; **not live eligible**

**Strategy key:** `EMA9_MTF_0DTE`

**Broker boundary:** official Robinhood Trading MCP through Codex only

**Last evidence pass:** 2026-07-21

## Decision

IntelliDhan now monitors a simple 9EMA continuation setup on SPY and QQQ. It
records qualified signals and their paper outcomes, but it cannot create a live
alert or executable broker intent. Historical results were too sparse and
regime-dependent to justify real-money promotion.

The operator surface has only `SIMULATION` and `LIVE`. Simulation is the safe
default and records real option-quote entries/exits without orders. Live is
time-limited, but the strategy's explicit evidence gate still blocks it from
creating an executable order.

## Exact setup

The engine evaluates completed 5-minute candles only from 10:00 through 15:15
ET. A setup must meet every condition below:

1. The underlying is SPY or QQQ.
2. The 5-minute trend score is at least +40 for calls or at most −40 for puts.
3. The 15-minute and 1-hour scores agree by at least 20 points; the daily trend
   cannot oppose the trade.
4. The 9EMA is above the 21EMA on 5-minute, 15-minute, and 1-hour frames for a
   bullish setup, with the inverse required for a bearish setup.
5. The trigger candle tags the 5-minute 9EMA and closes back in the trend
   direction, on the correct side of VWAP, with a directional candle body.
6. RSI must remain in a continuation regime (52–76 bullish, 24–48 bearish),
   relative volume must be at least 0.8×, and the close must finish in the
   directional 40% of the candle.
7. The stop sits beyond the 5-minute 21EMA or trigger extreme plus 0.15 ATR.
   Targets are 1R, 2R, and 3R. Existing concurrency, duplicate, correlation,
   extension, profile, data-quality, and session gates still apply.

The strategy is marked `live_eligible=false` and `shadow_monitor=true` in code.
The normal engine therefore places a qualifying setup into a separate research
queue. The live loop persists a `SHADOW` alert and underlying paper trade,
updates separate research controls, publishes a `shadow_signal` event, and
creates a non-executable Simulation intent for Codex to enrich with official
Robinhood option quotes. It does not send Telegram trade instructions.

## Historical result

The research harness used 55 calendar days / 37 market sessions of currently
available 5-minute data and an untouched chronological split:

| Segment | Trades | TP1 before initial stop | Average result |
|---|---:|---:|---:|
| Train | 14 | 7.1% | −0.768R |
| Validation | 11 | 63.6% | +0.498R |
| Test | not scored | — | — |

No variant met the predeclared validation requirement of at least 30 trades,
75% TP1-before-stop, positive expectancy, and training consistency. Because no
winner qualified on validation, the test segment was not opened. The reversal
between train and validation is evidence of instability, not improvement.

The calibration artifact contains no confidence buckets and explicitly sets
`live_eligible` to false. Consequently, no composite score can promote this
strategy through the live confidence gate.

## Capital and broker controls

The auto-trade policy now carries `max_available_capital_fraction: 0.80`. Before
claiming any future live intent, Codex must fetch fresh buying power from the
dedicated Robinhood Agentic account and submit it to the authenticated
`capital-review` endpoint. The application rejects stale/wrong-account reviews
and blocks required capital above that ceiling.
An expired two-minute claim lease cannot be reclaimed with the old observation;
Codex must submit another fresh capital review first.
The selector uses the largest whole-contract position inside every active
threshold. It caps capital at the lesser of 80% of fresh buying power,
per-order dollar risk, and remaining daily dollar risk. It filters 0/1DTE
contracts for direction, fresh two-sided quotes, ≤10% spread, minimum volume and
open interest, and affordability; among survivors it chooses the highest
absolute delta, then sizes the maximum whole-contract count at the current ask.
For long options, premium paid is treated as maximum order risk.

The application does not store Robinhood credentials or account identifiers.
It stores the timestamped buying-power amount used for each sizing decision so
the threshold calculation is auditable. Contract selection and every pre-trade
review must use the official runtime MCP schema. Option-premium performance is reported only when a
Simulation intent has a validated contract plus real-quote entry and exit
receipts; the older underlying paper ledger remains separately labeled.

## Winner management

The auto-trade plan uses `TREND_BREAK_FULL_EXIT`: it holds the full position
while completed 5-minute candles stay on the trend side of the 9EMA. After
+1R, the risk reference moves to breakeven. It exits every contract on a closed
5-minute 9EMA break, opposing 15-minute trend, hard stop, data-quality failure,
or the broker contract's sellout deadline. It never averages down and does not
use fixed profit targets that prematurely cap a runner.

## Audit surface

Authenticated users can request `GET /api/trade-log` to retrieve, newest first:

- persisted signal plans, including `research_only` and `SHADOW` status;
- underlying-level paper trades and outcomes;
- real-option Simulation entry/exit events with their reasoning; and
- append-only execution-intent lifecycle events, including claims and broker
  receipts for other eligible strategies. These global broker records are
  visible only to ADMIN and legacy-owner sessions; other accounts receive an
  empty execution-event list.

Every future broker intent includes the multi-cap maximum sizing rule, required
fresh buying-power check, dedicated Agentic-account scope, protective-exit
requirement, broker review plus confirmation, and the existing abort conditions.

## Promotion checklist

Do not make the strategy live eligible until all items pass on a newly frozen
rule set:

1. At least 30 validation observations and 15 untouched test observations, with
   positive expectancy and stable results across SPY, QQQ, bullish, bearish,
   high-volatility, and low-volatility slices.
2. Walk-forward and forward-paper evidence that beats buy/no-trade baselines
   after option spread, slippage, and fees.
3. Same-day Robinhood contract discovery with minimum volume/open interest,
   maximum spread, appropriate delta, and no silent equity fallback.
4. Simulation reconciliation of signal, selected contract, hypothetical fill,
   protection, outcome, and expiry.
5. An independently reviewed calibration artifact with
   `live_eligible: true`, followed by explicit, time-limited operator selection
   of `LIVE` and broker-required confirmation of each reviewed order.

No step may be bypassed because a recent validation slice looks favorable.
