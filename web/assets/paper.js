(function () {
  "use strict";

  const ET = "America/New_York";
  const POLL_MS = 5000;
  const HEARTBEAT_MAX_MS = 60000;
  const finite = (value) => (typeof value === "number" || (typeof value === "string" && value.trim() !== "")) && Number.isFinite(Number(value));
  const money = (value, signed = false) => finite(value)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", signDisplay: signed ? "exceptZero" : "auto" }).format(Number(value))
    : "—";
  const friendly = (value) => String(value || "Not verified").replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
  const time = (value) => {
    if (!value || !Number.isFinite(Date.parse(value))) return "—";
    return new Intl.DateTimeFormat("en-US", { timeZone: ET, hour: "numeric", minute: "2-digit", second: "2-digit" }).format(new Date(value));
  };
  const sessionDate = (value) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return "Your paper trading session";
    const date = new Date(value + "T12:00:00-05:00");
    if (!Number.isFinite(date.getTime())) return "Your paper trading session";
    return new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "long", month: "long", day: "numeric", year: "numeric" }).format(date);
  };
  const tone = (value) => !finite(value) || Number(value) === 0 ? "" : Number(value) > 0 ? "positive" : "negative";
  const first = (...values) => values.find((value) => value !== null && value !== undefined);
  const heartbeatFresh = (value, now = Date.now()) => {
    const stamp = Date.parse(value);
    return Number.isFinite(stamp) && now - stamp >= -5000 && now - stamp <= HEARTBEAT_MAX_MS;
  };

  // Pure formatting helpers are exported only for dependency-free regression tests.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { money, friendly, time, sessionDate, tone, heartbeatFresh, finite };
  }
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  };
  const set = (id, value, className) => {
    const element = $(id);
    element.textContent = value;
    if (className !== undefined) element.className = className;
  };
  const field = (label, value) => {
    const element = node("div");
    element.append(node("dt", "", label), node("dd", "", value));
    return element;
  };
  const reasonBlock = (heading, reason) => {
    const element = node("div", "reason-block");
    element.append(node("p", "", heading), node("p", "", reason || "No reason recorded."));
    return element;
  };
  const emptyState = (title, description) => {
    const element = node("div", "empty-state");
    const icon = node("span", "empty-icon");
    icon.setAttribute("aria-hidden", "true");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.5");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M8 4h8m-8 0H6a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2M8 4V2h8v2M8 10h8M8 14h6");
    svg.append(path); icon.append(svg);
    const copy = node("div");
    copy.append(node("h3", "", title), node("p", "", description));
    element.append(icon, copy);
    return element;
  };
  const audit = (data) => {
    const details = node("details", "audit-details");
    details.append(node("summary", "", "Recorded evidence"), node("pre", "", JSON.stringify(data, null, 2)));
    return details;
  };

  let lastState = null;
  let lastSuccess = null;
  let lastRendered = "";
  let lastTrades = null;
  let polling = false;
  let timer = null;

  function connection(message, error = false) {
    const banner = $("connection-banner");
    banner.hidden = !message;
    banner.className = "connection-banner" + (error ? " error" : "");
    banner.textContent = message;
  }

  function renderAccount(state) {
    const account = state.account || {};
    set("account-equity", money(account.equity));
    set("account-start", "Starting balance: " + money(account.initial_capital));
    set("account-realized", "Realized P/L: " + money(account.realized_pnl, true));
    const dailyPnl = first(account.today_pnl, account.daily_pnl);
    set("daily-pnl", money(dailyPnl, true), tone(dailyPnl));
    set("daily-note", finite(dailyPnl) ? "Realized + fresh open P/L, after costs" : "Unavailable until marks are fresh");
    set("available-cash", money(first(account.available_cash, account.settled_cash)));
    set("unrealized-pnl", money(account.unrealized_pnl, true), tone(account.unrealized_pnl));
    set("mark-note", state.open_position ? (finite(account.unrealized_pnl) ? "Marked at observed bid, after costs" : "No fresh bid · result not scored") : "No open position");
    const policy = state.policy || {};
    set("max-premium", money(first(policy.max_premium, policy.max_premium_per_trade)));
    set("max-loss", money(first(policy.max_daily_loss, policy.daily_loss_limit)));
    set("max-entries", policy.strategy === "ORB_RETEST_SNIPER_V2" && policy.max_entries_per_day === null
      ? "No count cap · risk limits apply" : first(policy.max_entries, policy.max_entries_per_day, "—"));
  }

  function renderAgent(state) {
    const agent = state.agent || {};
    const isFresh = heartbeatFresh(agent.heartbeat_at);
    const status = String(agent.status || "UNKNOWN").toUpperCase();
    const active = ["RUNNING", "WATCHING", "READY", "WAITING", "WAITING_FOR_RANGE", "PREFLIGHT", "MANAGING", "IN_POSITION", "SCHEDULED"].includes(status);
    const blocked = ["BLOCKED", "DATA_BLOCKED", "STALE", "ERROR", "UNAVAILABLE", "STOPPED", "EXIT_PENDING"].includes(status);
    set("agent-heading", !isFresh && active ? "Agent heartbeat is stale" : friendly(agent.status));
    set("agent-reason", !isFresh && active ? "The displayed account may be out of date. No new paper entries should be assumed." : (agent.reason || "Waiting for an explicit runner status."));
    $("agent-mark").className = "status-mark " + ((!isFresh && active) || blocked ? "error" : isFresh && active ? "healthy" : "caution");
    set("feed-status", friendly(agent.feed_status));
    set("heartbeat", time(agent.heartbeat_at) + (agent.heartbeat_at ? " ET" : ""));
  }

  function renderPosition(state) {
    const position = state.open_position;
    const pendingReason = position && (position.status === "EXIT_PENDING" ? position.exit_reason : position.exit_pending_reason);
    const container = $("position-content");
    container.replaceChildren();
    if (!position) {
      set("position-status", "No open trade", "badge neutral");
      container.append(emptyState("Cash is a position, too.", "The agent will wait for a complete setup and an eligible option. It will not trade just to meet a daily target."));
      return;
    }
    set("position-status", pendingReason ? "Exit waiting for a quote" : "Paper position open", "badge " + (pendingReason ? "warning" : "neutral"));
    const title = node("div", "position-title");
    const copy = node("div");
    copy.append(node("h3", "", [position.symbol, position.option_type || position.direction].filter(Boolean).join(" · ")),
      node("p", "", position.contract || position.option_id || "Contract not recorded"));
    title.append(copy, node("strong", "position-pnl " + tone(state.account?.unrealized_pnl), money(state.account?.unrealized_pnl, true)));
    const grid = node("dl", "position-grid");
    const markPrice = finite(state.account?.unrealized_pnl) ? first(position.mark?.bid, position.mark_price, position.bid) : null;
    grid.append(field("Option entry", money(position.entry_price)), field("Current option bid", money(markPrice)),
      field("Contracts", first(position.quantity, "—")), field("Underlying stop", money(first(position.stop, position.stop_price))),
      field("Underlying target", money(first(position.target, position.target_price))), field("Entered", time(position.entered_at) + " ET"));
    container.append(title, grid, reasonBlock("Why the agent entered", position.entry_reason));
    if (pendingReason) {
      const pending = node("div", "pending-exit");
      pending.append(node("strong", "warning", "Exit requested — not yet priced"), node("p", "", pendingReason), node("p", "", "No exit fill or profit has been invented. The position remains unresolved until a valid observation is available."));
      container.append(pending);
    }
  }

  function renderTrades(state) {
    const trades = Array.isArray(state.trades) ? state.trades : [];
    const serialized = JSON.stringify(trades);
    if (serialized === lastTrades) return;
    lastTrades = serialized;
    const container = $("trade-list");
    const expanded = new Set(Array.from(container.querySelectorAll(".trade-card[open]")).map((item) => item.dataset.tradeId));
    container.replaceChildren();
    set("trade-count", trades.length + (trades.length === 1 ? " trade" : " trades"));
    if (!trades.length) {
      container.append(emptyState("A clean page. A fresh start.", "No trades recorded yet. The journal fills automatically when the agent enters or exits a paper position."));
      return;
    }
    trades.slice().sort((a, b) => (Date.parse(b.entered_at) || 0) - (Date.parse(a.entered_at) || 0)).forEach((trade, index) => {
      const card = node("details", "trade-card");
      card.dataset.tradeId = String(first(trade.id, trade.trade_id, trade.signal_id, index));
      card.open = expanded.has(card.dataset.tradeId);
      const summary = node("summary");
      const main = node("span", "trade-main");
      main.append(node("strong", "", [trade.symbol, trade.option_type || trade.direction].filter(Boolean).join(" · ")), node("small", "", trade.contract || trade.option_id || "Contract not recorded"));
      const result = node("span", "trade-result " + tone(trade.realized_pnl));
      result.append(node("strong", "", money(trade.realized_pnl, true)), node("small", "", trade.exited_at ? "Closed" : friendly(trade.status || "OPEN")));
      summary.append(main, result);
      const body = node("div", "trade-body");
      const grid = node("dl", "position-grid");
      const fees = first(trade.fees, trade.total_fees, finite(trade.entry_fee_cents) ? (Number(trade.entry_fee_cents) + (finite(trade.exit_fee_cents) ? Number(trade.exit_fee_cents) : 0)) / 100 : null);
      grid.append(field("Entry / option", money(trade.entry_price)), field("Exit / option", money(trade.exit_price)), field("Contracts", first(trade.quantity, "—")),
        field("Entry time", time(trade.entered_at) + " ET"), field("Exit time", trade.exited_at ? time(trade.exited_at) + " ET" : "Not closed"), field("Recorded fees", money(fees)));
      body.append(grid, reasonBlock("Why it entered", trade.entry_reason));
      if (trade.exit_reason || trade.exit_pending_reason) body.append(reasonBlock(!trade.exited_at ? "Why an exit is needed" : "Why it exited", trade.exit_reason || trade.exit_pending_reason));
      body.append(audit(trade));
      card.append(summary, body);
      container.append(card);
    });
  }

  function renderWatchlist(state) {
    const items = Array.isArray(state.watchlist) ? state.watchlist : [];
    const container = $("watchlist");
    container.replaceChildren();
    if (!items.length) {
      container.append(node("p", "empty-copy", "No market observations yet. The agent will watch SPY and QQQ when the feed is available."));
      return;
    }
    items.forEach((item) => {
      const element = node("div", "watch-item");
      const title = node("div", "watch-title");
      title.append(node("h3", "", item.symbol || "—"), node("span", "watch-price", money(item.price)));
      element.append(title, node("span", "watch-status", friendly(item.status)), node("p", "", item.reason || "Waiting for the next observation."), node("span", "watch-time", item.observed_at ? "Observed " + time(item.observed_at) + " ET" : "No quote timestamp"));
      container.append(element);
    });
  }

  function renderEvents(state) {
    const events = Array.isArray(state.events) ? state.events : [];
    const container = $("activity-list");
    container.replaceChildren();
    if (!events.length) {
      container.append(node("p", "empty-copy", "No decisions recorded yet. The agent logs skipped setups and data failures as well as trades."));
      return;
    }
    events.slice().sort((a, b) => (Date.parse(first(b.recorded_at, b.at, b.timestamp)) || 0) - (Date.parse(first(a.recorded_at, a.at, a.timestamp)) || 0)).slice(0, 20).forEach((event) => {
      const row = node("div", "activity-row");
      const stamp = first(event.recorded_at, event.at, event.timestamp, event.observed_at);
      const eventTime = node("time", "", time(stamp));
      if (stamp) { eventTime.dateTime = stamp; eventTime.title = stamp; }
      row.append(eventTime, node("span", "activity-kind", [first(event.symbol, event.details?.symbol), friendly(event.kind || event.type)].filter(Boolean).join(" · ")), node("p", "", event.reason || event.message || "Recorded event"));
      container.append(row);
    });
  }

  function render(state) {
    set("session-date", sessionDate(state.session_date));
    renderAgent(state);
    const serialized = JSON.stringify(state);
    if (serialized !== lastRendered) {
      renderAccount(state); renderPosition(state); renderTrades(state); renderWatchlist(state); renderEvents(state);
      lastRendered = serialized;
    }
    set("updated-at", "Last synced " + time(lastSuccess) + " ET. Updates automatically every 5 seconds.");
  }

  async function poll() {
    if (polling) return;
    polling = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch("/api/paper/state", { cache: "no-store", credentials: "same-origin", signal: controller.signal, headers: { Accept: "application/json" } });
      if (response.status === 401) throw new Error("session expired");
      if (!response.ok) throw new Error("state unavailable");
      const state = await response.json();
      if (!state || state.mode !== "SIMULATION" || !state.account || typeof state.account !== "object") throw new Error("invalid paper state");
      lastState = state;
      lastSuccess = new Date().toISOString();
      connection("");
      render(state);
    } catch (error) {
      connection(error.message === "session expired" ? "Your local browser session has expired. Reopen this portal from its local address to reconnect. Any displayed prices and P/L may be stale." : lastState ? "Connection interrupted. Showing the last saved view — prices and P/L may be stale. Reconnecting automatically." : "The paper journal is not connected yet. Keep the local runner open; this page will reconnect automatically.", true);
      set("agent-heading", "Connection unavailable");
      $("agent-mark").className = "status-mark error";
      set("feed-status", "Not verified");
      if (lastSuccess) set("updated-at", "Last successful sync " + time(lastSuccess) + " ET. Displayed values are not current.");
    } finally {
      clearTimeout(timeout);
      polling = false;
      clearTimeout(timer);
      timer = setTimeout(poll, POLL_MS);
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { clearTimeout(timer); poll(); }
  });
  poll();
}());
