# 04 — Module: 0DTE

Same-day expiration options on the most liquid index/ETF complex. Highest velocity, highest discipline requirements — this module leans hardest on the Technician's Codex and the tightest risk caps.

## 1. Universe & Instruments

| Group | Symbols | Instrument notes |
|---|---|---|
| Index options | **SPX, NDX** | Cash-settled, European, no early assignment, §1256 tax treatment — preferred for spreads |
| Core ETFs | SPY, QQQ, **SMH** | SMH has M/W/F expiries; treat nearest expiry ≤ 1 DTE as "0DTE-class" |
| Leveraged ETFs | TQQQ, SQQQ, SOXL, SOXS, SPXL, SPXS, UPRO | Options often cheaper in absolute $; engine may express an NDX signal via TQQQ calls when budget-fit is better (see sizing, doc 09) |

Session windows: entries allowed 9:35–15:50 ET (RULE-T12); all 0DTE suggestions carry a hard "flatten by" time (default 15:55 ET) printed on the alert.

## 2. Sub-sections

### 2.A Directional (calls/puts) — "Momentum Desk"
Playbooks (full parameters in doc 08):

| Strategy key | Trigger sketch | Bias source |
|---|---|---|
| `ORB_BREAKOUT` | Break of 9:30–10:00 range + 2 consecutive 5m closes beyond (RULE-T3/T4) + rel-vol ≥ 1.5× + VWAP same side | 15m/1H/D alignment |
| `VWAP_RECLAIM` | Loss and reclaim of VWAP with 2-candle confirmation; entry on first pullback that holds VWAP | Intraday trend flip |
| `EMA9_TREND_PULLBACK` | Established intraday trend (price riding 9 EMA on 5m); entry on tag of 9/21 EMA with RSI holding ≥ 60 (long) / ≤ 40 (short) | Trend continuation (highest historical win-rate class) |
| `LEVEL_REJECTION` | Rejection wick + engulfing at level-map confluence (gamma wall, prior day H/L) with RSI divergence | Mean reversion — counter-trend rules apply (85% bar) |
| `EVENT_MOMENTUM` | Post-print (CPI/FOMC) direction after lockout expires: 2×5m closes agreeing with post-event VWAP side | Macro surprise score |
| `FAILED_AUCTION_ROTATION` | Look above/below a reference fails (no acceptance: ≤1 TPO period, low volume) and price re-enters range → target opposite extreme (doc 16 §2.3) | Auction structure |
| `VALUE_AREA_80PCT` | Open outside prior VA; re-entry holds 2 consecutive 30-min periods inside → ride to opposite VA extreme | Value-area mechanics |

Contract selection (directional): delta 0.30–0.45, prefer strikes at/just beyond the nearest OI wall in the trade direction; spread ≤ 5% of mid; expected move sanity check (TP1 must sit inside 1× expected move).

### 2.B Advanced Strategies — "Income Desk"
Defined-risk premium structures exploiting 0DTE theta:

| Strategy key | Structure | When engine proposes it |
|---|---|---|
| `CREDIT_SPREAD_TREND` | Bull put / bear call spread, short strike ≈ 10–15 delta, beyond expected move & behind a gamma wall, width per budget | Trending or quietly drifting day aligned with D/1H trend; IV elevated vs realized |
| `IRON_CONDOR_RANGE` | Both-side condor, short strikes outside expected move | Range-day detection: ADX(5m) < 18, price pinned between walls, no tier-1 events |
| `IRON_FLY_PIN` | Butterfly centered on max-pain / dominant gamma strike, entered post-14:00 | Opex pin behavior; small size, lotto-risk-labeled |
| `BROKEN_WING_FLY` | Skewed fly with zero/low risk on one side | Directional-lean day where credit can finance the structure |

Premium structures always: defined risk, credit ≥ ⅓ width (spreads), management rule printed on alert (close at 50–65% max profit or 2× credit loss; never hold short gamma into final 30 min unless fully behind walls).

## 3. Module-Specific Signal Inputs

- **Opening range** (9:30–10:00) high/low/mid; ORB quality score (range vs 5-day average, gap context).
- **Intraday level map:** overnight H/L, prior day H/L/C, weekly open, SPX/NDX round numbers, **options walls** — max OI/gamma strikes for today's expiry, refreshed every 15 min; dealer-gamma flip level estimate.
- **Expected move** for the day's expiry (ATM straddle) — every 0DTE alert displays it.
- **Breadth pack** for index trades: NYSE TICK, advance/decline, % of NDX members above VWAP, equal-weight vs cap-weight divergence.
- **Leveraged ETF handling:** signals computed on the *underlying index* (NDX for TQQQ/SQQQ, SOX for SOXL/SOXS) then translated; decay/beta-slippage warnings on any hold-past-close suggestion.
- **Auction/profile pack (doc 16):** open-type classification (posted by 10:05 ET; Open-Drive opens become hard reversal references), open location vs. prior value area, Initial Balance + range-extension tracking, developing VA/POC (prominent-POC magnets), day-type probability meter, one-timeframing indicator, failed-auction watch, p/b shape read, overnight-inventory (trapped longs/shorts) card. Trend-day detection **overrides** all mean-reversion playbooks for the session.
- **Intraday pressure score (doc 15 §7):** uptick/downtick imbalance, NBBO drift, micro-structure state, absorption warnings — a live F4 input on the cockpit.

## 4. Risk Caps (0DTE-specific)

| Cap | Default |
|---|---|
| Max risk per alert | 25% of daily 0DTE budget |
| Max concurrent open | 2 (RULE-A1) |
| Max alerts/day | 5 |
| Cooldown after 2 stops | 90 min |
| Hard flatten time | 15:55 ET, alert reminder at 15:45 |
| Counter-trend bar | 85% confidence + half size |

## 5. UI (module screen)

Three-pane layout (see doc 11): live SPX/NDX/SMH chart rack with ORB + VWAP + walls drawn; the day's active alerts as cards on the right rail; bottom drawer = intraday tape of evaluated-but-suppressed setups ("why we're quiet" transparency feed, RULE-M3).

## 6. Success Metrics (module)

Rolling 30-trade: win rate ≥ 68% directional / ≥ 78% premium; avg R ≥ +0.45/trade; max daily drawdown within budget cap 100% of days; calibration gap ≤ 5 pts.
