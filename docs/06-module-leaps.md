# 06 — Module: LEAPS

Long-dated options (9–30 months) as capital-efficient exposure to high-conviction theses, plus the **income-generating hedged structures** that make long exposure survivable — headlined by the **Dynamic Collar** (the TQQQ strategy from the product brief), which is a permanent flagship of this module.

## 1. Sub-sections

### 1.A Directional LEAPS (calls/puts)

| Strategy key | Sketch |
|---|---|
| `LEAPS_STOCK_REPLACEMENT` | Deep ITM call (0.70–0.80 delta), 12–24 months out, on quality-screen names in M/W uptrends; entry timed at D-pullback confluence (buy fear, RULE-B2) |
| `LEAPS_GROWTH_CONVEXITY` | 0.50–0.60 delta, 18–30 months on Lynch "fast grower" tags with PEG < 1.5 (RULE-L2) and low IV rank (buy premium cheap, RULE-T11) |
| `LEAPS_PUT_THESIS` | Long-dated puts on deteriorating fundamentals + broken W/M structure (rare; Ackman-style catalyst short with defined risk) |
| `INDEX_LEAPS_DIP` | SPY/QQQ LEAPS calls triggered by the drawdown ladder (below) |

**Drawdown ladder** (mirrors "Buy the Fear" from the Dynamic Collar, applied index-wide):
- −10% from ATH → scale 1 (25% of LEAPS dry powder)
- −20% → scale 2 (35%)
- −30% or VIX > 40 → scale 3 (40%)
Each rung still requires a W-timeframe stabilization trigger (2 weekly closes reclaiming the 9 EMA or a confirmed D higher-low) — no falling knives.

### 1.B Advanced / Income Structures

| Strategy key | Structure | Role |
|---|---|---|
| `DYNAMIC_COLLAR` | Long shares + long 1-year ~70%-strike puts + short-dated covered calls + cash reserve | Flagship — full spec §2 |
| `PMCC` (Poor Man's Covered Call) | Long 0.75Δ LEAPS call + rolling short 30–45 DTE 0.20–0.30Δ calls against it | Income on capital-efficient exposure; short call pays down LEAPS cost basis |
| `LEAPS_RISK_REVERSAL` | Sell long-dated put at accumulation zone to finance LEAPS call | High-conviction names only; assignment risk = "getting paid to buy where we wanted to" |
| `ZEBRA` | 2× 0.70Δ calls − 1× short 0.50Δ call (zero extrinsic) | Stock-like exposure without theta bleed |
| `DIAGONAL_LADDER` | Staggered LEAPS + quarterly short-call ladder | Smooths income vs single PMCC |

## 2. Flagship Spec — Dynamic Collar (TQQQ Strategy)

Direct implementation of the 6-step strategy in the brief. Engine automates the *management state machine*; user supplies capital allocation.

**Setup (per the brief's example, TQQQ at $80, 1,000 shares):**
1. **Core:** long TQQQ shares (or QQQ variant, config).
2. **Protection:** buy 1-year puts at ~70% of spot, $5-strike increments (spot $80 → $55–$60 strike), 1 put per 100 shares.
3. **Income:** sell short-dated (weekly/monthly) covered calls above current price, count ≤ share lots.
4. **Dry powder:** maintain cash reserve (default 30% of position value).

**Engine-managed state machine & alert triggers:**

| State trigger | Engine alert |
|---|---|
| Rally: spot ≥ put strike / 0.70 + $5 | **Roll puts up**: sell old put, buy new ~70% strike (brief step 5); shows net debit and the covered-call plan to earn it back |
| Call threat: spot within 1% of short call with W-trend STRONG_UP | **Roll up & out** (don't fight the rally — brief step 6, "protect upside participation") |
| Income window: IV pop (IVR ≥ 50) with price below recent high | **Sell calls**: strike selection above resistance walls, higher strikes preferred (rules of thumb from brief) |
| Drawdown ladder: −20/−30%, −40/−50%, crash | **Buy the fear**: deploy reserve tranches (buy more / buy aggressively / puts-are-now-cash opportunity) |
| Sideways decay: 60 days flat | Reminder: covered-call income is carrying put cost — show running "insurance funded %" metric |

**Dashboard card for each collar:** net delta, put protection level & months left, calls sold vs lots, income collected YTD vs put cost (insurance funded %), cash reserve %, and the risk list from the brief (calls capping upside, sideways bleed, deep-bear cash drain, roll cost, overtrading traps) each with a live status light.

## 3. LEAPS-Specific Signal Inputs

- Monthly/Weekly MTF dominance (M 0.35 · W 0.35 · D 0.30).
- IV rank on long-dated expiries (buy LEAPS when IVR < 30 preferred; alert notes vega cost otherwise).
- Fundamentals gate: quality screen (doc 07 §2) must pass for single names — LEAPS are compressed HODL theses (RULE-B3).
- Leveraged-ETF math: for TQQQ-class underlyings, engine models volatility-decay drag explicitly and displays "decay cost @ current realized vol" on every alert.

## 4. Lifecycle

Quarterly thesis review alert per open LEAPS (fundamentals re-check + M/W trend re-score); roll alert at 6–9 months remaining (preserve extrinsic); milestone trackers as in Swings but on weekly cadence; short-leg management alerts (rolls, 50% profit closes) in near-real-time.

## 5. Risk Caps

Single-name LEAPS ≤ 15% of LEAPS budget; index/collar structures ≤ 35%; total short-call coverage never exceeds long delta (no naked upside risk); max 6 concurrent theses (RULE-A1 concentration with sanity).

## 6. Success Metrics

Per-thesis IRR vs underlying buy-and-hold; income structures: premium collected vs protection cost ≥ 80% annually (collar goal: insurance ~fully funded); max drawdown of collared positions ≤ ½ of naked underlying drawdown.
