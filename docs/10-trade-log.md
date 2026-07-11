# 10 — Trade Log & Performance Tracking

Every alert is tracked to a terminal outcome whether or not the user takes it. The log serves three masters: user review, engine calibration (doc 03 §4), and honest public-to-self accountability (RULE-C2).

## 1. Lifecycle States

```
SUGGESTED ──(user: Took it / MCP fill detected)──▶ TAKEN
    │                                                │
    ├─(validity window lapses)──▶ EXPIRED_UNTAKEN    ├─▶ STOPPED / TP1..TPn / FLATTENED_TIME / THESIS_EXIT
    └─(engine cancels: stale/degraded)──▶ CANCELLED  └─▶ CLOSED_MANUAL (user closed off-plan)
Every alert ALSO runs a parallel PAPER track: simulated fills at entry-zone
mid, managed exactly per the printed plan → paper outcome recorded even for
untaken alerts. Calibration uses the PAPER track (uniform, unbiased);
user P&L uses the real track.
```

## 2. Record Contents

Per alert: full alert snapshot (immutable), factor vector, paper fills/exits with timestamps and bar evidence, real fills (from Telegram button ack + Robinhood `get_pnl_trade_history` reconciliation), realized P&L (real + paper), R-multiple, MAE/MFE (max adverse/favorable excursion — did the stop breathe right?), process-adherence grade (entered inside zone? honored stop? trimmed at TPs?), and a post-trade chart snapshot.

## 3. Views (web)

| View | Contents |
|---|---|
| **Ledger** | Filterable table (module, strategy, symbol, state, date); each row expands to the full alert card + outcome chart |
| **Performance** | Equity curves (paper vs taken), win rate, profit factor, avg R, expectancy, by module/strategy/DOW/time-of-day heatmaps |
| **Calibration** | Claimed-vs-realized confidence curves (doc 03 §4.5), per strategy class, with demotion status badges |
| **Discipline** | Error-frequency dashboard grouped by root cause (Douglas's four fears; Tendler's tilt/confidence/discipline families — full detector table in doc 17 §3); per-trade Consistency Score (0–7 principles honored); cooldowns/interventions triggered; 20-Trade Sample Mode tracking with skipped-signal counterfactual P&L; A/B/C-game range chart; euphoria-guard and equity-threshold events |
| **Journal** | Free-text + tags on any trade; Mental Hand History guided flows and Emotional Map entries (doc 17 §4) linked to trades; weekly auto-generated review draft ("what worked / what didn't") |

## 4. Metrics Definitions (single source of truth)

- Win rate = wins / decided (excludes CANCELLED/EXPIRED_UNTAKEN from denominators; LOTTO-labeled excluded from headline stats).
- Expectancy = (WR × avgWin) − ((1−WR) × avgLoss), in R.
- Profit factor = gross wins / gross losses.
- Calibration gap = |claimed − realized| per 5-pt confidence bucket, rolling 90d.
- All stats shown with sample sizes; buckets under n=20 render as "insufficient data" — never false precision.

## 5. Exports & Retention

CSV/JSON export; nightly snapshot backup; records immutable after terminal state (corrections append, never overwrite) — the log must be trustworthy enough to audit the engine.
