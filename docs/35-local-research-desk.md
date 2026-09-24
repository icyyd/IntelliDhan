# Local research desk and trading-readiness plan

Date: 2026-09-24. Status: local Simulation/research; profitability unvalidated.

## What is running

The local profile serves the existing terminal at `http://127.0.0.1:8321`,
collects completed underlying bars, and persists accounts, signals, and journals
in a private SQLite directory outside Git. It does not use or replace Koyeb's
database. Existing cloud users, sessions, settings, and history are not imported.
No cloud service was stopped, merged, or deployed by this pass.

The launcher ignores repository `.env` and inherited database, broker,
delivery, AI, and research credentials. Optional credential-dependent feeds
therefore remain unavailable until a separately reviewed local configuration
path exists. Do not weaken isolation by sourcing the cloud `.env`. Normal
Yahoo underlying data is still an external service with coverage/rate limits;
it is not a tick-level executable options feed.

The server binds only to IPv4 loopback and rejects foreign hosts/origins.
Generated owner, agent, and control credentials remain private on disk. Local
HTTP uses non-Secure HttpOnly cookies because traffic stays on loopback; do not
expose this profile through port forwarding, a public tunnel, or a LAN bind.
The local OS account and filesystem remain part of the trust boundary.

`INTELLIDHAN_LOCAL_ONLY=1` forces Simulation, revokes recovered placement
authority, and rejects Live updates server-side. Existing executed broker
exposure, if present in an imported store, is not erased or marked closed;
reconciliation remains required. The launcher does not import such a store.

## Setup and operation

Python 3.11+ on macOS or Linux is required. Windows is not supported by this
launcher (`fcntl`/POSIX permissions). For a new checkout, create its own venv:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/local_runtime.py start
.venv/bin/python scripts/local_runtime.py create-admin
.venv/bin/python scripts/local_runtime.py status
.venv/bin/python scripts/local_runtime.py backup
.venv/bin/python scripts/local_runtime.py stop
```

The first-admin command prompts for email, name, and a non-echoed password,
then registers through the local account API. It refuses to overwrite an
existing account. Sign in through the browser afterward. Do not send passwords
or generated tokens to an agent. Account sessions retain the existing 30-day
server expiry; explicit logout/revocation still applies.

The current isolated development worktree uses the existing dependency venv
without changing its editable installation:

```bash
cd /Users/dhanvin/Documents/IntelliDhan-beta-optimize
/Users/dhanvin/Documents/IntelliDhan/.venv/bin/python scripts/local_runtime.py \
  --python /Users/dhanvin/Documents/IntelliDhan/.venv/bin/python start
```

Replace `start` with `create-admin`, `status`, `backup`, or `stop`. Global
options go before the command. The launcher sets source paths to this checkout
so the shared venv does not run old source from the other worktree. Keep the
checkout and venv present while using this instance. Do not delete/move them
while the server is running.

Default data paths:

- macOS: `~/Library/Application Support/IntelliDhan/local`
- Linux: `~/.local/share/IntelliDhan/local`

Use an absolute `--data-dir` outside every Git worktree if overriding. The
directory must be owned by the current user with mode 0700, and existing
runtime/secret/state files must be private (0600). Unsafe paths fail closed;
the launcher does not silently relax permissions or regenerate existing secrets.
The private log is `runtime.log`. Start/stop checks PID plus instance identity;
it never kills an unknown process or force-kills a slow shutdown.

`backup` takes a consistent online SQLite snapshot, verifies integrity, and
creates a uniquely named private file under `backups/`. Existing copies are
not deleted. A same-device backup does not protect against device loss; use
encrypted off-device backups for disaster recovery. The SQLite copy does not
include launcher secrets or policy/state sidecars: a full recovery procedure
must preserve those privately too. Do not upload any of these to Git.

The process runs after its launching terminal closes, but sleep, shutdown,
reboot, network loss, or a crash interrupts it. There is no installed login
service, crash supervisor, keep-awake change, or automatic backup schedule.
Check health before each session. `status`/`/api/liveness` only prove process
availability; `/api/health` and per-symbol health remain mandatory.

## Research and risk corrections

Research confidence is capped below the 75% Live threshold. Previously that
also prevented the 9EMA research strategy from entering the Simulation queue.
Only research-only `EMA9_MTF_0DTE` observations in the 0DTE module now bypass that
Simulation confidence test; their score is not changed or represented as a win
probability. Allowlist, expiry, source-data health, option liquidity, sizing,
calibration, and Live gates remain enforced in their applicable paths.

Simulated entry debit is fresh ask times the unchanged selected quantity. It
must fit both the selected ceiling and the current per-order, buying-power
fraction, and remaining daily ceilings; changed prices/caps can require new
selection. Buying-power evidence must still be fresh. Filled Simulation
positions count toward its open-position cap; unfilled candidates do not.
Actual entry debit consumes that entry day's Simulation budget even after an
exit, separately from Live exposure. This is a premium-usage budget, not a
guarantee that stop orders limit realized loss.

Underlying paper entries stop at session close minus five minutes; normal
sessions flatten at 15:55 ET and half-days at 12:55 ET. Already-open trades with
unknown-calendar or missing-cutoff observations become `UNRESOLVED_DATA` without
an invented exit or return; pending entries expire unfilled. Reports display
the unresolved count and exclude those trades from scored outcomes.
Missing results can conceal gains or losses: material unresolved observations
must be resolved or conservatively bounded before any promotion decision.

This underlying paper model still uses partial profits and breakeven logic,
not the v2 whole-position option trend-break lifecycle. Its displayed results
exclude option spreads/fees and are not evidence of option profitability.
The optional Yahoo option Composer is not wired to the production loop. Its
premium-denominated entry zone/source risk must be reconciled with dynamic
underlying-zone intent validation before anyone connects that path; do not
relax caps to make it pass. Production composition remains underlying-only.

## What still blocks real trading

1. The actual `EMA9_MTF_0DTE` calibration remains `HISTORICAL_RESEARCH` with no
   qualified winner and `live_eligible: false`. Its July train/validation split
   had only 14/11 trades and unstable expectancy. The September tests of ORR
   and raw 30-minute EMA were negative and are not tests of this MTF lifecycle.
2. Starting the server does not start a broker quote agent. Official Robinhood
   MCP runtime tools/account access were unavailable in this implementation
   session. Configure/authenticate the project alias with
   `codex mcp login robinhood-trading`, then reconnect/restart Codex as needed
   and inspect advertised schemas. An enabled config entry alone is not proof
   of working quotes or the correct account. Never use unofficial clients.
3. Real-option Simulation requires fresh official bid/ask, option identity,
   delta, liquidity, buying power and sellout deadline, followed by ENTRY/EXIT
   receipts with reasoning. No such fills were collected by this pass. Simulated
   fills are hypothetical even when quotes are real; they are not broker fills.
4. There is no verified five-year intraday/options dataset in the repo. Free
   underlying history cannot establish 0DTE/LEAPS executable returns.
5. Swing replay still needs next-observable fills, gap-aware stops, spreads/
   fees, unresolved-position reporting, purged split boundaries, and a
   one-time finalist holdout evaluation. Its current output cannot authorize
   Live. LEAPS needs point-in-time option prices/IV or future quote-based data.

## Next experiment: the actual strategy, not another optimized proxy

Freeze the MTF entry, full-position trend-break exit, option selection, risk
policy, costs, and code/config hashes before collecting new sessions. Use
future completed sessions after that freeze; September observations already
inspected are development/diagnostic data, not an untouched holdout.

Record every candidate, rejection reason, data gap, selected contract/quantity,
entry ask, exit bid, fees, timestamps, and entry/exit reasoning. Evaluate net
expectancy, drawdown, cost stress, uncertainty, and per-symbol/regime stability
alongside sample counts and unresolved observations. Do not optimize win rate
alone or selectively drop missed exits. Broker review/fees and execution
latency still need conservative modeling beyond the current quote journal.

Doc 30 requires at least 30 validation and 15 untouched test observations,
positive stable net expectancy, walk-forward and forward-paper evidence,
reconciled lifecycle, and independently reviewed promotion. Those sample
minimums do not guarantee sufficient statistical confidence. Only a separately
reviewed runtime can later permit Live, with explicit time-limited activation
and per-order confirmation under doc 27. Neither unlimited hosting nor repeated
tuning can make a strategy loss-proof.
