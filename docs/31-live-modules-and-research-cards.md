# 31 — Live Modules & Research Cards

## Intent

Reduce routine interaction cost while making the first stock-pick decision
useful. Signal cards are the primary object; charts and specialist tables are
progressive detail. This document is the implementation contract for the
module refresh and focus-card presentation.

## Refresh contract

| Surface | Refresh path | Target cadence | UI state |
|---|---|---:|---|
| Engine state, signal queues, trade state | WebSocket plus existing poll | 15 seconds / push | Header data-health indicator |
| Briefing and macro feed | `/api/briefing`, `/api/news` | 60 seconds | Feed timestamp and unavailable state |
| Daily brief | `/api/daily-brief` | 2 minutes | Generated timestamp in the brief |
| Focus anchors, ranked picks, research feeds | `/api/focus`, `/api/intelligence/:symbol` | 2 minutes | `Scan checked · Xm ago` / `Checking…` |
| AI curation | Explicit user action | On demand | Button progress and saved result |

Routine data does not expose a manual refresh control. A refresh is triggered
by the application timers, websocket events, sign-in bootstrap, or an explicit
AI curation action. The automatic path respects provider/cache TTLs and uses an
in-flight guard; an explicit force refresh is reserved for controlled calls.
Every feed failure remains visible and does not silently reuse a stale success
state. “Scan checked” describes the endpoint check time; the feed pills and
dossier retain each provider's own availability and source timestamps.

## Module hierarchy

1. Compact header: module purpose, active signals, held-back count, live badge,
   and latest engine timestamp.
2. Signal queue: active and held-back tabs; signal cards contain the complete
   trade plan and open the selected decision context.
3. Closed “Market context” (0DTE) or “Price context” (Swing) disclosure:
   profile rows, daily chart, and indicator chips remain available but do not
   compete with signal triage.

The same hierarchy should be used when LEAPS/HODL views are promoted from
research-only status. Do not add a second summary panel that repeats the module
header or the signal queue counts.

## Stock-pick insight contract

The Top 3 focus list is deterministic first. Each card shows the setup posture,
technical/financial/overall score, confirmation, and source coverage. The
collapsed Investment case provides a small, honest thesis:

- business description (from company profile or the setup label);
- two strongest setup facts and their interpretations;
- analyst-target context only if returned by the provider;
- invalidation, volatility/news risk, and latest filing context when available.

This is deliberately a compact summary of the full Analyze dossier. It must
remain source-aware: absent fundamentals, SEC, news, or social feeds are shown
as unavailable/partial and cannot be filled with generated claims. AI review is
advisory and cannot rerank a deterministic pick or create an execution intent.

## Acceptance checks

- A signed-in user can open 0DTE or Swings and see the active/held-back queue
  without opening a chart.
- The visible status changes from updating to a relative freshness timestamp
  after each successful external refresh.
- A focus card can be understood from its setup and confirmation in five
  seconds; the business case and risks are one disclosure away.
- Unconfigured research feeds remain labelled and score coverage is preserved.
- No routine refresh button, duplicate module summary, or chart-first panel is
  introduced.

## Responsive collision guard

At compact widths the header must remain a single row: health/session chips are
removed before account/settings controls can shrink into one another, icon
buttons retain a usable 40px hit target, and the command button remains a
single flex item. The mobile task bar uses equal flexible segments with clipped
labels rather than fixed-width items that extend past the viewport. The page's
document `scrollWidth` must equal the viewport width at 320, 375, 390, 480,
520, 600, 768, 1024, and 1440px test viewports.
