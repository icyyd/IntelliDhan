# Late-window option observations

The pilot looks for direction in SPY and QQQ, then measures a **long 0DTE
option**: a call for an upside setup or a put for a downside setup. The earlier
[three-year study](38-sniper-study-and-release.md) measured ETF price changes.
It did not answer whether a trader could buy an affordable contract on a
trigger and sell it at a profit. This recorder tests that narrower question.

## Session contract

| Clock (New York) | What gets recorded |
| --- | --- |
| Before 10:30 | Scheduled only. The recorder makes no OpenD requests. |
| 10:30 up to, but not including, 11:15 | Observe new setups using completed five-minute candles and a fresh underlying trigger. Look for same-day standard SPY/QQQ contracts. |
| From 11:15 | No new entries. Follow an already open observation to its exit; a flat recorder can finish. |
| 11:30 | Request a timed exit for any remaining observation. A short retry window allows a genuine quote **after** the exit decision. Never backdate it to 11:30. |
| 11:35 | Stop retrying. Without a valid exit quote the observation remains unresolved, not a scratch trade or a presumed loss. No rollover. |

The original paper account remains separate: $1,000 starting cash, a $500
premium ceiling, a $500 full-risk daily budget, no same-day reuse of proceeds
and at most one position at a time. Late observations account for the morning
account's actual settled cash, losses and most recent exit, as well as previously
observed late debits and losses. They cannot consume or change primary capital.
After an exit, a new full candle must close inside the opening range before a
fresh breakout, retest and trigger. There is no fixed trade-count quota.

## Evidence for an option result

- **Setup:** the underlying and direction, trigger, stop, target, signal time,
  and why the level qualified. The target is in the ETF; it is not a promised
  percentage gain in the option. A retest that only arms the setup is counted
  separately from a fresh underlying quote that actually crosses the trigger.
- **Contract:** expiration, strike, call/put, delta and liquidity. In the bounded
  scan of nearby standard strikes, prefer the highest eligible absolute delta
  (0.50–0.80), subject to spread, volume, open interest, fresh prices and cost.
  A rejected or missing contract is not replaced with shares.
- **Quotes:** keep the bid, ask, sizes, provider-side timestamps and option
  snapshot time. For admission both book sides must be timestamped at or after
  the observed trigger; an old ask cannot price a new trade. The freshness limit
  is ten seconds. Optional volatility and Greeks are context, not substitute
  premiums. OpenD documents that initial cached responses may lack valid book
  timestamps. The recorder cannot assume these quotes are fresh or describe
  a displayed book as a guaranteed fill. [OpenD order book](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-order-book.html)
  and [option snapshot fields](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html).
- **Outcome:** one standard 100-share contract, modeled at ask plus $0.01 per
  share for entry and at bid minus $0.01 (floor zero) for exit, with $0.65
  per side in fees. Record the exit decision before pricing it. The bid/ask
  spread is already reflected in these modeled fills; don't subtract it twice.
  For example, a $2.00 entry ask and $2.20 exit bid imply a $16.70 net gain
  after modeled costs. That's a calculation example, not a market observation.

## Operating and reading the journal

From the paper-journal feature checkout:

```sh
DATA=/Users/dhanvin/Documents/IntelliDhan/data/paper-journal-2026-11-20
.venv/bin/python scripts/paper_late_options.py start --session-date 2026-11-20 --data-dir "$DATA"
.venv/bin/python scripts/paper_late_options.py report --session-date 2026-11-20 --data-dir "$DATA"
```

The authorized date is **November 20, 2026 only**. The Mac must stay awake
and signed in to OpenD. The separate recorder reads the matching local paper
observer and uses only market-data calls. It reserves quote capacity for other
work and never subscribes to paid data, trades at a broker, or publishes these
observations to the hosted account journal.

Keep the raw quote records in the private dated research directory. Coverage
counts describe what the worker could watch, not whether an option was
available at a usable price. If only one ETF can be observed, coverage is partial.
Compare
setups observed, skipped/unfillable setups, opened observations and resolved
round trips before looking at P/L or win rate. A session with no closed trades
has **no measured win rate**. An incomplete exit must stay visible alongside
closed results. A single date and a small number of observed contracts cannot
establish an options trading edge.
