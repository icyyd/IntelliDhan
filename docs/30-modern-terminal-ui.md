# Modern Terminal UI and Refresh Contract

**Status:** Implemented foundation

**Last updated:** 2026-07-21

**Branch:** `codex/robinhood-ui-refresh`

## Objective

Make IntelliDhan faster to scan without turning it into a chart wall. The Today
screen remains a signal engine: current market state, three names in focus,
complete signal cards, and optional detail. Secondary research and operational
controls stay available through progressive disclosure.

## Reference patterns

The pass borrows interaction principles rather than Robinhood branding or exact
visual assets:

- [Robinhood Legend](https://robinhood.com/us/en/legend/) keeps search, linked
  context, watchlists, and execution close together while allowing secondary
  tools to live in configurable widgets.
- [Robinhood watchlists and cards](https://robinhood.com/us/en/support/articles/watchlist-and-cards/)
  use cards to surface relevant market and account information above the wider
  watchlist.
- [Robinhood Legend widgets](https://robinhood.com/us/en/support/articles/widgets-in-robinhood-legend/)
  treat the selected symbol as shared context and allow watchlist rows to open
  deeper analysis.
- [Robinhood price alerts](https://robinhood.com/us/en/support/articles/stock-price-alerts/)
  keep freshness and notification state visible, but explicitly warn that
  alerts are informational and may be disrupted.

IntelliDhan deliberately differs where its purpose differs: charts remain
optional, the system does not mimic order-entry affordances, and evidence or AI
research cannot silently become an execution signal.

## Implemented hierarchy

1. The top command bar contains the brand, global search, refresh freshness,
   market health/session, theme, and account only.
2. The ticker tape is removed from the visual header because SPX/SPY/QQQ already
   provide market context on Today.
3. Mobile navigation exposes Today, Discover, and Analyze directly; 0DTE and
   Swing remain available through one compact Desks selector.
4. Each Top 3 card has one visible action: open the full analysis. Watchlist and
   per-symbol AI actions live in the analysis workflow or batch curation.
5. Historical reliability and paper results are collapsed until requested.
6. Capital and risk limits are accessible from the account dialog on mobile and
   the settings control on desktop, avoiding duplicate top-bar buttons.

## Two-minute refresh contract

`AUTO_REFRESH_MS` is fixed at `120000` in the web shell.

Every cycle refreshes:

- authenticated terminal state;
- calibration/evidence metadata;
- engine and daily briefs;
- Top 3 focus plus research enrichment;
- automation status.

The orchestration is single-flight: another timer, sign-in completion, or
visibility event reuses the in-progress batch instead of creating duplicate
requests. A login that overlaps the anonymous calibration refresh queues one
authenticated batch immediately afterward. Returning to a visible tab triggers
a catch-up only when the last successful batch is at least two minutes old.

WebSocket messages still refresh lightweight terminal state between cycles.
Automation status retains its 15-second safety poll because it represents
short-lived approval and arming state, not the slower analysis cadence.
Policy changes and disarming always force a post-mutation read after any older
poll completes, so an older response cannot repaint the prior mode.

The command bar reports refreshing, delayed, last-updated, and next-refresh
state. A failed or partial batch keeps the last successful timestamp and reports
`Refresh delayed`; it never clears the last good market data or implies that a
stale analysis is current.

## Responsive and overlap rules

- Sticky layers have explicit offsets and must not cover the first content row.
- The command bar progressively hides session detail and the refresh label
  before it permits horizontal overflow.
- At 900px the left rail disappears and the mobile task navigation becomes the
  sole primary navigation.
- Long status text truncates inside its own surface; it cannot enlarge the page
  or overlap adjacent controls.
- Card actions never wrap over scores, symbols, or evidence text.
- Verify dark and light themes at 375, 768, 1024, and 1440px.

## Non-goals and safety

This pass changes presentation and client refresh scheduling only. It does not
change strategy selection, confidence, source scoring, account authorization,
capital limits, Robinhood execution, or the fail-closed data-quality contract.
Automatic refresh is not automatic trading.
