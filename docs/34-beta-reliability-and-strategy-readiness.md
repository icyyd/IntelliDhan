# Beta reliability and strategy readiness

Date: 2026-09-24. Scope: keep the current Koyeb/Python/PostgreSQL stack; reduce
avoidable cost and failures, preserve records, and validate trading hypotheses.
No hosting migration, paid upgrade, database replacement, or Live activation.

Follow-up: the user subsequently chose a separate local research desk. See
[doc 35](35-local-research-desk.md). The Koyeb deployment remains untouched;
its quotas and outage are not resolved by the local instance.

## Hosting constraint

Koyeb documents a free PostgreSQL allowance of **five active hours/month and
1 GB**, with sleep after five minutes idle. A single 6.5-hour regular trading
session exceeds that monthly active-time budget. Continuous account polling,
WebSocket session checks, and trade persistence keep the database active.
Reducing query count inside an active hour does not turn it into free time.
Sources: [database limits](https://www.koyeb.com/docs/databases),
[free instance limits](https://www.koyeb.com/docs/reference/instances).

The deployed database returned an active-time-quota error on September 24.
Software cannot reset that allowance. The website can serve its HTML while
accounts, saved state, signals, and execution remain unavailable. Do not label
the application operational based only on HTTP 200 from `/` or `/api/liveness`.

Within the requested unchanged/free stack, the supported use is occasional
research beta within the available quota. Always-on autonomous trading is a
blocked requirement. Never make the service appear healthy by moving records
to ephemeral SQLite, bypassing durability checks, accepting a cached login as
server authorization, or inventing current market data. Do not add artificial
keep-alive traffic to defeat provider sleep policies.

## Implemented recovery and cost changes

- Database connections use five-second connection, statement, and lock limits.
  Libpq can attempt multiple resolved hosts, so connection timeout is not a
  five-second total request deadline. Startup restoration and account handlers
  run in worker threads; liveness can respond during those operations.
- A process-wide store circuit backs off 60 seconds for connection/operational
  failures and 30 minutes for active-time quota errors, allowing one recovery
  probe at a time. Connection strings and host details are not exposed through
  the sanitized availability response. There is no persistent keep-alive pool.
- Database-dependent API calls return 503 `DATABASE_UNAVAILABLE`, `Retry-After`,
  and `Cache-Control: no-store`. Failed reads never become empty accounts or
  empty trade histories. Existing cookies are not cleared on outages.
- Explicit sign-out still clears this browser's cookies and private view during
  an outage. If the database cannot revoke the session, the UI explains that
  server-side revocation did not complete; it does not promise deferred cleanup.
- WebSockets use retryable code 1013 for store failures; 4401 remains actual
  session expiry/revocation. Browser reconnection verifies identity and respects
  the storage cooldown. Authentication is never cached as a permission grant.
- Health requires successful persistence, not just prior schema initialization.
  Storage failure suppresses actionable symbols and new execution. Recovery
  restores durable state and replays market bars without sending historical
  entries as new alerts. A monotonic failure-generation latch requires engine
  restoration even if a browser's database probe succeeds first.
- Hidden tabs close WebSockets and stop periodic requests. Visible Today keeps
  its two-minute automatic refresh; the 15-second automation poll runs only
  while its dialog is open. Bursts of WebSocket events coalesce state reads.
  Background research requests do not refresh long-form AI dossiers.
- Home-page level lookup uses the deterministic analysis route instead of
  triggering an optional Claude review. Explicit AI research still has provider
  charges when configured; there is no claim of universally free AI usage.

The breaker is per process, not a provider-quota accounting system. Restarts
lose its cooldown, and repeated deployments can still retry the database.
No lifecycle deletes of user data, trade events, or historical evidence were
introduced. Synchronous persistence in some market/intent paths, a single
process, and free-provider uptime remain limits to address before live use.

## Business logic corrections

The optional research option selector previously could apply a short swing
expiry to LEAPS. It now applies explicit mandate windows: 0–1 calendar DTE for
scalps, 21–90 for swings (30–45 preferred), and 365–1095 for LEAPS (365–730
preferred). HODL is equity-only. LEAPS research prefers absolute delta 0.70–0.90;
these are product rules, not evidence of profitability.

Expiry eligibility uses the exchange calendar, including shortened sessions,
and rejects unknown dates. The current calendar lists 2026–2027; contracts
expiring in 2028 or later are withheld until verified calendar coverage is
extended. This limits the presently available LEAPS research candidates.

Crossed, nonfinite, one-sided, and overly wide quotes are rejected. Missing a
mandate falls back to a clearly labeled underlying research plan; the official
MCP Live contract still prohibits silent equity substitution. Research quote
cache entries expire after 30 seconds. Yahoo supplies no authoritative live
quote timestamp/Greeks here, so such option alerts carry provenance and stay
SHADOW. The production loop still constructs the composer without a Yahoo
selector, and no live broker path was switched to Yahoo.

Long-option risk now reserves the full premium, even when an estimated stop is
shown. A stop estimate does not guarantee its fill or limit loss to that number.
This aligns optional composition with existing official-MCP maximum-debit sizing.
See [OIC LEAPS overview](https://www.optionseducation.org/optionsoverview/how-leaps-work)
and [OIC LEAPS strategies](https://www.optionseducation.org/optionsoverview/leaps-strategies).

## Evidence and next decision

The [fresh validation report](research/2026-09-24-beta-validation.md) checks
predeclared ORR and EMA crossover settings on a later completed-session window,
with costs, complete-session filtering, and correlated-session uncertainty.
All three tested intraday profiles lose after costs. Do not promote or describe
them as calibrated winners. This check covers underlying bars, not executable
option performance and not the distinct production EMA9_MTF strategy.

Five-year daily source history shows that simple trend filters can reduce
drawdown while lagging buy-and-hold returns. After indicator warmup, the scored
period is approximately four years. That finding supports a research/risk
filter, not a profitable swing/LEAPS auto-trader. Current forecasts remain
unconfirmed, and option-level historical prices/fills are missing.

Required next work, in dependency order:

1. Verify durable storage and uptime before continuous automation. The local
   profile provides separate persistent SQLite without Koyeb quotas, but sleep,
   power loss, connectivity, backups, and broker access remain dependencies.
2. Collect official-MCP bid/ask Simulation observations and realistic exits for
   the actual production path, with reasons and rejected-entry records.
3. Correct swing replay's signal-close fills, missing costs, gap stops, and
   production-path mismatch before comparing entry filters.
   The local follow-up corrected the underlying paper executor to session close
   minus five minutes and added half-day/missing-observation tests. This does
   not validate actual broker exits or the separate option lifecycle.
4. Predeclare one new setup hypothesis and a future holdout; do not repeatedly
   optimize the September evaluation window now that its results are known.
5. Require positive net expectancy and acceptable drawdown under cost stress,
   adequate independent sessions/regimes, and successful forward paper results.
   Preserve all current calibration gates until separately reviewed promotion.
6. Build point-in-time long-dated option evidence and portfolio/account-specific
   planning before adding live swing/LEAPS execution.

No implementation or historical result makes a trading strategy loss-proof.
