# SPY/QQQ opening-range sniper: what the data supports

Decision, November 19, 2026: run the repeating technical setup in **simulation**.
Record volume and volatility filters beside it. Do not make those filters mandatory,
sell an options win-rate claim, or enable live orders on the strength of this replay.
This study covers SPY and QQQ, not the SPX index or SPX options.

## What makes a new setup

1. Mark the high and low from 09:30–09:45 ET. Use completed regular-session
   candles only: OpenD's 09:35 label is the **end** of the 09:30 candle.
2. Wait for a five-minute close outside the range. Within the next three bars
   price must touch the broken boundary and close outside again, on the same
   side of both its nine-bar exponential average (9EMA) and its session
   volume-weighted average price (VWAP). This distinguishes a held level from a wick.
3. On the very next candle, require a fresh underlying quote beyond the retest
   high (call) or low (put). Skip a move that already reached the target.
   The initial stop sits 0.1 five-minute ATR beyond the retest candle. The target
   is twice the distance from trigger to stop. An adverse 9EMA close, 60 minutes
   in position, or 11:30 ET also requests an exit. Missing exit quotes leave an
   unresolved position, not an assumed fill.
4. After any exit, including on the other ETF, a full new five-minute bar must
   **start after or at the exit** and close inside the original range. A new
   breakout, new retest and new trigger must follow. A previously filled or
   invalidated setup is not another opportunity. There is no numerical quota,
   averaging down, same-day proceeds reuse, or obligation to trade.

The 10:30 entry cutoff is separate from the trade counter. The earliest initial
trigger is 09:55. With positive-duration trades and the required rebuild, the
current morning window typically has room for at most two entries. A later
cutoff changes the strategy and requires its own replay and forward comparison.
The paper ledger additionally requires a liquid, affordable, timestamped 0DTE
option and reserves both fees against risk and settled cash. These conditions
are not present in the ETF replay. See the [session runbook](37-paper-session-journal.md).

## What we measured

The [V2 evidence file](evidence/orb-sniper-v2-2026-11-19.json) contains source
hashes, dates, per-ETF results, exits, drawdowns, sensitivity and intervals.
OpenD supplied 60,198 end-labeled five-minute bars per ETF, including warmup.
753 exchange sessions had complete mornings: 530 from November 20, 2023 through
December 2025 and 223 from January 2 through November 19, 2026. Each arm gets
its own position path, one position at a time, SPY first on simultaneous signals.
Same-bar ambiguity favors the stop. Returns are noncompounded **ETF** basis points
(100 bps = 1%), net of 2 bps round trip; no leverage or dollar account is modeled.

| Later 223 sessions | Trades | Winning trades | Mean net bps/trade | Mean net bps/session | Total net bps at 5 bps cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| Technical rules | 75 | 34.7% | +0.49 | +0.16 | −188.54 |
| Plus opening volume | 36 | 47.2% | +8.93 | +1.44 | +213.33 |
| Plus previous VIX | 74 | 35.1% | +0.74 | +0.25 | −167.18 |
| Both filters | 35 | 48.6% | +9.71 | +1.52 | +234.69 |

Opening volume did most of the work. VIX removed just one additional trade.
The combined arm had 17 green days, 18 red days and 188 days without a trade.
It gained 10.57 bps/trade on SPY (21 trades) and 8.41 on QQQ (14). In the
earlier 530 sessions it **lost** 0.16 bps/trade across 90 trades. The later
five-session block-bootstrap interval for its improvement over technical alone
was +0.11 to +2.80 bps/session; its interval against doing nothing was
−0.002 to +3.153. The later dates were seen in the [V1 study](evidence/orb-regime-2026-11-19.json)
before evaluating V2. This is exploratory, not independent validation. Later,
technical-only re-entered on four days; the combined arm did not re-enter on any.

### Why these inputs, and where they stop

- **Relative volume:** divide today's first 15 minutes of volume by the median
  of the same interval over the preceding 20 exchange sessions (minimum five
  usable openings). A ratio of at least 1 means activity is at or above the
  recent median. Using the same time of day avoids comparing the opening surge
  with a quiet midday bar. [Equity ORB research](https://alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content)
  motivates testing volume; its stock-selection results do not establish an ETF options edge.
- **VIX:** compare the immediately preceding session's close to the fixed
  band 12 ≤ VIX < 30. This is a test of market conditions, not a direction call.
  It does not use today's closing VIX or substitute yesterday's value for a
  missing prior-session observation. VIX measures expected **30-day** SPX
  volatility, not the move remaining today. [Cboe history](https://www.cboe.com/tradable_products/vix/vix_historical_data);
  [VIX versus VIX1D](https://www.cboe.com/insights/posts/what-the-vix-and-vix-1-d-indices-attempt-to-measure-and-how-they-differ).
- **Gamma exposure (GEX):** open interest does not tell us which side a dealer
  owns. With a complete, recent same-symbol chain, the observer can report
  unsigned hedge sensitivity per 1% move: sum of gamma × known open interest ×
  100 × spot² × 0.01. Without timestamped chain coverage and known OI as of the
  decision, mark it unknown. All historical observations were unknown: there is
  **no GEX backtest, dealer positioning estimate, or GEX entry rule** here.
  [OpenD option snapshot fields](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html);
  [Cboe's analysis of dealer positioning](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact/).

## Reproduce and monitor

With an authenticated, entitled read-only OpenD connection and available historical
quota, run from the feature checkout. The downloader caches market data locally;
do not commit or redistribute the raw data. With the cache in place, replay is offline.

```sh
.venv/bin/python scripts/fetch_orb_study.py --start 2023-10-19 --end 2026-11-19
.venv/bin/python scripts/orb_regime_study.py --sniper --output /tmp/orb-sniper-v2.json
.venv/bin/python scripts/paper_regime_shadow.py report --session-date 2026-11-20 --data-dir /Users/dhanvin/Documents/IntelliDhan/data/paper-journal-2026-11-20
```

The forward observer freezes the opening inputs before the first possible entry.
Missing or late inputs remain unknown. It matches actual paper receipts and net
option P/L to each qualifying filter; it cannot reconstruct trades the baseline
never took. Compare it to the matched-baseline diagnostic (32 later trades),
not directly to the independent 35-trade combined path. The Mac must stay awake
and genuine provider-timestamped bid/ask quotes must arrive. Displaying an
OpenD connection or a $1,000 starting account is not evidence of a fill.

## From one paper seat to paid execution

Proposed review gates, not automatic switches:

1. Freeze the policy version and record at least 20 complete prospective
   sessions and 30 closed, timestamped **option** round trips across both ETFs.
   Extend observation if samples or regimes are thin. Record missed/blocked
   setups, fillability, spreads, fees, worst losing day, drawdown, freshness and
   time-to-exit. A larger sample is needed if uncertainty remains material.
2. Compare net option results against flat cash and the matched technical control
   with day-block intervals. Stress wider spreads, delayed fills and losing
   sequences. Require positive evidence after costs, no unresolved exits or
   unreconciled ledger events, and an independent review of the failure drills.
   Passing a session count or making money one day is not permission to go live.
3. Keep the present journal owner-private. A named research seat needs an
   organization/user entitlement, transactional capacity enforcement, idempotent
   billing events, revocation and reassignment audit, and tenant-isolation tests.
   Entitlement to signals must not imply access to the owner's journal or broker.
   Validate quote display, derived-data and automated use rights with the
   vendors before distributing data: [OPRA documents](https://www.opraplan.com/document-library)
   and [Cboe index data](https://www.cboe.com/data/global-indices-feed).
4. Subscriber execution is a separate future product. Obtain securities counsel's
   review of the actual service and jurisdictions, plus written broker/data
   authorization. Require isolated per-subscriber accounts, explicit revocable
   permission, per-seat risk budgets, reconciled receipts, and a tested kill switch
   that blocks new orders without abandoning open positions. No shared tokens,
   automatic promotion, billing launch, or live order is part of this change.

Set seat prices only after confirming data rights and costs. Monthly break-even
seats = fixed monthly cost divided by net seat price minus per-seat variable
cost, rounded up; there is no break-even at a nonpositive contribution per seat.
