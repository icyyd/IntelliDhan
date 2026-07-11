# 05 — Module: Swings (Skips/Swings)

Positions held **2–20 trading days**, options-forward with equity support. The BABA 115C alert in the product brief is the archetypal output of this module: clean entry, milestone updates, trailing-stop guidance.

## 1. Universe & Horizon

- Config watchlist (~40 liquid large caps + index/sector ETFs, doc 02 §6).
- Options: 2–8 weeks to expiration (never < 2× expected hold, so theta doesn't eat the thesis); delta 0.35–0.55 for directional longs.
- Equity swings offered in parallel when options are budget-inefficient or IV is punitive (engine states which vehicle it picked and why).

## 2. Sub-sections

### 2.A Directional (calls/puts) — "Swing Desk"

| Strategy key | Trigger sketch |
|---|---|
| `DAILY_BREAKOUT` | Base/flag breakout on D with 2 daily closes above pivot (RULE-T4), rel-vol ≥ 1.5×, W trend up, base depth/tightness scored (F2) |
| `PULLBACK_CONTINUATION` | Uptrending D (9>21>50 EMA); pullback to 21/50 EMA or anchored VWAP with reversal candle + RSI holding > 40; entry on 4H confirmation |
| `POST_EARNINGS_DRIFT` | Earnings beat + gap-and-hold above pre-earnings high; enter D2–D3 on first 4H higher-low (RULE-L3, RULE-A2 catalyst). Whisper-beat (beat Earnings Whispers' number, not just consensus) scores materially higher — whisper beats drift harder |
| `ANALYST_UPGRADE_MOMENTUM` | Tier-1 upgrade or ≥ 15% PT raise (Benzinga feed) on a name already in a D uptrend; enter on first 1H pullback that holds VWAP-from-upgrade; skip if move gapped > 1× expected move at open (chase guard, RULE-M2) |
| `OVERSOLD_QUALITY_BOUNCE` | Quality-screen name, RSI(D) < 30 into major support confluence + W trend still up; counter-trend rules (85%) |
| `RS_LEADER` | Relative-strength leader (RS line new high vs SPX) breaking out while index consolidates |
| `BALANCE_BREAK_GO` | Breakout from multi-day balance (overlapping value areas) with initiative volume; targets scale with balance width (doc 16 §3) |
| `RESPONSIVE_FADE` | Bracket-extreme fade when volume *falls* into the extreme (rising volume into the extreme invalidates — it breaks instead) |
| `STRUCTURE_TURN` | Higher-TF structure + trending legs weakening while retracement legs strengthen (doc 15 §4) → break-of-structure entry on lower TF; counter-trend rules apply |

### 2.B Advanced Strategies — "Premium Desk"

| Strategy key | Structure | When |
|---|---|---|
| `BULL_PUT_SPREAD` | 15–25 delta short put spread, 30–45 DTE, below major support + expected move | Uptrend + IVR ≥ 40; POP ≥ 72% |
| `BEAR_CALL_SPREAD` | Mirror above resistance | Downtrend/failed breakout + elevated IV |
| `IRON_CONDOR_45` | 30–45 DTE condor outside expected move | Range-bound D chart, IVR ≥ 45, no earnings inside cycle |
| `EARNINGS_IV_CRUSH` | Short strangle-shaped defined-risk (iron condor) around earnings, strikes beyond implied move × 1.2 | Only when implied move ≥ 1.5× historical avg move (edge = overpricing); small size |
| `CALENDAR_PUTS_CALLS` | Long back-month / short front-month at expected pin | IV term-structure inversion opportunities |
| `CSP_WHEEL_ENTRY` | Cash-secured put at accumulation zone on HODL-approved names | Bridges to HODL module: "get paid to bid" (RULE-B2) |

Management rules printed on every premium alert: take profit at 50% of max (condors 25–50%), exit at 21 DTE regardless, roll rules, and the "never fight a broken thesis" stop.

## 3. Swing-Specific Signal Inputs

- Anchored VWAPs from: last earnings date, 52-week high/low, major swing pivots.
- Market-stage label (doc 15 §1) gates strategy eligibility; composite-profile balance boxes, day-over-day value migration, excess/poor-extreme flags, and yesterday's-trade continuation score (doc 16 §3) render on every swing chart.
- Sector RS (vs SPX) and peer confirmation (a semiconductor signal scores higher when SMH agrees).
- Catalyst calendar per symbol: earnings date **and confirmed time** (Earnings Whispers) with implied move (internal ATM-straddle calc, cross-checked vs OptionsAI expected-move tool), whisper vs consensus EPS, ex-div, product events, analyst rating changes (Benzinga), **IPO lockup expirations** (MarketBeat — hard warning flag on any long alert within 5 sessions of a lockup), macro exposure tags (RULE-A2).
- Smart-money context: recent Form 4 insider cluster buys/sells (EDGAR) and Dataroma superinvestor position changes shown on the candidate card; cluster insider *selling* into a breakout is a scored negative.
- Fundamental overlay (lightweight): PEG, revenue growth, margin trend from RH fundamentals — caps F7 for junk-quality names (RULE-L2), fully gates `OVERSOLD_QUALITY_BOUNCE` and `CSP_WHEEL_ENTRY` to quality names.
- Earnings blackout: no *new* short-premium alerts if earnings fall inside the structure's life (unless the strategy IS the earnings play).

## 4. Lifecycle & Milestone Updates (BABA-style)

Every swing alert spawns a **tracker** that emits follow-ups to web + Telegram:

| Event | Message content |
|---|---|
| +25% / +50% / +60% / +70% / +100% option P&L milestones | Current option price, % gain, **new suggested stop** (ratchet: at +50% stop→breakeven; +70% stop→+40%; +100% stop→+60% and trail 4H higher-lows) |
| TP zone touched | Which tranche to trim (T1 33%, T2 33%, runner 34% default) |
| Stop threat | Underlying closes beyond stop on trigger TF → "stop hit — exit" notice |
| Theta warning | < 14 DTE with thesis intact → roll-out suggestion with cost |
| Thesis break | MTF alignment flips against position → early-exit advisory even if stop not hit |

## 5. Risk Caps

Max risk per alert 33% of swing budget; max 5 concurrent; max 2 per sector cluster; earnings-hold requires explicit "event trade" label; counter-trend at 85% + half size.

## 6. Success Metrics

Rolling 30: win rate ≥ 60% directional (avg winner ≥ 2× avg loser) / ≥ 75% premium; profit factor ≥ 1.8; calibration gap ≤ 5 pts.
