# Local forward-paper agent and journal

Status: implemented research pilot; not a validated profitable strategy.
Session: Friday, November 20, 2026, America/New_York.

The pilot observes SPY and QQQ prices and writes hypothetical option trades to
its own dollar ledger. It starts with $1,000 once. It does not use or change the
Robinhood intent pipeline, brokerage buying power, existing account database,
Live mode, or any real order tool. OpenD is used only for market data.

## Operator launch

From the feature checkout, create an isolated environment and install the two
paper dependencies (the gateway dependencies are unnecessary for this portal):

```bash
python3.11 -m venv .venv
.venv/bin/pip install moomoo-api==10.8.6808 exchange-calendars==4.11.2
.venv/bin/python scripts/paper_trader.py preflight --session-date 2026-11-20
.venv/bin/python scripts/paper_trader.py start --session-date 2026-11-20
.venv/bin/python scripts/paper_trader.py status
```

Open <http://127.0.0.1:8330/>. The journal updates every five seconds. To use a
different private runtime location, supply the same `--data-dir /absolute/path`
on every command. The current prepared session uses
`/Users/dhanvin/Documents/IntelliDhan/data/paper-journal-2026-11-20`.

The process must remain running, the Mac must remain awake, and OpenD must stay
signed in. This is a local deterministic observer, not a hosted job or an LLM
making discretionary trades. It starts now and waits for the authorized date;
it never automatically rolls into another day's trading. The desktop automation
connector was unavailable during setup, so no separate Codex scheduled task was
created. The date-fenced local runner does not depend on that connector.

`stop` stops only the matching process instance. It does not erase losses or
resolve a missing exit. Do not stop a session with an open paper position unless
you accept an interrupted/unresolved result. Restarting with the same data
directory restores that position and services its exit before considering entries.

## Frozen pilot rules

| Control | Value |
| --- | --- |
| Starting virtual capital | $1,000; initialized once |
| Premium ceiling | $500 per entry, including modeled entry slippage |
| Daily risk budget | $500; full premium plus both fees must fit remaining budget |
| Exposure | One standard 100-share SPY or QQQ long call/put contract; no margin |
| Maximum entries | Two across the portfolio; one position at a time |
| Expiry | Same session only; no 1DTE fallback or SPX substitution |
| Delta | Absolute delta 0.50–0.80; highest affordable verified candidate first |
| Liquidity | Volume and open interest at least 100; spread at most 10% of midpoint |
| Quote age | At most 10 seconds; no missing/future timestamp substitution |
| Fill assumption | Entry ask + $0.01; exit bid − $0.01, floored at zero |
| Modeled fee | $0.65 per contract per side; an assumption, not a broker fee quote |
| Cash | Sale proceeds unavailable until the next exchange session |

With these cost assumptions, a full $500 ask debit would exceed the daily
worst-case budget after slippage and fees and is rejected. A $500 ceiling is not
a target to spend. It exposes up to approximately half the virtual account on
one option. Profits do not replenish the daily risk budget; losing closed trades
consume it. Stops cannot guarantee a particular loss.

The strategy observes the 9:30–9:45 opening range, waits for the first completed
five-minute close outside it, then requires a retest within three completed
candles. The retest must touch the boundary, close outside, and align with the
9-period EMA and that session's VWAP. Entry requires a break of the retest
extreme during the immediately following candle. A completed close back through
the boundary cancels the breakout. An observed pre-entry stop violation is
durably invalidated; a rebound cannot resurrect that signal.

The initial stop is beyond the retest extreme by 0.1 times the completed-bar
14-period mean true range. The target is 2R in the **underlying**, not a promised
option return. Exit also occurs on an opposing completed five-minute EMA break,
data-health failure, a 60-minute holding limit, or 11:30 ET. New entries stop at
10:30 ET. The first-breakout-only rule permits at most one unique setup per
symbol per day. Missing bars block a signal. Five-second polling is not tick
replay and can miss an intrapoll crossing.

## Data readiness and bounded scanning

OpenD is signed in and returns SPY/QQQ snapshots, option chains and completed
five-minute bars. The installed SDK is 10.8.6808. The observed account has 100
general data slots but only 20 option slots. Scans therefore inspect metadata
for the nearest 30 standard strikes, then request at most eight eligible books
per scan and never more than 20 owned option subscriptions. This is a disclosed
bounded search, not the entire chain or a guaranteed globally optimal contract.
It never cancels another application's subscriptions.

The November 19 after-hours probe returned a SPY option book with empty
`svr_recv_time_bid` and `svr_recv_time_ask`. Those quotes are not admissible for
fills. The latest bounded preflight also found no contracts passing the frozen
price/delta/liquidity filters at that time. Neither observation proves what will
be available after the next open. The runner rechecks in-session; if timestamps
or other required data remain unavailable, it must record a blocked/no-trade
session. It does not substitute Yahoo, the request time, a snapshot's last-trade
time alone, or guessed option prices.

## Ledger and privacy boundary

The journal shows cash, settled buying capacity, realized and open P/L, current
position, entry/exit reasoning, rejected setups, agent heartbeat and quote state.
Unknown open-position valuations are `null`, not zero. A requested exit with no
fresh option quote stays `EXIT_PENDING`; the program cannot invent an expiration
price or retrospectively label a loss a win. A genuine fresh option quote can
close a data-health exit even if the underlying quote is unavailable, with that
gap explicitly recorded.

SQLite persists integer cents, immutable audit events and a unique signal ID.
Transactions prevent duplicate positions and fills. Capital and policy cannot
change on restart. Export includes the full audit history; the API returns the
latest 200 events and the screen displays the latest 20. Runtime artifacts are
private, outside tracked source.

This separate portal is single-operator and loopback-only. A direct same-origin
top-level visit creates a one-day HttpOnly, SameSite=Strict local browser session;
personal API routes require it. Cross-site requests, unexpected Host/Origin,
iframes and writes are rejected. It trusts the local OS user, not an IntelliDhan
cloud account, and must not be exposed through a tunnel, reverse proxy, LAN bind
or shared hosting without a separately reviewed account-authentication design.
No broker, OpenAI, Claude, or gateway tokens are loaded into this process.

## Evidence and operational limits

Unit and integration tests exercise synthetic observations, not trading returns.
They establish implementation behavior, not a win rate. This is the first
forward-paper collection for the exact frozen strategy and fill assumptions.
There is no demonstrated positive expectancy or daily-profit guarantee.

Verification on November 19: the complete non-integration suite passed (456
tests, six integration tests deselected), Ruff and JavaScript syntax checks
passed, and local HTTP/session/account/asset smoke checks passed. Independent
agents reviewed the actual implementation and corrections. Native browser
control was unavailable, so a visual browser check is not claimed.

The NYSE calendar confirms a normal November 20 session. The exchange-calendar
adapter uses explicit schedule/session labels to avoid the pandas 3 timestamp
unit incompatibility in exchange-calendars 4.11.2 convenience methods.

References: [NYSE calendar](https://www.nyse.com/trade/hours-calendars),
[OpenD order-book API](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-order-book.html),
[OpenD book callbacks](https://openapi.moomoo.com/moomoo-api-doc/en/quote/update-order-book.html),
[local scheduled-task requirements](https://learn.chatgpt.com/docs/automations?surface=app).

## Private ChatGPT website

The journal also has a private ChatGPT-hosted mirror at
[IntelliDhan Paper Journal](https://intellidhan-paper-journal.dhanvinnraval.chatgpt.site).
Sign in with the owning ChatGPT account. The hosted UI is read-only and updates
automatically; it cannot start trading, alter risk, or access a brokerage.

OpenD and the date-fenced observer still run on the Mac. Outbound HTTPS sync
runs in a separate daemon thread with a two-second network timeout, no
redirects, bounded payloads, and retry backoff. Cloud failure does not change
local decisions or stop local exits. Mac sleep or lost connectivity makes the
site stale; after 30 seconds, current equity and P/L are unavailable rather
than presented as fresh. Recorded entries, exits, and reasons remain durable.

Configure service access using `scripts/paper_trader.py configure-site
--data-dir <private-runtime-directory>` in an interactive terminal. Paste only
the existing Site service credential into its hidden prompt. Never put the
credential in command arguments, browser code, environment dumps, or Git. The
configuration is written mode 0600 inside the owned 0700 runtime directory.
Restart only the owned observer with the same explicit session date/data path;
the existing ledger is retained. The destination is pinned to the Site above.
For an observer that is already running without integrated sync, use
`scripts/paper_site_sync.py start --data-dir <private-runtime-directory>`.
This starts a read-only sidecar; it never stops or modifies the observer.
It exits when that observer's process identity changes, allowing the next
observer start to own the integrated sync worker without duplicate uploaders.

The Site relies on its confirmed owner-private platform access boundary.
Do not make it public without implementing and reviewing separate service
authorization. Source lives in a separate Sites-managed Git repository at
`data/sites/intellidhan-paper-journal`; D1 migrations are published with it.
The source is independent of the existing Koyeb app and does not change its
deployment. A ChatGPT website is not a cloud-hosted OpenD process.

Published Site source checkpoint: `2280ebe047922d7189251aace4fd0223eb5ce56c`.
Verification: 512 Python tests (6 deselected), 16 hosted persistence/freshness
tests, Ruff and TypeScript/build checks,
independent review, service-authenticated live readback of the actual $1,000
zero-trade account, and rejection of unauthenticated API access. The browser
automation bridge was unavailable, so visual browser QA remains unverified.
