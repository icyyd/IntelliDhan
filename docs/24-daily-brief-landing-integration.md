# 24 — External Daily Brief Landing Integration

This document is the agent-readable operating contract for surfacing the latest
premarket artifact from `icyyd/intellidhan-daily-brief` on IntelliDhan's Signals
landing page.

## Outcome

The Signals landing page begins with a card-first **Today's analysis and setup**
module. It shows only the decision context a signal-oriented trader needs:

- the report's top market read and generation time;
- a compact S&P 500, Nasdaq, VIX, and US 10Y snapshot;
- at most four day/swing watchlist cards with rule eligibility, analyst
  conviction, gap, relative volume, context, and plan;
- a clear stand-aside notice when no intraday name passed the rules;
- the leading skip/risk and the most important source limitation;
- a one-click handoff from any symbol to IntelliDhan's on-demand analysis.

The source brief remains research context. It cannot create an alert, trading
intent, approval, or Robinhood order.

## Source review

The external pipeline is:

```text
scan.py -> packet.json -> Claude/Codex/Grok views -> REPORT.md
        -> render_report.py -> dated HTML -> Telegram
```

`packet.json` is the deterministic evidence layer. Its `day_eligible` and
`swing_eligible` flags are computed in code. `REPORT.md` is the reconciled
presentation layer and contains analyst conviction and plan language.

The integration intentionally reads `REPORT.md` and `packet.json`. It does not
embed the generated HTML, execute upstream markup, or allow a model opinion to
override deterministic eligibility.

The companion daily-brief change adds `publish_artifacts.sh` and calls it after
render and the Telegram delivery attempt. It publishes `REPORT.md` and
`packet.json` in one
commit. `stamp_report.py` binds the report to the exact packet SHA and target
date. The publisher snapshots and validates those bytes, validates the effective
fetch and sole push URL, and creates a detached temporary worktree from the
fetched `origin/main` SHA. It never stages or changes the shared checkout's HEAD
or index, and it never force-pushes. This companion change must be merged and
enabled for unattended daily updates; until then, IntelliDhan shows the newest
manually committed artifact.

## Data flow and trust boundary

```text
Private GitHub repository
  -> gateway-only GitHub Contents API fetch
  -> bounded Markdown/JSON parser
  -> normalized daily-brief JSON
  -> durable daily_briefs table (last-known-good)
  -> authenticated GET /api/daily-brief
  -> escaped card rendering in web/index.html
```

- GitHub credentials are server-only. The browser never receives a token.
- Repository and ref configuration are validated; the request host is fixed to
  `api.github.com` to prevent arbitrary server-side fetches.
- The configured branch/ref is resolved once to an immutable Git commit SHA;
  both `REPORT.md` and `packet.json` are fetched from that exact snapshot so a
  concurrent daily push cannot mix prose and eligibility from different runs.
- `REPORT.md` is capped at 256 KB and `packet.json` at 1 MB.
- Raw HTML is removed, and every dynamic browser value is escaped.
- The gateway caches successful reads for five minutes to limit GitHub load.
- Freshness fields are recomputed on every cached response, so the label flips
  at the session close even if artifact content remains inside the cache TTL.
- A successful normalized payload is stored in PostgreSQL/SQLite. If GitHub is
  unavailable, the API returns that payload as `STALE` with a visible notice.
- If there is no last-known-good brief, the landing page shows an explicit
  unavailable state and never labels old/sample content as today's setup.
- A setup card is emitted only when the matching packet field is the strict JSON
  boolean `true`. Missing values, strings such as `"false"`, AI-added tickers,
  and invalid numeric values fail closed.

## Configuration

Set these on the gateway service:

```dotenv
INTELLIDHAN_DAILY_BRIEF_GITHUB_TOKEN=<fine-grained-read-only-token>
INTELLIDHAN_DAILY_BRIEF_REPOSITORY=icyyd/intellidhan-daily-brief
INTELLIDHAN_DAILY_BRIEF_REF=main
```

The token should be a fine-grained GitHub token with read-only Contents access
to only `icyyd/intellidhan-daily-brief`. Store it as a Koyeb secret. Never put it
in `koyeb.yaml`, `.env.example`, browser JavaScript, logs, or a pull request.

## API contract

`GET /api/daily-brief` requires an authenticated IntelliDhan user and returns:

```json
{
  "status": "CURRENT | STALE | UNAVAILABLE",
  "freshness_reason": "current_session | previous_session | ...",
  "freshness_label": "Current brief | Previous session | After-hours report | ...",
  "source_health": "live | stored_fallback | unavailable",
  "report_date": "YYYY-MM-DD",
  "generated_at": "ISO-8601 America/New_York timestamp",
  "headline": "plain text market read",
  "summary": ["plain text"],
  "setups": [
    {
      "symbol": "CRWD",
      "module": "DAY | SWING",
      "plan": "plain text confirmation plan",
      "context": "plain text levels/trend",
      "conviction": "HIGH | MEDIUM | LOW | NONE",
      "rule_eligible": true,
      "gap_pct": 12.14,
      "rvol": 1.11,
      "rvol_source": "source label"
    }
  ],
  "market": [],
  "risks": [],
  "data_quality": {"limitations": []},
  "source": {"commit_sha": "immutable 40-character SHA", "url": "commit-pinned GitHub REPORT.md link"},
  "disclaimer": "Research brief only..."
}
```

## Freshness rules

- `CURRENT`: `generated_at` is on today's validated US-equities trading day,
  was produced during premarket/regular hours, and the target session has not
  completed.
- `STALE`: the report targets a prior/non-trading session, was generated outside
  premarket/regular hours, the target session is complete, the market calendar
  cannot validate it, or the gateway is serving a stored last-known-good copy.
- `UNAVAILABLE`: no valid upstream or stored payload exists.

A source timestamp more than five minutes in the future is rejected rather than
shown. Holiday, weekend, after-close, session-complete, and clock-skew boundaries
are covered by tests using the platform's single `MarketClock` authority.

The UI displays a reason-specific label such as **Current brief**, **Previous
session**, **After-hours report**, **Session complete**, or **Brief unavailable**.
It also displays the actual generation timestamp.

## UI rationale

The layout follows a modular bento/card hierarchy inspired by current research
and trading terminals, while retaining IntelliDhan's rounded, high-contrast
visual language. The primary hierarchy is market read -> setup cards -> main
risk/data warning. Charts remain secondary. The component collapses to one
column below 900 px and setup cards collapse below 700 px, with no page-level
horizontal overflow at 375 px.

## Acceptance checks

- Parser tests prove deterministic rule flags and analyst conviction remain
  separate.
- Snapshot tests prove both GitHub artifacts use one immutable commit.
- Boundary tests cover strict booleans, missing/invalid numerics, AI-added
  tickers, weekends, after-hours generation, session completion, and future
  clock skew.
- Auth tests prove the endpoint is not public.
- Storage tests prove last-known-good recovery.
- Injection-oriented fixture text is stripped/escaped.
- The landing page always states that the report is research, not a live signal.
- Manual browser checks at 1440 px and 375 px show correct card hierarchy,
  single-column collapse, no page-level horizontal overflow, and no browser
  console errors. These visual checks are required before release; they are not
  represented as an automated CI screenshot suite.
