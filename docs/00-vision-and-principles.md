# 00 — Vision & Investing Principles

## 1. Vision

IntelliDhan is a personal quant desk in a browser: it watches the market continuously across every timeframe, applies the combined judgment of the best investors and technicians in history, and speaks up **only** when the odds are strongly favorable — with a complete, ready-to-execute trade plan.

The product must feel like a **Bloomberg terminal designed by a great consumer app team**: information-dense yet instantly legible, fast, dark-mode-first, and beautiful.

## 2. The Investor Council (Fundamental Rulebook)

Every module encodes these principles as hard rules or scoring factors. They are not decoration — each maps to a concrete check in the signal engine (cross-referenced as `RULE-*` throughout the spec).

### Charlie Munger — Rationality & Inversion
| Rule ID | Principle | Engine implementation |
|---------|-----------|----------------------|
| RULE-M1 | *Invert, always invert* — ask how the trade fails first | Every alert must include a computed "failure scenario" (what invalidates it) and place the stop there |
| RULE-M2 | Avoid stupidity rather than seek brilliance | Setup blacklist: no chasing >2 ATR extended moves, no illiquid chains (see RULE-T8), no earnings-eve naked short options |
| RULE-M3 | Sit on your hands — big money is in the waiting | The engine has **no minimum alert quota**; zero-alert days are expected and displayed as "No edge today" |
| RULE-M4 | Circle of competence | Universe is whitelisted per module; the engine never alerts symbols outside the configured universe |

### Warren Buffett — Capital Preservation & Quality
| Rule ID | Principle | Engine implementation |
|---------|-----------|----------------------|
| RULE-B1 | Rule #1: never lose money; Rule #2: see Rule #1 | Max risk per alert ≤ configured % of daily budget (default 25% for 0DTE, 33% swing); portfolio-level daily loss kill switch (see doc 13) |
| RULE-B2 | Be fearful when others are greedy, greedy when others are fearful | Contrarian overlay: VIX percentile + put/call ratio + Fear & Greed inputs raise buy-the-dip scores in HODL/LEAPS modules during panic (mirrors "Buy the Fear" ladder in the Dynamic Collar strategy) |
| RULE-B3 | Wonderful company at fair price > fair company at wonderful price | HODL screener requires quality gates (ROIC, moat proxies) *before* valuation gates |
| RULE-B4 | Favorite holding period: forever | HODL module never emits sell alerts on price noise; only on thesis break (fundamental deterioration) or extreme overvaluation |

### Peter Lynch — Know What You Own & GARP
| Rule ID | Principle | Engine implementation |
|---------|-----------|----------------------|
| RULE-L1 | Know what you own and why | Every alert carries a plain-English thesis line ("Why this trade") — no alert ships without it |
| RULE-L2 | PEG discipline (growth at reasonable price) | Swing/LEAPS equity candidates scored on PEG; PEG > 2 caps confidence contribution from fundamentals at zero |
| RULE-L3 | Earnings drive stocks | Earnings calendar is a first-class input: earnings-date proximity modifies every score and is displayed on every alert |
| RULE-L4 | Categorize the stock (stalwart / fast grower / cyclical / turnaround) | HODL/LEAPS candidates are auto-tagged with a Lynch category that adjusts expected-hold and TP logic |

### Bill Ackman — Concentration, Catalysts & Hedging
| Rule ID | Principle | Engine implementation |
|---------|-----------|----------------------|
| RULE-A1 | Few, high-conviction positions | Alert throttle: max concurrent open suggestions per module (0DTE: 2, Swing: 5, LEAPS: 6, HODL: 10) |
| RULE-A2 | Catalyst-driven theses | Swing/LEAPS scoring includes an explicit catalyst factor (earnings, product events, macro prints, index rebalances) |
| RULE-A3 | Asymmetric payoffs; cheap convexity | Structure selector prefers defined-risk shapes whose reward:risk ≥ 2:1 for directional trades; flags cheap-IV convexity opportunities |
| RULE-A4 | Hedge tail risk explicitly | Dynamic Collar / protective-put templates are permanent members of the strategy library; portfolio hedge health shown on dashboard |

### Shared Council Doctrine
- **RULE-C1 (Margin of safety):** entries are placed at level-confluence zones (support, VWAP, prior value areas), never mid-air.
- **RULE-C2 (Process over outcome):** the trade log grades process adherence separately from P&L (doc 10).
- **RULE-C3 (Compounding math):** avoid large drawdowns; a 50% loss needs a 100% gain. Drawdown-aware sizing shrinks risk after losses (anti-martingale, doc 09).

## 3. The Technician's Codex (Chart Discipline)

Encodes the day-trading rulebook (ORB, 2-candle rule, 9 EMA, VWAP, RSI 40/60) plus classical multi-timeframe practice. These are `RULE-T*` and power the technical score in doc 03.

| Rule ID | Rule | Detail |
|---------|------|--------|
| RULE-T1 | **Trade the trend** | Trend determined per timeframe via EMA stack (9/21/50/200), higher-highs/higher-lows structure, and ADX. Counter-trend alerts require explicit tag + higher confidence bar (85%) |
| RULE-T2 | **Multi-timeframe alignment** | Every alert computes trend on Monthly / Weekly / Daily / 4H / 1H / 15m / 5m (module-appropriate subset) and displays the alignment matrix. Alignment score is a top-3 weighted factor |
| RULE-T3 | **ORB (Opening Range Breakout)** | 0DTE: opening range = 9:30–10:00 ET high/low; trade the confirmed break (with RULE-T4) |
| RULE-T4 | **2-Candle Confirmation** | Signals require 2 consecutive closes beyond the key level on the trigger timeframe — confirmation over prediction |
| RULE-T5 | **9 EMA bias** | Price above rising 9 EMA = bullish bias; below falling = bearish (per timeframe) |
| RULE-T6 | **VWAP is intraday fair value** | Above VWAP = bullish, below = bearish; VWAP rejections/reclaims are first-class events; anchored VWAP (from swing highs/lows, earnings) for swing module |
| RULE-T7 | **RSI 40/60 decision zones** | RSI > 60 = strength regime, < 40 = weakness regime; 40–60 = chop (no-trade zone for momentum entries); divergences flagged |
| RULE-T8 | **Liquidity gate** | Options: bid-ask spread ≤ 10% of mid (0DTE: ≤ 5%), OI ≥ 500, volume ≥ 100 on the alerted strike; equities: ADV ≥ $20M. Fail = no alert, ever |
| RULE-T9 | **Volume confirms** | Breakouts need ≥ 1.5× 20-period relative volume; low-volume breaks are downgraded to "watch" |
| RULE-T10 | **Key levels first** | Engine maintains a level map per symbol: prior day H/L/C, overnight H/L, weekly/monthly open, gaps, major S/R, round numbers, options walls (max gamma/OI strikes) |
| RULE-T11 | **Respect volatility regime** | IV rank/percentile decides structure: high IVR → sell premium (spreads, condors); low IVR → buy premium (debit structures, LEAPS) |
| RULE-T12 | **No-trade windows** | First 5 min after open, 2 min around major econ prints (CPI, PPI, FOMC, NFP), final 10 min for new 0DTE entries — signal generation suppressed, labeled "event lockout" |

## 4. Personal Discipline Layer (from the user's 4 Disciplines)

1. **Trade the trend** → RULE-T1/T2 enforced in scoring.
2. **Use a stop loss, every time** → alerts without a computed stop are structurally impossible (schema requires it, doc 09).
3. **Control position size** → sizing derived from daily capital budget and per-trade risk caps (doc 09); the UI never shows a trade without its dollar risk.
4. **Don't revenge trade or overtrade** → after 2 consecutive stopped-out alerts in a module in one day, that module enters cooldown (no new alerts for 90 minutes, dashboard shows "Cooldown — protect the mind"). Daily alert caps per module.

## 5. The Reference Shelf (source texts encoded in this spec)

| Text | Where it lives |
|---|---|
| Mark Douglas, *Trading in the Zone* | Probabilistic voice, consistency framework, error taxonomy → doc 17 §1–3 |
| Jared Tendler, *The Mental Game of Trading* | A/B/C-game, emotional maps, mental hand history, tilt detection → doc 17 §3–4 |
| James Dalton, *Markets in Profile* | Auction theory layer: value areas, open/day types, failed auctions, profile-shape vetoes → doc 16 |
| Rayner Teo, *Ultimate Guide to Price Action Trading* | 4-stage model, S/R engine, M.A.E. entry grammar, candlestick heuristics → doc 15 |
| Chart/MACD/tape cheat sheets | Pattern detector library, MACD signal states, intraday pressure score → doc 15 §5–7 |

The Personal Discipline Layer (§4 above) is superseded in detail by doc 17's intervention ladder — §4 remains the summary.

## 6. What IntelliDhan Is Not

- Not an ungoverned auto-trader: `SIMULATION` is the default and cannot place
  orders. Separately authorized, time-limited `LIVE` may stage eligible intents
  through Codex and the official Robinhood MCP only after broker review and
  explicit confirmation, and
  it cannot bypass data-quality, strategy, account, price, protection, or risk
  gates and can be switched back to Simulation at any time.
- Not a get-rich-quick machine: expected edge is modest and compounding-driven.
- Not financial advice: all outputs carry the standing disclaimer (doc 13).
