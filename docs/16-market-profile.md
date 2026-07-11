# 16 — Market Profile & Auction Theory Layer

Source: James Dalton, *Markets in Profile*. This layer gives the engine a structural read of *why* price is moving — who is participating, whether value agrees with price, and whether an auction is beginning, continuing, or exhausted. It is a first-class signal input alongside the indicator pipeline: several of its states act as **vetoes** on other strategies, which is where most of its edge lives.

Design principle (Dalton's, adopted platform-wide): the engine outputs **continuation / stagnation / reversal odds and asymmetric-opportunity flags — never price forecasts** — and volume is only interpretable after attempted direction is determined.

## 1. Profile Construction

- TPO profiles: 30-min periods bucketed per price tick; per-price volume kept alongside (real volume preferred over TPO count where feed allows).
- **POC** = price with max TPO/volume ("fairest price"). **Value Area** = ~70% of volume built outward from POC (two-price pairs above vs. below, add the larger, repeat).
- Composite profiles: merge N sessions for bracket/balance analysis (swing timeframe).
- Computed for: SPX/NDX/SMH complex + full swing universe; intraday developing profile updates each 30-min period close.

## 2. Intraday State Machine (0DTE module inputs)

### 2.1 Open classification (first conviction read of the day)
| Open type | Detection | Signal |
|---|---|---|
| **Open-Drive** | First 1–2 periods one-directional; open at extreme of developing range; open price never revisited | Highest conviction. Open becomes a hard reference: later trade back *through* an Open-Drive open = "something changed" → reversal odds spike. Out-of-balance + with higher-TF trend → expect elongation and value migration (raise trend-day probability); inside prior range/against trend → moderate move only |
| **Open-Test-Drive** | Early probe beyond a known reference (prior H/L) finds no business, reverses hard back through open within 1–2 periods | Second-highest conviction; tested extreme likely holds as day's extreme → directional bias with defined risk at the test |
| **Open-Rejection-Reverse** | Directional open rejected via single-print tail, reverses through open | Moderate conviction; require confirmation (elongation, one-timeframing, volume) before directional alerts |
| **Open-Auction (in range)** | Random rotation around open, inside prior value | Non-conviction day → suppress momentum signals for first 5 periods |
| **Open-Auction (out of range)** | Rotation but open outside prior range | Out-of-balance: quick return into prior range/VA → target opposite extreme of that range; no return → go with the out-of-balance direction |

Open type + open location (vs. prior day's VA and range) posts to the 0DTE dashboard by 10:05 ET and modulates F1/F2 for the rest of the session.

### 2.2 Initial Balance & day-type evolution
- **IB** = first two 30-min periods. Range extension beyond IB with acceptance (TPOs building) escalates day-type toward trend day; both-side extension returning to mid = neutral/balancing day.
- **Trend day detection:** range ≥ k×ATR, ≥70% of periods make directional progress, elongated profile, close in extreme decile → **never fade; overrides all mean-reversion strategies** (`IRON_CONDOR_RANGE`, `LEVEL_REJECTION`, `GAMMA_WALL_FADE` are hard-vetoed). Gap + elongation + volume + close-on-extreme → next-day continuation bias.
- **Non-conviction day:** attempted direction indeterminate → volume-based signals suppressed entirely.

### 2.3 Auction quality rules (all computable)
| Rule | Detection | Action |
|---|---|---|
| **Failed auction / look-above-and-fail** | Probe beyond reference (prior day H/L, balance edge) gains no acceptance (≤1 TPO period, low volume) and price re-enters range | Signal rotation to the **opposite extreme** of the accepted range — feeds `LEVEL_REJECTION` and a new `FAILED_AUCTION_ROTATION` strategy |
| **One-timeframing** | Successive periods never take out prior period's opposite extreme | **Veto all counter-trend signals** while active ("never fade a one-timeframing auction") |
| **Excess (tail)** | ≥2 single-TPO prints at a day extreme formed in one period + low relative volume at the extreme + reversal ≥ X% of range | Auction end marker → arms opposite-direction setups; the longer the tail, the stronger |
| **Poor high/low** | Same extreme printed in ≥2 periods (no excess) | Weak structure → expect revisit; repeated equal lows = market "too short" → short-covering rally risk flag |
| **Prominent POC** | Unusually wide POC line | Gravity anchor/magnet: intraday target; expect revisit next session absent high-volume breakout |
| **Spike rule** | Late-day directional spike | Withhold judgment until next open: value builds within/beyond spike → acceptance/continuation; open back through spike → excess/reversal |
| **80% rule** *(Mind Over Markets lineage, kept as named strategy)* | Open outside prior VA, then price re-enters and holds 2 consecutive 30-min periods inside | Target the opposite VA extreme — implemented as `VALUE_AREA_80PCT` (0DTE income/directional hybrid) |

### 2.4 Profile shape — the momentum veto
- **p-shape** after a rally (≥ ~60–65% of TPO mass in upper third, thin stem): **short covering, old business** → label "low continuation odds"; vetoes momentum longs (this is Dalton's trend-trader trap — trend systems that buy short-covering rallies get reversed).
- **b-shape** after a decline: long liquidation being absorbed by patient buyers → upside-risk flag; advises short exits even on weak closes.
- **Squat/fat profile** after directional days = auction tiring → early-warning on trend-continuation scores.
- Elongation metric (range ÷ max TPO width) = initiative-conviction gauge feeding F2/F4.
- **Overnight inventory:** settle vs. where 60–70% of overnight volume traded → trapped-shorts/longs read at the open ("inventory, inventory, inventory") — displayed on the 0DTE cockpit.

## 3. Swing/Bracket Layer

- **Balance detection:** N consecutive overlapping VAs (or inside days) = bracket. Markets bracket >75% of the time — the swing module's default assumption.
- **Breakout-from-balance:** "go with any breakout from balance in your timeframe," volume-confirmed; wider balance broken → bigger expected move (target scaling). Feeds `DAILY_BREAKOUT` targets and validity.
- **Bracket-extreme logic:** rising volume into an extreme → breakout likely (don't fade); falling volume → extreme holds (responsive fade toward opposite extreme). Feeds `IRON_CONDOR_45` placement and `LEVEL_REJECTION`.
- **Initiative vs. responsive classification** of every range extension/tail vs. prior VA: initiative acceptance = trend signal; responsive dominance = bracket confirmation.
- **Value vs. price divergence (daily bias):** classify developing VA vs. prior (higher / overlapping-higher / unchanged / overlapping-lower / lower / outside). *Value matters more than price*: price up + value unchanged/lower = weak (early-reversal input). Directional attempt on rising volume = valid; on falling volume = failing. Rising price + falling volume → mean below price → revisit odds high.
- **Trend age:** spacing between successive balance areas (well-separated = young; stacked = aging) + late-trend checklist (countertrend auctions as strong as with-trend + declining volume) → modulates F1 for swing/LEAPS and feeds the briefing's trend-health commentary.
- **Yesterday's-trade score:** per prior session compute attempted direction, volume vs. average, VA placement class, shape (p/b/elongated/squat) → continuation-confidence 0–100, shown on swing charts and in the daily briefing index read.

## 4. Context & News Doctrine (from the book, merged into MacroContext)

- News has lasting impact in **brackets**, little in high-confidence **trends**; contrary news causing only a temporary setback = trend *confirmation* (score boost, not penalty).
- Stay flat ahead of scheduled releases (already RULE-T12); fade release spikes only at pre-mapped references.
- Widely-watched technical levels (200-DMA etc.): volume decides — increasing volume into the level → it breaks; decreasing → it holds. Applied to level-strength scoring in F3.
- Flight-to-safety regimes suspend normal price/volume logic; expect full retrace through low-volume zones when unwound → MacroContext gets a `FLIGHT_TO_SAFETY` state that raises confidence bars platform-wide.

## 5. New Strategy Registrations (→ doc 08)

| Key | Module | Sketch |
|---|---|---|
| `FAILED_AUCTION_ROTATION` | 0DTE | Look-above/below-and-fail at a reference → rotation target = opposite extreme of accepted range; defined-risk vertical toward target |
| `VALUE_AREA_80PCT` | 0DTE | 80% rule re-entry → ride to opposite VA extreme |
| `BALANCE_BREAK_GO` | Swing | Breakout from multi-day balance with initiative volume; targets scaled to balance width |
| `RESPONSIVE_FADE` | Swing | Bracket-extreme fade on falling volume into the extreme; invalid if volume rises |

All pass the standard confidence gate; profile-state vetoes (§2.2–2.4) apply to the *whole* strategy registry, which is where this layer most improves accuracy.

## 6. UI Additions

- **Profile pane** on 0DTE and swing charts: TPO/volume profile with VA shading, POC line (prominent-POC highlighted), tails/single prints, p/b badges, open-type chip, one-timeframing indicator, IB brackets.
- 0DTE cockpit bottom drawer gains: open classification card, overnight-inventory read, day-type probability meter (updated each period), failed-auction watchlist.
- Swing charts gain: composite-profile ribbon, value-migration arrows (day-over-day VA class), balance-area boxes with age/spacing annotations.
