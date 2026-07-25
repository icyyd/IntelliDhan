# 32 — UX Signals-First Simplicity Pass

**Status:** Implemented on branch `feat/ux-signals-first-simplicity` (unmerged)  
**Date:** 2026-07-25  
**Scope:** Today hierarchy, visual density, progressive disclosure — no signal,
rank, score, auth, or execution logic changes.

## Intent

Reduce visual complexity on Today while increasing modern richness around the
objects that matter: **signals and analysis**. Charts, automation, and deep
research remain available after a deliberate selection.

Logo assets and brand color tokens in `web/assets/brand/` and
`intellidhan-theme.css` are unchanged.

## Reference patterns

- Robinhood Legend: purpose-specific surfaces; secondary tools do not compete
  with the primary workspace.
- TradingView: discovery and inspection as separate tasks connected by symbol
  context; alerts stay scannable without a chart wall.
- Bloomberg “hide complexity”: keep capability, sequence it so the first
  viewport answers one job.

These are interaction references, not visual copies or performance claims.

## Hierarchy (Today)

1. **Signal radar** — full-width primary band; compact cards with thesis and
   confidence.
2. **Market pulse + highlights + macro** — secondary context row.
3. **Premarket / discovery watch** — tertiary band.
4. **Selected setup detail** — only after an explicit card selection; the
   complete plan remains in the context panel rather than duplicating the radar.
5. **Historical reliability / paper performance** — bottom drawer, compressed
   chrome.

The decision rail (auto-trader summary) is removed from the Today scan path so
the page stays a single column of research decisions. Auto-trader remains
reachable from the market-pulse footer action and Settings/policy dialogs.

## Implementation notes

- Additive stylesheet: `web/assets/ux-simplicity.css` (linked from
  `web/index.html`).
- Grid reordering uses existing pane class names (`today-signals-pane`, etc.)
  so behavior and IDs stay stable.
- Signal, focus, calibration, and execution APIs are untouched.
- The legacy full signal board remains hidden on Today to prevent duplicate
  alerts; selecting a radar card opens the complete plan and price context.
- Mobile collapses to a single column: signals → trend → highlights → events →
  movers.

## Acceptance checks

- On open, the first large surface is the signal radar.
- Brand mark and orange/marigold/navy tokens match production assets.
- Selecting a signal still reveals the detail panel only after explicit choice.
- Held-back remains a tab on the full signal board.
- No horizontal overflow at 390 / 768 / 1024 / 1440 px from this stylesheet.
- No change to ranking, confidence math, or live eligibility.

## Deliberately deferred

- Full SPA route split of `web/index.html`.
- Unified context tab strip (highlights/events/movers in one pane).
- Command-palette expansion beyond the existing Jump-to surface.
- Portfolio and Review task surfaces (still planned, not implied by this pass).
