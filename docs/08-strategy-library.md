# 08 — Strategy Library

Canonical registry of every strategy the engine can propose. Each entry in code is a `StrategyDef` declaring: module(s), universe, trigger TF, entry template, exit/management template, factor-weight vector, hard-gate overrides, and its calibration table. This doc is the human-readable catalog.

## 1. Registry Format

```yaml
key: BULL_PUT_SPREAD
modules: [SWING]
direction: BULLISH
vehicle: OPTIONS_SPREAD
trigger_tf: D
dte: {min: 30, max: 45}
legs:
  - {side: SELL, type: PUT, delta: [0.15, 0.25]}
  - {side: BUY,  type: PUT, width_below: budget_fit}
entry: {price: "net credit ≥ width/3", placement: "short strike below major support AND below 1x expected move"}
management: {tp: "50% of credit", stop: "2x credit or short-strike breach on D close", time_exit: "21 DTE"}
gates: {ivr_min: 40, pop_min: 0.72, earnings_inside_cycle: false}
rr_metric: POP_BASED
```

## 2. Catalog Overview

### Directional — options buying
| Key | Module | Shape | Core edge |
|---|---|---|---|
| ORB_BREAKOUT | 0DTE | long call/put | Opening-range momentum + confirmation (RULE-T3/T4) |
| VWAP_RECLAIM | 0DTE | long call/put | Intraday fair-value flips (RULE-T6) |
| EMA9_TREND_PULLBACK | 0DTE | long call/put | Trend continuation at dynamic support (RULE-T5) |
| LEVEL_REJECTION | 0DTE | long call/put | Confluence reversals (counter-trend rules) |
| EVENT_MOMENTUM | 0DTE | long call/put | Post-print direction persistence |
| DAILY_BREAKOUT | Swing | long call | Base breakouts w/ volume (RULE-T9) |
| PULLBACK_CONTINUATION | Swing | long call/put | Buy dips in trends at EMA/AVWAP confluence |
| POST_EARNINGS_DRIFT | Swing | long call | PEAD anomaly + catalyst (RULE-A2/L3) |
| OVERSOLD_QUALITY_BOUNCE | Swing | long call | Quality names at panic levels (RULE-B2) |
| RS_LEADER | Swing | long call | Relative-strength leadership |
| ANALYST_UPGRADE_MOMENTUM | Swing | long call / shares | Tier-1 upgrade catalyst in an existing uptrend (Benzinga feed; chase-guarded) |
| LEAPS_STOCK_REPLACEMENT | LEAPS | deep ITM call | Capital-efficient compounding |
| LEAPS_GROWTH_CONVEXITY | LEAPS | ATM+ call | Cheap long vega on fast growers |
| LEAPS_PUT_THESIS | LEAPS | long put | Defined-risk catalyst short |
| INDEX_LEAPS_DIP | LEAPS | long call | Drawdown-ladder fear buying |

### Premium selling / income (theta engines)
| Key | Module | Shape | Core edge |
|---|---|---|---|
| CREDIT_SPREAD_TREND | 0DTE | put/call credit spread | Same-day theta behind gamma walls |
| IRON_CONDOR_RANGE | 0DTE | condor | Range-day premium capture |
| IRON_FLY_PIN | 0DTE | butterfly | Opex pin at max gamma |
| BROKEN_WING_FLY | 0DTE | skewed fly | Credit-financed directional lean |
| BULL_PUT_SPREAD / BEAR_CALL_SPREAD | Swing | vertical credit | Trend + IV overpricing, POP ≥ 72% |
| IRON_CONDOR_45 | Swing | condor 30–45 DTE | Range + high IVR |
| EARNINGS_IV_CRUSH | Swing | iron condor over earnings | Implied > historical move overpricing |
| CALENDAR_PUTS_CALLS | Swing | calendar | Term-structure edges |
| CSP_WHEEL_ENTRY | Swing/HODL | cash-secured put | Paid to bid at accumulation zones |
| PMCC | LEAPS | diagonal | Income against LEAPS |
| DIAGONAL_LADDER | LEAPS | staggered diagonals | Smoothed income |
| ZEBRA | LEAPS | ratio call spread | Stock exposure, no extrinsic |
| LEAPS_RISK_REVERSAL | LEAPS | short put + long call | Financed convexity on conviction names |
| DYNAMIC_COLLAR | LEAPS | shares + puts − calls + cash | Flagship survivorship engine (doc 06 §2) |
| COVERED_CALL_OVERLAY | HODL | short call vs shares | Yield on stagnant-zone holdings (strikes above Expensive-zone boundary so upside to fair value is never capped away) |

### Designed-for-positive-skew additions (engine-original)
| Key | Module | Idea |
|---|---|---|
| GAMMA_WALL_FADE | 0DTE | Fade moves into dominant dealer-gamma strike in final 2 hours on pin-probability score; defined-risk fly |
| VOL_CRUSH_MONDAY | Swing | Sell weekend-inflated premium Monday open on range-bound liquid names (systematic variance-risk premium) |
| SKEW_ARB_COLLAR | LEAPS | Enter Dynamic Collars specifically when put-skew is cheap vs calls (collar cost near zero) — "free insurance" detector |
| LADDERED_LOTTO_BUDGET | 0DTE | Optional 5%-of-budget bucket for >3:1 asymmetric event plays, hard-capped, labeled LOTTO, excluded from calibration stats |
| DISPERSION_LITE | Swing | Long single-name premium vs short index premium when correlation extreme (advanced, phase 3) |
| FAILED_AUCTION_ROTATION | 0DTE | Look-above/below-and-fail at a reference → defined-risk vertical toward the opposite extreme of the accepted range (doc 16) |
| VALUE_AREA_80PCT | 0DTE | 80%-rule value-area re-entry → ride to opposite VA extreme (doc 16) |
| BALANCE_BREAK_GO | Swing | Breakout from multi-day balance with initiative volume; targets scaled to balance width (doc 16) |
| RESPONSIVE_FADE | Swing | Bracket-extreme fade on falling volume into the extreme (doc 16) |
| STRUCTURE_TURN | Swing | Trend-health turning-point entry: weakening trending legs + strengthening retracements at higher-TF structure (doc 15) |
| LOCKUP_SUPPLY_EVENT | Swing | IPO lockup expiration (MarketBeat calendar) on extended recent-IPO names: bear call spread or long put into the supply event; requires D downtrend confirmation + borrow/IV sanity; also powers the *defensive* rule — no long alerts within 5 sessions of any universe name's lockup |
| SMART_MONEY_CONFLUENCE | HODL/LEAPS | ≥ 2 tracked superinvestors (Dataroma 13F) newly bought + name in Attractive valuation zone + insider cluster buys (Form 4) → high-conviction accumulation card (conviction signal, technical triggers still time the entry) |

## 3. Shared Management Doctrine

- Defined risk always for short premium; naked short options are **not representable** in the schema (deliberate).
- Profit-taking defaults: verticals 50% · condors 25–50% · flies 25% · long options tranche at TP zones (33/33/34) with milestone stop-ratchets (doc 05 §4).
- Time exits: short premium at 21 DTE (swing) / 15:55 (0DTE); long options at 50% of entry DTE if thesis unproven.
- Roll doctrine (from the brief): roll winners up/out, don't fight (RULE-6 of collar); never roll a loser more than once; rolls must collect credit or buy demonstrable protection.
- Every strategy's alert prints its management plan — the user should never wonder "now what?".

## 4. Adding a Strategy (governance)

New strategies require: written thesis (edge source), ≥ 3y walk-forward backtest (or 12m for 0DTE) with per-bucket calibration table, replay-harness run on curated sessions, and a 30-day `SHADOW` research period under operator `SIMULATION` (evaluated + logged, not alerted), then promotion. Same pipeline demotes underperformers automatically (doc 03 §4.3).
