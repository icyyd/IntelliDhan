# Platform safety and data-integrity contract

Date: 2026-07-18
Status: active, mandatory agent contract

This document preserves the platform-wide controls that are independent of any
broker execution-agent implementation. `AGENTS.md` requires agents to read it
before changing research, data, authentication, execution, or deployment paths.

## Application token and browser boundary

The Codex bearer and operator control tokens are independent secrets:

- `AUTOTRADE_CODEX_AGENT_TOKEN` authenticates only intent list, option-selection,
  Simulation receipt, capital-review, Live claim, and broker-receipt endpoints.
  It must never be shared with a retired runner.
- `AUTOTRADE_CONTROL_TOKEN` authenticates operator policy, switch-to-Simulation,
  intent creation, and rejection endpoints when an ADMIN session is not used.
- `ANTHROPIC_API_KEY` is an optional server-only research credential. It may
  call Claude only with the bounded multi-brain evidence packet in doc 15 and
  never authenticates an agent, broker action, intent, receipt, or MCP server.
- The browser uses a separate HttpOnly account session and never receives either
  automation token.

Never expose setup, invite, control, Codex-agent, broker, or account tokens in
browser state, URLs, prompts, tool output, logs, analytics, or version control.
Do not treat a request-body agent label as authentication; the new Codex-only
bearer is the credential boundary and the literal claim body is defense in depth.

## Research cannot authorize execution

- `GET /api/analyze/{symbol}` and `GET /api/dossier/{symbol}` are descriptive
  research. Never convert their consensus, posture, forecast, business summary,
  news, or social attention into an alert, auto-trade intent, live order, or
  calibration promotion.
- Use a forecast horizon only as context when it is not `UNCONFIRMED`, confidence
  is `MODERATE` or `HIGH`, matched samples are at least 12, and walk-forward
  Brier skill is positive. Never substitute a forecast probability for a
  strategy-specific calibrated win rate.
- Strategy confidence stays below the live gate until calibration metadata
  explicitly declares `HISTORICAL_OOS`, `FORWARD_PAPER`, or `LIVE_VALIDATED`
  evidence and the strategy is marked `live_eligible: true`.
- `smart-play-v1` is a settled-daily, configured-universe research rank. It is
  not a probability, broad-market rank, fundamental score, recommendation, or
  execution signal.
- The closed-schema OpenAI thesis endpoint may select supplied evidence and
  return bounded research labels. It cannot change deterministic rank, create an
  intent, or invent evidence. Require a complete universe scan and exact
  evidence-ID matches; fail closed on any mismatch.
- Claude is an optional independent review layer over the deterministic
  multi-brain packet. It has no tools, browsing, MCP connection, or visibility
  into automation state; it cannot modify the reconciled posture or any rank.
  Provider failure or a citation outside the supplied evidence IDs leaves the
  deterministic dossier unchanged and visibly marks the review unavailable.
- A ChatGPT Workspace Agent research dispatch must use a dedicated no-broker,
  no-general-write agent. Its output cannot rank, signal, claim, approve, or
  execute.

## Accounts, persistence, and health

- Personal state endpoints and `/ws` require a server-expiring account session.
  Preferences, capital limits, watchlists, and saved screens are scoped by
  `user_id`.
- Per-user capital limits are review ceilings only. Do not claim they resize
  shared signals or constrain shared execution intents until a user-specific
  order-planning layer exists.
- The shared execution policy separately requires a fresh official-MCP buying
  power observation. The 9EMA selector sizes to the maximum whole-contract
  position inside the configured fraction and every stricter per-order/daily
  risk threshold; no individual threshold can be exceeded.
- Broker execution events are global operational records and are returned only
  to ADMIN or legacy-owner sessions. TRADER and VIEWER trade-log responses omit
  them.
- Production intent events are immutable individual database rows keyed by a
  unique event ID. The current intent snapshot may be overwritten, but replica
  overlap cannot erase already-appended audit events.
- PostgreSQL via `DATABASE_URL` is the production operational store. SQLite is
  for local use unless it is on a verified mounted persistent path.
- `/api/liveness` proves only that the process responds; it never authorizes a
  trade. `/api/health` and the per-symbol actionable state remain mandatory in
  the execution loop.
- A 503 is a global execution block except for the narrowly specified
  `PARTIAL`/per-symbol continuation in doc 27.

## Point-in-time and data-quality rules

- Historical tests use completed, adjusted bars and chronological/no-lookahead
  labels. Current fundamentals, estimates, filings, news, or social snapshots
  must not be injected into historical rows without point-in-time archives.
- Missing provider data stays missing and visibly reduces coverage. Never fill a
  missing input with a favorable default or allow attention/sentiment to create
  a directional posture by itself.
- A quarantined symbol blocks new Live intents and claims. An already
  claimed intent retains its broker-receipt path but placement authority is
  revoked.
- Do not add or promote a strategy because it is popular or profitable on one
  window. Require predeclared parameters, realistic costs, walk-forward tests,
  held-later validation, adequate samples, and explicit evidence promotion.

## GitOps and independent review

Before editing, committing, reviewing, merging, or deploying:

1. Fetch and prune; inspect `git status -sb`, local/remote history, the complete
   diff, and open PRs. Collaborator changes are input, never something to reset,
   overwrite, or silently reformat.
2. Work on a narrow `codex/*` branch from current `main`. If another branch or PR
   already owns the cohesive change, update it rather than competing.
3. Every system-changing pass updates `README.md` in the same commit. Reconcile
   its date and last-system-pass marker, shipped capabilities, setup/deployment
   instructions, safety boundaries, and document index as applicable. A
   context-file or PR-body update is not a substitute.
4. Stage explicit in-scope paths. Run proportional unit, integration, lint,
   schema, UI, and smoke checks; commit tersely and push with upstream tracking.
5. Open a draft PR and request independent review of the actual patch, including
   security, data integrity, UX, tests, documentation truth, and CI.
6. Address blocking findings, rerun checks, and obtain a clean re-review. Never
   approve an unreviewed or failing PR and never represent a self-review as an
   independent approval.
7. Merge, branch deletion, secret changes, deployment, and execution-mode changes
   each require explicit user authorization. Deploy only merged `main`, preserve
   a rollback target, verify Koyeb health, and perform a non-mutating smoke test.

## Product expansion

Complete the durable Discover → Analyze → Decide → Track loop before adding more
indicators or dashboards. Fundamental quality, valuation, estimates, revisions,
or earnings drift require point-in-time data contracts before backtesting or
ranking. Product copy must distinguish implemented behavior from planned work.
