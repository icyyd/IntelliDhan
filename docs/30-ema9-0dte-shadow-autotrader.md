# SPY/QQQ 9EMA 0DTE SHADOW Auto-Trader

**Status:** implemented for monitored research and underlying-level paper tracking; **not live eligible**

**Strategy key:** `EMA9_MTF_0DTE`

**Broker boundary:** official Robinhood Trading MCP through Codex only

**Last evidence pass:** 2026-07-21

## Decision

IntelliDhan now monitors a simple 9EMA continuation setup on SPY and QQQ. It
records qualified signals and their paper outcomes, but it cannot create a live
alert or executable broker intent. Historical results were too sparse and
regime-dependent to justify real-money promotion.

This is deliberate. “Auto-trader” currently means automated monitoring,
deterministic paper execution, and durable audit collection. Live orders remain
behind the evidence and operator gates in docs 27 and 28.

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
queue. The live loop persists a `SHADOW` alert and paper trade, updates separate
research concurrency controls, and publishes a `shadow_signal` event. It does
not send Telegram trade instructions or call the broker-intent path.

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
Eighty percent is a maximum exposure, not a sizing target: per-order dollar
risk, daily loss, open-intent, liquidity, and protective-exit limits may reduce
the order substantially. The system never upsizes a small risk-defined plan to
consume the ceiling.

The application does not store Robinhood credentials, account identifiers, or
buying power. Contract selection and every pre-trade review must use the official
runtime MCP schema. Until exact same-day option selection, spread/liquidity
validation, protective exits, and receipt reconciliation pass in SHADOW and
SUPERVISED modes, the paper ledger measures the underlying plan only and must
not be presented as option-premium performance.

## Audit surface

Authenticated users can request `GET /api/trade-log` to retrieve, newest first:

- persisted signal plans, including `research_only` and `SHADOW` status;
- underlying-level paper trades and outcomes; and
- append-only execution-intent lifecycle events, including claims and broker
  receipts for other eligible strategies. These global broker records are
  visible only to ADMIN and legacy-owner sessions; other accounts receive an
  empty execution-event list.

Every future broker intent includes the 80% buying-power ceiling, required fresh
buying-power check, `upsize_to_ceiling=false`, dedicated Agentic-account scope,
protective-exit requirement, and the existing abort conditions.

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
4. SHADOW reconciliation of signal, selected contract, hypothetical fill,
   protection, outcome, and expiry.
5. An independently reviewed calibration artifact with
   `live_eligible: true`, followed by explicit operator authorization for
   `SUPERVISED`. `ARMED` remains a separate, time-limited decision.

No step may be bypassed because a recent validation slice looks favorable.
