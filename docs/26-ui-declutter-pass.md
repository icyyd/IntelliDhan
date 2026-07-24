# Signal terminal decluttering pass

Date: 2026-07-18
Branch: `codex/ui-declutter`

## Goal

Reduce cognitive load on Today without weakening signal evidence, safety state,
or access to analysis. The screen should answer four questions in order:

1. What is the market/data state?
2. What deserves attention now?
3. Is there a complete actionable signal?
4. Where can the user inspect supporting evidence?

## Reference patterns

- [Robinhood Legend](https://robinhood.com/us/en/legend/) uses purpose-specific,
  linked widgets and preset layouts instead of showing every tool in one fixed
  surface. IntelliDhan applies the same principle through one primary focus
  workspace and progressive disclosure for secondary research.
- [Robinhood Legend widgets](https://robinhood.com/us/en/support/articles/widgets-in-robinhood-legend/)
  keep watchlists as compact, configurable monitoring surfaces. IntelliDhan's
  wider curated radar is therefore collapsed beneath the Top 3 rather than
  competing with it.
- [TradingView Supercharts](https://www.tradingview.com/support/solutions/43000746464-getting-started-with-supercharts/)
  moves watchlists, news, alerts, screeners, and calendars into a secondary
  toolbar/overlay. IntelliDhan similarly keeps historical reliability and paper
  performance below the primary signal workflow.

These products are design references, not evidence of strategy performance or
permission to copy their execution behavior.

## Removed overlap

| Previous surface | Change | Reason |
| --- | --- | --- |
| Welcome hero plus separate ticker-search card | Combined into one command hero | One dominant starting action |
| Three boxed hero counters | Reduced to a quiet inline status line | Counts already exist in signal/automation areas |
| Three large market anchor cards | Compressed into one SPX/SPY/QQQ strip | Preserve context without consuming a full row |
| Top 3 plus always-open curated side panel | Radar moved into a collapsed disclosure under Top 3 | Top 3 remains authoritative; wider research is still available |
| Static strategy-lane panel | Removed from Today | 0DTE and Swing have dedicated navigation and signal cards already show methods |
| Empty selected-setup panel | Hidden until an active signal exists | No placeholder decision panel in the main reading path |
| Full market matrix below the signal detail | Removed from Today | Duplicated market strip, Top 3, and ticker analysis |
| Held-back signal tab plus held-back bottom drawer | Bottom duplicate removed | One audit surface is sufficient |
| Empty capital/risk panel | Hidden until signed-in limits load | Prevents an unlabeled blank card |
| Large automation explainer | Reduced to status, counts, and settings action | Keeps execution state visible without dominating research |

## Non-negotiable behavior

- No signal, score, rank, data feed, authentication, or execution logic changes.
- Top 3 remains deterministic and fail-closed.
- Signal cards retain entry, stop, targets, invalidation, expiry, risk, and
  historical-evidence status.
- Data-quality and execution-state warnings remain visible.
- Research posture remains distinct from validated forward edge.
- `SIMULATION` remains the default automation mode.

## Validation checklist

- unique DOM IDs and valid inline JavaScript;
- no horizontal overflow at 390, 768, 1024, and 1440 px;
- no panel overlap with long text, search suggestions, or expanded radar;
- keyboard-visible ticker combobox and radar disclosure;
- selected signal reveals the detail panel; no signal keeps it hidden;
- light and dark theme contrast;
- signed-out, loading, empty, and authenticated states;
- full Python suite, Ruff, and `git diff --check`.

## Browser verification

- The default Today path now exposes four primary sections instead of ten:
  command hero, market anchors, Top 3 focus, and signal board.
- No horizontal overflow was observed at 390, 768, 1024, 1280, or 1440 px.
- The collapsed curated radar was expanded on mobile without overlap.
- Real SEC-backed `Apple` search returned six suggestions and resolved AAPL;
  its on-demand 9EMA, 50-day average, breakout, and invalidation levels rendered
  without overflow.
- Autocomplete initially escaped the clipped hero region. It is now in normal
  flow, expands the hero, and stays above the following market strip on mobile
  and desktop.
- Light and dark themes were inspected, and the final browser console contained
  no errors.
- Automated verification passed with 246 tests (5 deselected), Ruff, two parsed
  inline scripts, unique DOM IDs, and a clean `git diff --check`.

## Independent review corrections

- Capital-limit loading now distinguishes loading, ready, and error states. A
  signed-in failure keeps the risk panel visible with a blocking warning and a
  retry action instead of silently removing the safety surface.
- Command-palette ticker results now open the full on-demand analysis module;
  they no longer target hidden market-only state.
- Setup detail stays collapsed on initial load and appears only after the user
  explicitly selects an alert card.
- Autocomplete gives the active keyboard option a visible marker, clears old
  option DOM immediately, and invalidates pending/debounced requests on input,
  submit, selection, outside click, or Escape.
- Browser recheck confirmed the active-option marker, cancellation of a pending
  `Micro` search, no mobile overflow, and a clean console.
- Re-review found that a persistent budget error could recreate its live region
  on each 15-second state poll. Unchanged error/loading states now preserve the
  same warning and Retry node, avoiding repeated screen-reader announcements or
  lost keyboard focus. A Node-backed behavior test verifies node identity across
  repeated renders and replacement only when the error changes.
- Independent final re-review reported no actionable findings on implementation
  head `a83f099`; both GitHub CI jobs passed. Draft PR #12 remains unmerged until
  the user explicitly confirms the merge.
