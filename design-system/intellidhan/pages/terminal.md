# Terminal page override

This file narrows `../MASTER.md` for the live terminal.

- Default to dark mode and near-opaque surfaces for decision clarity.
- Use orange for the current navigation item, primary action, selected signal,
  focus ring, and the monogram bindu only.
- Keep bullish green and bearish red limited to explicit labeled market state.
- Keep the welcome/search surface compact; signal and focus cards own the page.
- Keep the command bar to search, freshness, market state, theme, and account;
  do not repeat the market strip or settings action there.
- Show one primary action on each Top 3 card. Watchlist and AI follow-ups belong
  in Analyze or the batch-curation flow.
- Charts are progressive disclosure after a signal is selected.
- Historical reliability and paper results are collapsed by default.
- Do not add decorative panels when an existing card can hold the information.
- At tablet widths move the decision rail below Today; at mobile use the task
  navigation and a single content column. Group 0DTE and Swing under one Desks
  selector instead of adding more primary mobile buttons.
- Refresh market data and analysis every two minutes through a single-flight
  batch. Preserve WebSocket state updates and the faster automation-status
  safety poll.
