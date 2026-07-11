# 03 — Signal & Confidence Engine

The heart of IntelliDhan. Converts market state into **rare, high-conviction, fully-specified alerts**. Design goals: transparent (every score decomposable), calibrated (claimed 75% ≈ realized 75%), and conservative by default.

## 1. Pipeline Overview

```
Bar close (trigger TF) ─▶ Strategy trigger predicate ─▶ raw Setup
Setup ─▶ Factor scoring (8 factors) ─▶ Composite score (0–100)
Composite ─▶ Calibration map (per strategy class) ─▶ Calibrated confidence %
Confidence ≥ threshold? ─▶ Suppressor checks ─▶ Alert Composer
                └─ else ─▶ logged as SUB_THRESHOLD (for calibration learning)
```

Every evaluated setup — alerted or not — is persisted with its full factor vector and later outcome. This is the training set that keeps calibration honest.

## 2. Multi-Timeframe Trend Engine (MTF)

Per symbol, per timeframe ∈ {M, W, D, 4H, 1H, 15m, 5m}, computed on bar close:

**Trend state** = weighted vote of:
| Component | Bullish condition | Weight |
|---|---|---|
| EMA stack | 9 > 21 > 50 (rising) | 0.30 |
| Price vs 9 EMA (RULE-T5) | close > 9 EMA | 0.15 |
| Market structure | higher highs & higher lows (last 2 pivots) | 0.25 |
| RSI regime (RULE-T7) | RSI ≥ 60 strong / 50–60 lean / 40–50 lean-bear / ≤ 40 weak | 0.15 |
| ADX/DMI | ADX ≥ 20 and +DI > −DI | 0.15 |

Output per TF: `STRONG_UP / UP / NEUTRAL / DOWN / STRONG_DOWN` + numeric score −100..+100.

**Alignment Matrix** (rendered on every alert and chart page):

```
        M    W    D    4H   1H   15m  5m
TQQQ    ▲▲   ▲▲   ▲    ▲    ▲▲   ►    ▲
```

**Alignment score** for a candidate direction = weighted agreement across the module's relevant TFs:
| Module | TFs & weights |
|---|---|
| 0DTE | D 0.20 · 1H 0.20 · 15m 0.30 · 5m 0.30 |
| Swing | W 0.20 · D 0.35 · 4H 0.30 · 1H 0.15 |
| LEAPS | M 0.35 · W 0.35 · D 0.30 |
| HODL | M 0.50 · W 0.30 · D 0.20 (used for entry timing only) |

## 3. The Eight Confidence Factors

Composite = Σ (factor × weight). Weights vary per strategy class (each strategy declares its vector); defaults:

| # | Factor | Measures | Default wt |
|---|---|---|---|
| F1 | **Trend alignment** | MTF alignment score in trade direction (RULE-T1/T2) | 22% |
| F2 | **Setup quality** | Strategy-specific trigger strength: ORB break cleanliness, 2-candle confirmation (RULE-T4), pattern completion %, base tightness | 18% |
| F3 | **Level confluence** | Entry proximity to level-map confluence (VWAP, prior day H/L, gamma walls, S/R, round numbers) — margin of safety RULE-C1 | 14% |
| F4 | **Momentum & volume** | Relative volume ≥ 1.5× (RULE-T9), MACD state, RSI slope, breadth (for index trades: % above 20DMA, TICK, A/D) | 12% |
| F5 | **Volatility fit** | IV rank vs structure appropriateness (RULE-T11); expected move vs target distance; theta profile sanity | 12% |
| F6 | **Flow & smart-money positioning** | Put/call skew, unusual volume on chain, OI walls supporting direction, dealer-gamma proximity; **insider/institutional overlay** (Form 4 cluster buys, Dataroma superinvestor adds — slow-moving, weighted for Swing/LEAPS/HODL only) | 8% |
| F7 | **Macro/catalyst context** | MacroContext regime agreement (incl. Fed-cycle state & rate-path deltas, doc 02 §7); catalyst presence (RULE-A2): earnings w/ whisper data, **analyst upgrades/PT raises**, 8-K events; event-risk penalty (earnings/FOMC/**IPO lockup expiry** inside trade horizon unless the strategy is an event play) | 8% |
| F8 | **Statistical POP** | Model probability of profit: options-pricing-derived POP for the exact structure (delta/expected-move based), plus historical base rate of this setup class | 6% |

Each factor is 0–100 with a documented rubric; the alert UI shows the factor breakdown as a horizontal bar stack (doc 11).

**Hard gates (binary, applied before scoring):** liquidity RULE-T8, data quality OK, no event lockout RULE-T12, universe whitelist RULE-M4, extension check RULE-M2 (entry not > 2 ATR from mean for momentum entries), reward:risk ≥ strategy minimum (directional ≥ 2:1; premium-selling structures use POP ≥ 70% + credit ≥ ⅓ width instead), **M.A.E. grammar satisfied** (structure + area of value + trigger, doc 15 §3).

**Auction-state vetoes (doc 16):** trend-day detection vetoes all mean-reversion strategies; active one-timeframing vetoes counter-trend signals; p-shape (short-covering) profile vetoes momentum longs (b-shape mirrors for shorts); non-conviction days suppress volume-based signals. Vetoes fire *after* scoring and are logged with the suppressed setup — the "why we're quiet" feed shows them.

**Structural factor enrichment:** F2 gains pattern-detector outputs and candle CLV/relative-size reads (doc 15 §4–5); F3 gains value-area edges, POC magnets, and flipped-level bonuses; F4 gains MACD states/divergences and the intraday pressure score (doc 15 §6–7); F1 gains market-stage gating and value-vs-price divergence (doc 16 §3).

## 4. Calibration — making "75%" mean 75%

1. **Backtest prior:** every strategy ships with a walk-forward backtest (≥ 3 years where data allows; 0DTE: ≥ 12 months of 1-min data) producing a mapping `composite score bucket → historical win rate`.
2. **Calibration map:** confidence % = isotonic-regression fit of realized win rate vs composite score, per strategy class. A composite of 82 might map to 74% for 0DTE ORB longs but 79% for swing bull put spreads.
3. **Live re-calibration:** trade-log outcomes (doc 10) update the map weekly (exponentially weighted, so regime changes surface fast). If a strategy's rolling-30-trade realized win rate drops > 10 pts below claimed, it is **auto-demoted**: threshold raised to 85% and flagged "under review" on the dashboard.
4. **Threshold:** alerts require calibrated confidence ≥ **75%** (counter-trend: ≥ 85%). User-adjustable upward only.
5. **Transparency widget:** dashboard shows claimed-vs-realized calibration curve per module, all-time and rolling-90-day. (RULE-C2: we grade the process publicly.)

**Definition of "win" for calibration:** trade reaches TP1 before stop (directional), or expires/closes ≥ 50% max profit before breach (premium structures) — matching the management rules the alert itself states.

## 5. Suppressors & Throttles

| Suppressor | Rule |
|---|---|
| Event lockout | No new alerts −2 min/+5 min around tier-1 prints (CPI, PPI, FOMC, NFP, PCE); no 0DTE entries 9:30–9:35 or after 15:50 ET (RULE-T12) |
| Module cooldown | 2 consecutive stopped-out alerts in a module same day → 90-min cooldown (discipline layer) |
| Concurrency caps | 0DTE: 2 open · Swing: 5 · LEAPS: 6 · HODL: 10 (RULE-A1) |
| Duplicate guard | Same symbol+direction+strategy within ATR-based distance of a live alert → merged, not re-alerted |
| Degraded data | Any input feed stale > 30 s (intraday TFs) → symbol suppressed + ops alert |
| Correlation cap | Max 2 simultaneous open suggestions in the same correlated cluster (e.g., QQQ/TQQQ/NDX/SOXL count as one cluster) |

## 6. Setup Object (canonical schema)

```jsonc
{
  "setup_id": "stp_20260710_093512_tqqq_orb",
  "module": "0DTE",
  "strategy": "ORB_BREAKOUT_CALL",        // registry key, doc 08
  "symbol": "TQQQ", "direction": "LONG",
  "trigger_tf": "5m",
  "mtf_matrix": { "D": 62, "1H": 71, "15m": 80, "5m": 85 },
  "factors": { "F1": 84, "F2": 78, "F3": 70, "F4": 88, "F5": 66, "F6": 61, "F7": 75, "F8": 72 },
  "composite": 77.9,
  "confidence": 0.78,                      // calibrated
  "hard_gates": { "liquidity": true, "rr": 2.4, "extension_atr": 1.1 },
  "levels": { "entry_underlying": 84.62, "stop_underlying": 83.90, "tp_underlying": [85.40, 86.10, 87.00] },
  "context": { "macro_regime": "risk_on", "next_event": "CPI 2026-07-14 08:30", "iv_rank": 38 },
  "explain": "5m ORB break above 84.55 with 2-candle confirmation, 2.1x rel-vol, reclaim of VWAP; 15m/1H/D aligned up; entry at ORH retest confluence with VWAP."
}
```

The Alert Composer (doc 09) turns a `Setup` into a concrete options/equity order plan.

## 7. Accuracy & Testing Requirements

- Indicator unit tests vs TA-Lib and hand-computed fixtures; golden-file tests for MTF trend states on recorded sessions.
- **Replay harness:** any historical session can be replayed bar-by-bar through the full engine; CI replays 20 curated sessions (trend day, chop day, CPI day, opex, half-day) and asserts alert set stability.
- Determinism: same inputs → same alerts (no wall-clock reads inside the engine; market clock injected).
- Backtests use conservative fills: entries at signal-bar close + slippage model (options: mid + 40% of half-spread), never intra-bar optimistic fills.
