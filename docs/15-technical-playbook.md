# 15 — Technical Playbook: Price Action, Patterns & Confirmation Signals

Source material: Rayner Teo's *Ultimate Guide to Price Action Trading*, the NewTraderU technical-analysis cheat sheet, and the user's chart/MACD/tape cheat sheets. This doc converts them into the engine's **pattern detector library** and enriches factor F2 (setup quality) and F4 (momentum). Everything here is computable — each detector has explicit geometry rules so it can be unit-tested against fixture charts.

## 1. Market Stage Model (Rayner's 4 Stages ↔ regime detection)

Computed on the Daily timeframe per symbol (Weekly for HODL), refreshed on bar close:

| Stage | Detection rules | Module posture |
|---|---|---|
| **1. Accumulation** | ≥ 5-month prior decline; range with defined S/R; 200-DMA slope ≈ flat (|slope| < 0.02%/day); price whipping across 200-DMA | Watch-only; HODL/LEAPS build zones; breakout of range resistance arms Stage-2 alerts |
| **2. Advancing** | Breakout from accumulation range; HH/HL sequence; price > 200-DMA; 200-DMA slope rising | Longs only (buy breakouts & pullbacks); short setups suppressed |
| **3. Distribution** | ≥ 5-month prior advance; range with defined S/R; 200-DMA flattening; whipping across 200-DMA | Tighten stops on longs; premium-selling favored; breakdown of support arms Stage-4 |
| **4. Declining** | Breakdown from distribution range; LH/LL; price < 200-DMA; 200-DMA slope falling | Shorts/puts only; HODL fear-ladder watches for capitulation into Stage 1 |

The stage label renders on every chart header and gates strategy eligibility (e.g., `DAILY_BREAKOUT` requires Stage 2 or a Stage-1→2 transition; `LOCKUP_SUPPLY_EVENT` scores higher in Stage 3/4).

## 2. Support/Resistance Engine (level quality)

Rayner's drawing rules, made algorithmic:
- Levels are **zones, not lines** (zone width = 0.25 × ATR(14) of the drawing timeframe).
- Auto-drawn from ≥ 200 bars of context; candidate pivots clustered; a level's **strength score** = f(touch count, recency, volume at touches, whether body or wick respected it).
- Only "obvious" levels surface (strength ≥ threshold) — mirroring "if you second-guess it, it's not a level."
- **Role-flip tracking:** broken support becomes resistance (and vice versa) with a `FLIPPED` tag — flipped levels score *higher* in F3 confluence (trapped-trader logic from the guide).
- **Dynamic S/R:** 20 MA and 50 MA tracked as dynamic zones in strong trends (respect count ≥ 3 makes them "respected MAs" usable as areas of value); trendlines/channels fit via pivot regression where ≥ 3 touches exist.

## 3. The M.A.E. Formula as the Universal Entry Grammar

Every directional strategy in the library is expressible as Rayner's three-step grammar — the engine enforces all three parts before F2 can score above 60:

1. **Market structure** — stage/trend context says *what* to do (long/short/stand aside). Maps to MTF trend engine (doc 03 §2).
2. **Area of value** — *where*: entry must be at a scored zone (S/R, respected MA, trendline, VWAP/AVWAP, value-area edge). Maps to F3 confluence. Entries "in mid-air" are structurally blocked (RULE-C1).
3. **Entry trigger** — *when*: a confirmed price-action trigger (candlestick reversal per §4, break-of-structure, or the module's 2-candle rule). Maps to F2.

## 4. Candlestick Intelligence (pattern-free reading + named patterns)

**Two-question heuristic** (Rayner's cheat-sheet method) — computed for every bar close on the trigger TF:
1. **Close location value:** `CLV = (close − low) / (high − low)`. CLV ≥ 0.75 → buyers control; ≤ 0.25 → sellers control (regardless of candle color — a green candle closing near lows is seller-controlled).
2. **Relative size:** body ≥ 2× median body of prior 10 bars → conviction; ≈ median → no strength.

These two values feed F2 directly and generate the plain-English candle commentary on alert cards ("strong rejection of lower prices — closed in top ¼ of range at 2.3× average size").

**Named reversal triggers** (used as M.A.E. entry triggers at areas of value):
| Pattern | Geometry | Signal |
|---|---|---|
| Hammer | little/no upper shadow; close in top ¼; lower shadow 2–3× body; after decline | Bullish rejection |
| Shooting Star | mirror of hammer; after advance | Bearish rejection |
| Bullish/Bearish Engulfing | 2nd body fully covers 1st body, opposite close | Control flip |

(Hammer ≈ lower-TF engulfing — detectors deduplicate across TFs so one event isn't double-counted.)

**Trend-health monitor (trending vs retracement legs):** per swing leg, compare median body size of with-trend legs vs counter-trend legs. Healthy trend = large trending bodies, small retracement bodies. **Weakness warning** when retracement bodies grow ≥ with-trend bodies — fires the "turning point watch" state (Rayner's bonus technique): at higher-TF structure + weakening trending legs + strengthening retracement legs → arm break-of-structure entry on the lower TF. Implemented as strategy `STRUCTURE_TURN` (Swing, counter-trend rules apply).

## 5. Chart Pattern Library (from the cheat sheets)

Detectors with target/stop geometry per the classical measured-move convention (pattern height projected from breakout):

**Continuation:** bull/bear flag · bullish/bearish pennant · bullish/bearish rectangle · cup-with-handle (and inverted) — require a preceding trending move (the "pole"); entry on breakout with volume ≥ 1.5× (RULE-T9) + 2-candle confirmation (RULE-T4).

**Bilateral (direction from the break):** ascending / descending / symmetrical triangles — engine pre-arms *both* break levels (entry-stop bracket above lower-highs slope and below higher-lows slope, per the cheat sheet) and alerts only on the confirmed side.

**Reversal:** double/triple top & bottom · head-and-shoulders and inverse — neckline break + volume + retest logic; these carry reversal-grade confidence requirements (85% bar when against the prevailing MTF trend).

Every detected pattern renders on the chart as an overlay (pattern outline + measured target zone) and contributes a named line to the alert thesis.

## 6. MACD Signal Sheet (from the MACD cheat sheet)

MACD(12,26,9) states tracked per TF and fed to F4:
| Signal | Rule | Weight |
|---|---|---|
| Centerline cross (bull/bear) | MACD line crosses 0 | Trend-regime confirmation |
| Signal-line cross (bull/bear) | MACD crosses signal | Swing timing input |
| Price divergence | Price LL vs MACD HL (bullish) / price HH vs MACD LH (bearish) | Reversal-watch flag; pairs with RSI divergence for `LEVEL_REJECTION` / `STRUCTURE_TURN` |
| Histogram divergence | MACD line LL vs histogram HL (and mirror) | Early leg-exhaustion warning (trend-health monitor input) |

Divergences alone never trigger entries — they arm reversal strategies that still require a §4 trigger at a §2 level.

## 7. Tape-Reading Checklist (from the tape cheat sheet)

Full time-and-sales data isn't in v1's feed; the computable subset becomes the **intraday pressure score** (0DTE module, F4 input):

| Cheat-sheet cue | v1 proxy |
|---|---|
| Bids stepping up / asks stepping down | NBBO midpoint drift over rolling 60 s |
| Most transactions on ask vs bid | Uptick/downtick trade imbalance (quote-rule approximation) |
| HH+HL vs LL+LH on the tape | 1-min micro market-structure state |
| SPY higher / sector higher | Index + sector agreement flags (already in breadth pack) |
| T&S accelerating without price progress | Volume burst + price stall = absorption warning (fades breakout scores) |

Full order-flow (spoof detection, big prints, holding the bid) is deferred to a phase-6 data upgrade and marked out-of-scope for v1 accuracy claims.

## 8. Indicator Doctrine Reminders (NewTraderU sheet)

Already codified but restated as engine invariants: ranges are defined by horizontal S/R and traded at the extremes or on confirmed breaks; MACD signals beginnings of swings (never sole trigger); RSI 30/70 marks classical OB/OS but IntelliDhan trades the 40/60 regime zones (RULE-T7) with 30/70 reserved for `OVERSOLD_QUALITY_BOUNCE`-class extremes; MAs filter trend per timeframe; every alert quantifies its signal, sets a stop at a key level, and trails winners (milestone ratchets, doc 05 §4).
