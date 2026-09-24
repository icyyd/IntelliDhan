/* Beginner desk: presentation only. Research never creates execution authority. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else {
    root.IntelliDhanDesk = api;
    api.mount(document);
  }
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const REFRESH_MS = 120000;
  const HORIZONS = {
    "0DTE": { title: "Today’s setups", label: "Today · 0DTE", tab: "deskTab0dte", note: "Same-day options can lose their full premium. A stock-price setup is not a verified option contract." },
    SWING: { title: "Swing setups", label: "Swing · 2–5 trading days", tab: "deskTabSwing", note: "2–5 trading days is the intended holding window—not a verified exit rule for every existing strategy. Check each setup’s coverage." },
    LEAPS: { title: "Long-term research", label: "LEAPS · long-dated options", tab: "deskTabLeaps", note: "A long-term stock thesis is not an option recommendation. Contract pricing, liquidity, and a validated LEAPS strategy are separate requirements." },
  };
  const array = value => Array.isArray(value) ? value : [];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
  const number = value => value === null || value === undefined || value === "" || typeof value === "boolean" ? null : Number.isFinite(Number(value)) ? Number(value) : null;
  const money = value => number(value) === null ? "Not available" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value));
  const words = value => String(value || "Not available").replaceAll("_", " ").toLowerCase();
  function dateLabel(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return "Time not available";
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(value)) + " · daily data";
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }).format(new Date(value)) + " ET";
  }
  function dailyDateLabel(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return "date unavailable";
    // Daily candles carry session dates, not intraday quote observations.
    const zone = /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? "UTC" : "America/New_York";
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: zone }).format(new Date(value));
  }
  function safeURL(value) {
    try { const url = new URL(String(value)); return ["https:", "http:"].includes(url.protocol) ? url.href : null; }
    catch (_) { return null; }
  }
  function externalLink(label, url) {
    const valid = safeURL(url);
    return valid ? `<a href="${esc(valid)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : esc(label);
  }
  function trendLabel(value) {
    if (["UP", "STRONG_UP"].includes(value)) return { label: "Upward", tone: "up", detail: "Buyers have the stronger trend." };
    if (["DOWN", "STRONG_DOWN"].includes(value)) return { label: "Downward", tone: "down", detail: "Sellers have the stronger trend." };
    if (["SIDEWAYS", "NEUTRAL"].includes(value)) return { label: "Sideways", tone: "wait", detail: "Direction is mixed. Wait for a clearer setup." };
    return { label: "Not confirmed", tone: "wait", detail: "Current direction is not available." };
  }
  function displayTrend(value, stale) {
    const trend = trendLabel(value);
    return { label: stale ? (trend.label === "Not confirmed" ? "Unverified trend" : `Last-known ${trend.label.toLowerCase()}`) : `${trend.label} trend`, tone: stale ? "wait" : trend.tone };
  }
  function actionLabel(summary, paused = false) {
    if (paused || !summary || summary.freshness !== "CURRENT" || ["UNAVAILABLE", "STALE", "RESEARCH_ONLY"].includes(summary.status) || summary.horizon_coverage !== "VALIDATED_SETUP") return "Wait";
    if (summary.next_step === "TRACK_ONLY") return "Track only";
    if (summary.next_step !== "REVIEW_SETUP") return "Wait";
    return summary.research_view === "BUY" ? "Review buy setup" : summary.research_view === "SELL" ? "Review bearish setup" : "Review setup";
  }
  function orderedSignals(summaries, horizon, paused = false) {
    const priority = summary => {
      if (actionLabel(summary, paused).startsWith("Review")) return 0;
      if (!paused && summary.freshness === "CURRENT" && ["CURRENT", "RESEARCH_ONLY"].includes(summary.status)) return 1;
      return 2;
    };
    const timestamp = summary => {
      const value = summary.as_of ? Date.parse(summary.as_of) : NaN;
      return Number.isFinite(value) ? value : -Infinity;
    };
    return array(summaries).filter(summary => summary.horizon === horizon)
      .map((summary, index) => ({ summary, index, priority: priority(summary), timestamp: timestamp(summary) }))
      .sort((a, b) => a.priority - b.priority || b.timestamp - a.timestamp || a.index - b.index)
      .map(item => item.summary);
  }
  function researchLabel(value) {
    return ({ BUY: "Buy view", SELL: "Sell / avoid view", HOLD: "Hold / watch view", WAIT: "Wait for evidence" })[value] || "View not available";
  }
  function levelHTML(level) {
    const unit = level?.unit === "OPTION_USD_PER_SHARE" ? "Option premium per share" : level?.unit === "UNDERLYING_USD_PER_SHARE" ? "Stock / index level" : "Unit not available";
    const range = number(level?.low) !== null && number(level?.high) !== null ? `${money(level.low)}–${money(level.high)}` : money(level?.value);
    return `<div class="desk-level"><span>${esc(level?.label || "Reference")}</span><b>${esc(range)}</b><small>${unit}</small></div>`;
  }
  function listHTML(items, fallback) {
    const values = array(items).filter(item => typeof item === "string" && item.trim());
    return values.length ? `<ul class="desk-reason-list">${values.map(item => `<li>${esc(item)}</li>`).join("")}</ul>` : `<p class="desk-muted">${esc(fallback)}</p>`;
  }
  function playbookHTML(catalog, horizon) {
    const playbooks = array(catalog?.playbooks).filter(item => item.horizon_id === horizon);
    return playbooks.length ? `<p class="desk-muted">These are rule-based research methods, not a promise of profit. Each needs its own realistic testing before use.</p><div class="desk-playbook-grid">${playbooks.map(item => `<article class="desk-playbook"><h3>${esc(item.name)}</h3><p>${esc(item.summary)}</p><span class="desk-tag desk-tag-research">${esc(words(item.validation_status))}</span><h4>What we look for</h4>${listHTML(typeof item.setup === "string" ? [item.setup] : item.setup, "Setup rules are not supplied.")}<h4>When to wait</h4>${listHTML(item.when_to_avoid, "No explicit filters supplied.")}<h4>When the idea ends</h4><p>${esc(item.exit_review || "An exit rule has not been supplied.")}</p><p class="desk-muted">${esc(item.evidence_note || "Evidence not available.")}</p>${listHTML(item.gaps, "No additional evidence gaps supplied.")}</article>`).join("")}</div>` : '<p class="desk-muted">No strategy description is available for this timeframe yet. Missing strategy evidence is not a signal to trade.</p>';
  }
  function sourceObservationsHTML(decision) {
    const labels = { COMPLETED_BAR: "Price bar", CONTENT: "Content date", PROVIDER_OBSERVATION: "Provider retrieval" };
    const rows = array(decision?.source_observations);
    return `<details class="desk-card desk-detail"><summary>When was each source observed?</summary><p class="desk-muted">A newly assembled report does not make old evidence current. Provider retrieval times are not filing dates, and missing feeds do not confirm a thesis.</p>${rows.length ? `<dl class="desk-facts">${rows.map(item => `<div><dt>${esc(item.name || "Source")}<small class="desk-source-meta">${esc(words(item.status))}</small></dt><dd>${esc(dateLabel(item.as_of))}<small class="desk-source-meta">${esc(labels[item.timestamp_kind] || "Timestamp meaning unavailable")} · ${esc(words(item.freshness))}</small></dd></div>`).join("")}</dl>${listHTML(rows.map(item => item.note).filter(Boolean), "No timestamp caveats supplied.")}` : '<p class="desk-muted">Per-source timing is unavailable. Do not assume that news, financial facts, or social data are current.</p>'}<p class="desk-muted">Report assembled: ${esc(dateLabel(decision?.research_generated_at))}. This is not evidence freshness.</p></details>`;
  }
  function signalHTML(summary, source, paused) {
    const trend = trendLabel(summary.trend);
    const action = actionLabel(summary, paused);
    const stale = paused || summary.freshness !== "CURRENT" || ["STALE", "UNAVAILABLE"].includes(summary.status);
    const displayedTrend = displayTrend(summary.trend, stale).label;
    const dataLabel = paused ? "Data: updates paused" : summary.freshness === "STALE" || summary.status === "STALE" ? "Data: stale" : summary.freshness !== "CURRENT" || summary.status === "UNAVAILABLE" ? "Data: unverified" : "Data: current";
    const research = summary.horizon_coverage !== "VALIDATED_SETUP" || summary.status === "RESEARCH_ONLY";
    const firstReason = array(summary.reasons)[0] || summary.headline || "No supporting explanation is available.";
    const blocker = paused ? "Updates are paused. Cached values are not a current entry." : array(summary.blockers)[0] || (action === "Wait" ? "This setup has not cleared the evidence and freshness checks." : "Review the full plan before making any decision.");
    const leg = array(source?.legs)[0];
    const vehicle = source?.vehicle === "OPTION" && leg ? (leg.option_type === "PUT" ? "Buy put candidate · benefits from a fall" : leg.option_type === "CALL" ? "Buy call candidate · benefits from a rise" : "Option research candidate") : "Underlying-price setup · option contract not verified";
    const risk = number(source?.dollar_risk) === null ? "Risk amount not available." : `${money(source.dollar_risk)} ${source.vehicle === "OPTION" ? "full-premium risk reference" : "model risk reference; gaps can exceed a stop"}. Not personalized sizing.`;
    return `<article class="desk-card desk-signal-card">
      <div class="desk-card-top"><span class="desk-symbol">${esc(summary.symbol)}</span><span class="desk-tag desk-tag-${stale ? "wait" : trend.tone}">${esc(displayedTrend)}</span></div>
      <div class="desk-signal-action"><h3>${esc(action)}</h3><span class="desk-tag ${research ? "desk-tag-research" : "desk-tag-wait"}">${research ? "Research only" : "Setup to review"}</span><span class="desk-tag desk-tag-${stale ? "wait" : "research"}">${dataLabel}</span></div>
      <p class="desk-signal-thesis">${esc(firstReason)}</p><p class="desk-wait-reason">${esc(blocker)}</p>
      <div class="desk-card-meta"><span>${esc(HORIZONS[summary.horizon]?.label || summary.horizon_label || "Timeframe unavailable")}</span><span>Setup: ${esc(dateLabel(summary.as_of))}</span><span>Data: ${esc(dateLabel(summary.data_as_of))}</span></div>
      <details class="desk-detail"><summary>Why, price levels &amp; risks</summary><p><b>${esc(vehicle)}</b></p><p>${esc(summary.horizon_note || "Holding-period evidence is not available.")}</p>${listHTML(summary.reasons, "Supporting evidence is not available.")}<div class="desk-levels">${array(summary.levels).map(levelHTML).join("") || '<p class="desk-muted">Price levels not available.</p>'}</div><p class="desk-risk-copy">${esc(risk)}</p>${listHTML(summary.blockers, "No additional blockers supplied. This is not execution approval.")}<p class="desk-muted">Evidence: ${esc(words(summary.horizon_coverage))} · Data: ${paused ? "updates paused" : esc(words(summary.freshness))}. ${esc(summary.headline || "")} Research views are not a win probability. No order is placed from this card.</p>${source?.vehicle === "OPTION" ? '<p class="desk-muted">Option stops are estimates, not loss guarantees. Underlying targets do not predict option returns.</p>' : ""}</details>
      <button class="desk-text-button" type="button" data-desk-analyze="${esc(summary.symbol)}">Understand ${esc(summary.symbol)} <span aria-hidden="true">↗</span></button>
    </article>`;
  }
  function retryDelay(response, payload, now) {
    const header = response.headers?.get("Retry-After");
    const seconds = header ? Number(header) : NaN;
    const headerMS = Number.isFinite(seconds) ? seconds * 1000 : header ? Date.parse(header) - now : 0;
    const bodyMS = Number(payload?.retry_after_seconds) * 1000;
    return Math.max(1000, Number.isFinite(headerMS) ? headerMS : 0, Number.isFinite(bodyMS) ? bodyMS : 0, header || bodyMS > 0 ? 0 : 60000);
  }
  function createController({ fetchImpl, now = Date.now, visible = () => true, changed = () => {} }) {
    const state = { session: null, view: "signals", horizon: "0DTE", snapshot: null, dossier: null, symbol: "", journal: null, news: null, brief: null, playbooks: null, watchlists: [], preferences: null, paused: true, busy: false, analysisBusy: false, error: "", analysisError: "", accountMessage: "", watchMessage: "", lastUpdated: null, retryAt: 0 };
    let epoch = 0, analysisVersion = 0, viewVersion = 0, refreshPromise = null, refreshController = null, analysisController = null;
    const emit = () => changed(state);
    const authenticated = () => state.session?.authenticated === true;
    function clearPrivate(message) {
      epoch++; analysisVersion++;
      refreshController?.abort(); analysisController?.abort();
      Object.assign(state, { session: { authenticated: false }, snapshot: null, dossier: null, symbol: "", journal: null, news: null, brief: null, watchlists: [], preferences: null, paused: true, analysisBusy: false, busy: false, error: message || "", analysisError: "", watchMessage: "", lastUpdated: null });
      emit();
    }
    async function request(url, options = {}, signal) {
      const isLogout = url === "/api/auth/session" && options.method === "DELETE";
      if (now() < state.retryAt && !isLogout) throw Object.assign(new Error("Updates are paused while the server recovers. Your session is preserved."), { status: 503 });
      const controller = new AbortController();
      const abort = () => controller.abort();
      if (signal?.aborted) controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      const timeout = setTimeout(abort, url.startsWith("/api/dossier/") ? 95000 : 55000);
      try {
        const response = await fetchImpl(url, { credentials: "same-origin", cache: "no-store", ...options, signal: controller.signal });
        const payload = await response.json().catch(error => { if (response.ok) throw error; return {}; });
        if (controller.signal.aborted) throw new DOMException("Request was cancelled", "AbortError");
        if (!response.ok) {
          if (response.status === 503 || response.status === 429) {
            state.retryAt = Math.max(state.retryAt, now() + retryDelay(response, payload, now()));
            state.paused = true;
            emit();
          }
          const error = new Error(typeof payload.detail === "string" ? payload.detail : `The request could not be completed (${response.status}).`);
          error.status = response.status;
          if (response.status === 401 && !(url === "/api/auth/session" && options.method === "POST")) clearPrivate("Your session expired. Sign in again to continue.");
          throw error;
        }
        return payload;
      } finally { clearTimeout(timeout); signal?.removeEventListener("abort", abort); }
    }
    async function refresh() {
      if (!visible() || now() < state.retryAt) { emit(); return false; }
      if (refreshPromise) return refreshPromise;
      const generation = epoch;
      refreshController = new AbortController();
      const signal = refreshController.signal;
      state.busy = true; emit();
      const batch = (async () => {
        try {
          const session = await request("/api/auth/session", {}, signal);
          if (generation !== epoch || signal.aborted) return false;
          if (!state.playbooks || state.playbooks.unavailable) await loadPlaybooks();
          if (generation !== epoch || signal.aborted) return false;
          if (!session.authenticated) {
            if (authenticated()) clearPrivate("");
            state.session = session; state.paused = true;
            if (state.view === "analyst" && state.symbol && !state.analysisBusy) await analyze(state.symbol, { background: true });
            emit(); return true;
          }
          state.session = session;
          const tasks = [["snapshot", "/api/state"], ["watchlists", "/api/watchlists"]];
          if (!state.preferences && !session.user?.legacy) tasks.push(["preferences", "/api/account/preferences"]);
          if (state.view === "signals") tasks.push(["news", "/api/news"], ["brief", "/api/daily-brief"]);
          if (state.view === "journal") tasks.push(["journal", "/api/trade-log?limit=30"]);
          const results = await Promise.all(tasks.map(async ([key, url]) => {
            try { return { key, value: await request(url, {}, signal) }; }
            catch (error) { return { key, error }; }
          }));
          if (generation !== epoch || signal.aborted) return false;
          const stateResult = results.find(result => result.key === "snapshot");
          state.paused = Boolean(stateResult?.error) || now() < state.retryAt;
          state.error = stateResult?.error ? "Market updates are unavailable. Last-known values are for context only; wait before acting." : "";
          for (const result of results) {
            if (!result.error) state[result.key] = result.key === "watchlists" ? array(result.value.watchlists) : result.value;
            else if (["news", "brief", "journal"].includes(result.key)) state[result.key] = { unavailable: true };
          }
          if (!state.paused) state.lastUpdated = now();
          if (state.view === "analyst" && state.symbol && !state.analysisBusy) await analyze(state.symbol, { background: true });
          return !state.paused;
        } catch (error) {
          if (generation === epoch && !signal.aborted) { state.paused = true; state.error = "Account or data access is temporarily unavailable. We will retry automatically without signing you out."; }
          return false;
        } finally { if (generation === epoch) { state.busy = false; emit(); } }
      })();
      refreshPromise = batch;
      try { return await batch; }
      finally { if (refreshPromise === batch) { refreshPromise = null; state.busy = false; emit(); } }
    }
    async function analyze(symbol, { background = false } = {}) {
      const normalized = String(symbol || "").trim().toUpperCase();
      if (!/^[A-Z][A-Z0-9.\-]{0,11}$/.test(normalized)) { state.analysisError = "Enter a valid stock ticker, such as AAPL or SPY."; emit(); return false; }
      if (!visible() || now() < state.retryAt) { state.analysisError = "Analysis updates are paused. The current view will retry automatically."; emit(); return false; }
      analysisController?.abort(); analysisController = new AbortController();
      const signal = analysisController.signal, version = ++analysisVersion, generation = epoch;
      const previousSymbol = state.symbol;
      state.symbol = normalized; state.analysisBusy = true; state.analysisError = "";
      if (previousSymbol !== normalized) state.dossier = null;
      emit();
      try {
        const data = await request(`/api/dossier/${encodeURIComponent(normalized)}?include_backtest=false&include_review=${background ? "false" : "true"}`, {}, signal);
        if (version !== analysisVersion || generation !== epoch || signal.aborted) return false;
        state.dossier = data; return true;
      } catch (error) {
        if (version === analysisVersion && generation === epoch && !signal.aborted) state.analysisError = "Analysis could not be updated. Any previous result below is last-known research, not a current trading instruction.";
        return false;
      } finally { if (version === analysisVersion && generation === epoch) { state.analysisBusy = false; emit(); } }
    }
    async function signIn(email, password) {
      state.accountMessage = "Signing in…"; emit();
      try {
        await request("/api/auth/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
        epoch++; refreshController?.abort(); refreshPromise = null; state.preferences = null;
        await refresh();
        state.accountMessage = authenticated() ? "Signed in. Your private settings are loaded." : "Sign-in is being verified. Please wait for account access to recover.";
        emit(); return authenticated();
      } catch (error) { state.accountMessage = error.status === 401 ? "Email or password is incorrect." : "Sign-in could not be verified. Please try again when account access recovers."; emit(); return false; }
    }
    async function signOut() {
      try {
        const result = await request("/api/auth/session", { method: "DELETE" });
        clearPrivate(result.session_revoked === false ? "Signed out on this browser. Server-side revocation could not be completed while storage is unavailable." : "Signed out.");
        state.accountMessage = state.error; emit(); return true;
      } catch (_) { state.accountMessage = "Sign-out could not be confirmed. Your private view is still present; please retry when account access recovers."; emit(); return false; }
    }
    async function saveWatch(symbol) {
      if (!authenticated()) { state.watchMessage = "Sign in to save stocks to your account."; emit(); return false; }
      const normalized = String(symbol || "").trim().toUpperCase();
      if (!/^[A-Z][A-Z0-9.\-]{0,11}$/.test(normalized)) return false;
      const generation = epoch;
      state.watchMessage = `Saving ${normalized}…`; emit();
      try {
        let list = state.watchlists.find(item => item.watchlist_id === "research") || state.watchlists[0];
        if (!list) { list = await request("/api/watchlists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Research" }) }); }
        if (generation !== epoch || !authenticated()) return false;
        await request(`/api/watchlists/${encodeURIComponent(list.watchlist_id)}/symbols/${encodeURIComponent(normalized)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        const lists = await request("/api/watchlists");
        if (generation !== epoch) return false;
        state.watchlists = array(lists.watchlists); state.watchMessage = `${normalized} saved to your watchlist.`; emit(); return true;
      } catch (_) { if (generation === epoch) { state.watchMessage = "The stock could not be saved. Existing watchlist entries are unchanged."; emit(); } return false; }
    }
    async function loadPlaybooks() {
      if (!visible() || now() < state.retryAt) return false;
      try { state.playbooks = await request("/api/playbooks"); emit(); return true; }
      catch (_) { state.playbooks = { unavailable: true }; emit(); return false; }
    }
    function setView(view) {
      if (!["signals", "analyst", "journal"].includes(view)) return Promise.resolve(false);
      const prior = refreshPromise, version = ++viewVersion;
      state.view = view; emit();
      // A running batch owns the view that scheduled its requests. Queue one
      // follow-up for the latest navigation, rather than reuse the wrong scope.
      return prior ? prior.then(() => version === viewVersion && state.view === view ? refresh() : false) : refresh();
    }
    function setHorizon(horizon) { if (!HORIZONS[horizon]) return; state.horizon = horizon; emit(); }
    function pause() { epoch++; analysisVersion++; refreshController?.abort(); analysisController?.abort(); refreshPromise = null; state.paused = true; state.busy = false; state.analysisBusy = false; emit(); }
    return { state, refresh, analyze, signIn, signOut, saveWatch, loadPlaybooks, setView, setHorizon, pause, request };
  }

  function mount(document) {
    const $ = id => document.getElementById(id);
    const client = createController({ fetchImpl: (...args) => fetch(...args), visible: () => document.visibilityState !== "hidden", changed: render });
    let preferencesApplied = false;
    function render(state) {
      const expanded = new Set([...document.querySelectorAll("details[data-desk-detail][open]")].map(detail => detail.dataset.deskDetail));
      const focusedDetail = document.activeElement?.tagName === "SUMMARY" ? document.activeElement.parentElement?.dataset.deskDetail : null;
      const signedIn = state.session?.authenticated === true;
      $("deskConnection").textContent = document.visibilityState === "hidden" ? "Updates paused while this tab is hidden" : Date.now() < state.retryAt ? "Updates paused · server recovery" : state.busy ? "Checking the latest data…" : !signedIn ? "Sign in for signals and your watchlist" : state.paused ? "Updates unavailable · wait for fresh data" : "Connected · evidence-aware research";
      $("deskUpdated").textContent = state.lastUpdated ? `Last checked ${dateLabel(state.lastUpdated)} · every 2 minutes` : "Automatically checks every 2 minutes";
      $("deskNotice").hidden = !state.error; $("deskNotice").textContent = state.error;
      $("deskAccountButton").textContent = signedIn ? state.session.user?.display_name?.split(" ")[0] || "Account" : "Sign in";
      $("deskSignedIn").hidden = !signedIn; $("deskSignedOut").hidden = signedIn;
      $("deskAccountTitle").textContent = signedIn ? "Your account" : "Sign in";
      $("deskAccountIdentity").textContent = signedIn ? `${state.session.user?.display_name || "Account"} · ${state.session.user?.email || "Administrator session"}` : "";
      $("deskAccountStatus").textContent = state.accountMessage;
      if (state.session && !state.session.accounts_enabled && !signedIn) $("deskSetupHelp").textContent = "No local account is available yet. From the project directory, run scripts/local_runtime.py create-admin in Terminal. Your administrator can also use the advanced account setup screen.";
      ["signals", "analyst", "journal"].forEach(view => { $("desk" + view[0].toUpperCase() + view.slice(1)).hidden = state.view !== view; });
      document.querySelectorAll("[data-desk-view]").forEach(button => { if (button.dataset.deskView === state.view) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); });
      if (state.preferences && !preferencesApplied) {
        preferencesApplied = true;
        const theme = state.preferences.theme === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : state.preferences.theme;
        document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
        document.documentElement.dataset.reduceMotion = String(Boolean(state.preferences.reduced_motion));
      }
      if (!signedIn) preferencesApplied = false;
      renderSignals(state); renderContext(state); renderWatchlist(state); renderAnalysis(state); renderJournal(state);
      document.querySelectorAll("details[data-desk-detail]").forEach(detail => {
        if (expanded.has(detail.dataset.deskDetail)) detail.open = true;
        if (focusedDetail === detail.dataset.deskDetail) detail.querySelector("summary")?.focus({ preventScroll: true });
      });
    }
    function renderSignals(state) {
      const horizon = HORIZONS[state.horizon];
      document.querySelectorAll("[data-desk-horizon]").forEach(button => { const selected = button.dataset.deskHorizon === state.horizon; button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1; });
      $("deskSignalPanel").setAttribute("aria-labelledby", horizon.tab);
      $("deskHorizonTitle").textContent = horizon.title; $("deskHorizonNote").textContent = horizon.note;
      const summaries = orderedSignals(state.snapshot?.signal_desk?.signals, state.horizon, state.paused);
      $("deskSignalCount").textContent = summaries.length ? `Showing ${Math.min(6, summaries.length)} of ${summaries.length} recent setups · current first, then newest in each group. More history is in My journal.` : "";
      const available = summaries.filter(item => actionLabel(item, state.paused).startsWith("Review"));
      const timeframe = state.horizon === "0DTE" ? "5m" : state.horizon === "SWING" ? "D" : "W";
      const timeframeLabel = ({ "5m": "5-minute", D: "daily", W: "weekly" })[timeframe];
      const spy = state.snapshot?.symbols?.SPY;
      const spyQuality = spy?.data_quality || state.snapshot?.readiness?.symbols?.SPY;
      const blocked = state.paused || spyQuality?.actionable === false || !spyQuality || ["STALE", "QUARANTINED", "WAITING"].includes(spyQuality.status);
      const trend = trendLabel(blocked ? "UNKNOWN" : spy?.matrix?.[timeframe]);
      $("deskPulse").innerHTML = `<p class="desk-eyebrow">SPY ${timeframeLabel} trend</p><div class="desk-pulse-title"><h2>${trend.label}</h2><span class="desk-tag desk-tag-${trend.tone}">${available.length ? "Review a setup" : "Wait for confirmation"}</span></div><p>${esc(trend.detail)} ${available.length ? `${available.length} setup${available.length === 1 ? " is" : "s are"} available for review—not automatic execution.` : "No verified entry is being recommended here."}</p><small>Trend describes direction, not a buy or sell instruction.</small>`;
      $("deskBenchmarks").innerHTML = ["SPY", "QQQ", "SPX"].map(symbol => {
        const data = state.snapshot?.symbols?.[symbol], quality = data?.data_quality || state.snapshot?.readiness?.symbols?.[symbol];
        const stale = state.paused || !quality || quality.actionable === false;
        const reading = trendLabel(stale ? "UNKNOWN" : data?.matrix?.[timeframe]);
        return `<div class="desk-benchmark"><b>${symbol}</b><strong>${esc(money(data?.last))}</strong><span class="desk-tone-${reading.tone}">${reading.label}</span><small>${stale ? "Last-known / unavailable" : `${timeframeLabel} context`}</small></div>`;
      }).join("");
      const sources = array(state.snapshot?.alerts);
      $("deskSignalList").innerHTML = summaries.length ? summaries.slice(0,6).map(summary => signalHTML(summary, sources.find(item => item.alert_id === summary.source_alert_id), state.paused)).join("") : `<div class="desk-empty"><span class="desk-tag desk-tag-wait">Wait</span><h3>${state.session?.authenticated ? "No supported setup right now." : "Sign in to load your signal desk."}</h3><p>${state.horizon === "LEAPS" ? "A validated LEAPS signal is not available. Use Stock analyst to investigate the business and long-term trend; that research does not select an option contract." : state.paused ? "Current market data is not verified. We will check again automatically." : "The engine has not supplied a supported setup for this timeframe. Waiting is a valid decision."}</p></div>`;
      $("deskSignalList").querySelectorAll("details").forEach((detail, index) => { const summary = summaries[index]; detail.dataset.deskDetail = `signal:${summary?.source_alert_id || `${summary?.horizon}:${summary?.symbol}`}`; });
      $("deskPlaybooks").innerHTML = playbookHTML(state.playbooks, state.horizon);
    }
    function renderContext(state) {
      const brief = state.brief;
      $("deskBrief").innerHTML = brief && !brief.unavailable ? `<span class="desk-tag desk-tag-wait">${esc(brief.freshness_label || words(brief.status))}</span><h3>${esc(brief.headline || "Brief not available")}</h3>${listHTML(array(brief.summary).slice(0,2), brief.notice || "No summary was supplied.")}<p class="desk-muted">${esc(dateLabel(brief.generated_at))} · Research context, not a live entry.</p>` : '<p class="desk-muted">The daily brief is unavailable. It will retry automatically.</p>';
      const news = array(state.news?.items).slice(0,3);
      $("deskNews").innerHTML = news.length ? news.map(item => `<article class="desk-headline"><h3>${externalLink(item.title || "Market headline", item.link)}</h3><small>${esc(item.source || "Source unavailable")} · ${esc(dateLabel(item.published_at))}</small></article>`).join("") : '<p class="desk-muted">No current headlines are loaded. We do not substitute invented market news.</p>';
      const events = array(brief?.economic_events).slice(0,4);
      $("deskEvents").innerHTML = events.length ? events.map(item => `<div class="desk-event"><b>${esc(item.event || item.name || "Economic event")}</b><span>${esc(item.time_et || item.time || "Time unavailable")}</span><small>${esc(item.forecast_vs_previous || item.impact || "Check release-time volatility")}</small></div>`).join("") : '<p class="desk-muted">No events are loaded. Check an economic calendar before trading; missing data does not mean there are no releases.</p>';
    }
    function renderWatchlist(state) {
      const symbols = [...new Set(state.watchlists.flatMap(list => array(list.symbols)))].slice(0,40);
      $("deskWatchlist").innerHTML = (symbols.length ? `<div class="desk-watch-chips">${symbols.map(symbol => `<button type="button" class="desk-watch-chip" data-desk-analyze="${esc(symbol)}">${esc(symbol)} <span aria-hidden="true">↗</span></button>`).join("")}</div>` : `<p class="desk-muted">${state.session?.authenticated ? "No saved stocks yet. Analyze a ticker and save it here." : "Sign in to see your private watchlist."}</p>`) + `<p class="desk-muted" role="status">${esc(state.watchMessage)}</p>`;
    }
    function renderAnalysis(state) {
      $("deskAnalysisStatus").textContent = state.analysisBusy ? `Updating ${state.symbol} research…` : state.analysisError || (state.dossier ? "Research refreshed. Price timestamps and coverage remain visible below." : "");
      $("deskAnalyzeButton").disabled = state.analysisBusy;
      $("deskAnalysisContent").setAttribute("aria-busy", String(state.analysisBusy));
      if (!state.dossier) { $("deskAnalysisContent").innerHTML = `<div class="desk-empty"><h2>${state.analysisBusy ? "Gathering the evidence…" : "One ticker. The complete picture."}</h2><p>${state.analysisBusy ? "Checking price history and available business context. Missing evidence stays missing." : "Search a ticker to see its trend, research view, and key risks."}</p></div>`; return; }
      const dossier = state.dossier, analysis = dossier.analysis || {}, decision = dossier.decision || {}, intelligence = dossier.intelligence || {}, company = intelligence.company || {}, trend = trendLabel(decision.trend), multi = intelligence.multi_brain || {};
      const stale = Boolean(state.analysisError) || decision.freshness !== "CURRENT";
      const horizons = array(decision.horizons);
      const companyName = company.name || dossier.security?.name || analysis.symbol;
      const financials = array(intelligence.fundamentals?.metrics).slice(0,5);
      const filings = array(intelligence.filings?.items).slice(0,3);
      const news = array(intelligence.news?.headlines).slice(0,3);
      const review = multi.claude_review;
      $("deskAnalysisContent").innerHTML = `<section class="desk-analysis-hero"><div><p class="desk-eyebrow">${esc(companyName || "Company research")}</p><div class="desk-analysis-symbol"><h2>${esc(analysis.symbol || state.symbol)}</h2><strong>${esc(money(analysis.price))}</strong></div><p>${esc(company.description || "A verified business description is not available from the connected providers.")}</p><small>${esc(company.overview_limitation || "")} Price as of ${esc(dateLabel(analysis.as_of))} · ${esc(analysis.source || "Source unavailable")}</small></div><div class="desk-verdict"><span class="desk-tag desk-tag-${trend.tone}">${trend.label} trend</span><h3>${stale ? "Wait for fresh evidence" : esc(researchLabel(decision.research_view))}</h3><span class="desk-tag desk-tag-research">Research view · not an order</span><p>${esc(decision.headline || "There is not enough evidence to provide a clear research view.")}</p><small>A sell view is not an instruction to open a short position. A hold view does not mean we know what you own.</small></div></section><div class="desk-analysis-toolbar"><button class="desk-button desk-button-secondary" type="button" data-desk-save="${esc(analysis.symbol || state.symbol)}">Save to watchlist</button><span role="status">${esc(state.watchMessage)}</span></div><div class="desk-explain-grid"><section class="desk-card"><p class="desk-eyebrow">The case</p><h3>Why this view?</h3>${listHTML(decision.reasons, "The available inputs do not support a reliable explanation yet.")}</section><section class="desk-card"><p class="desk-eyebrow">Before you act</p><h3>What could change it?</h3>${listHTML(decision.blockers, "No explicit blockers were supplied. That does not remove market, gap, valuation, or options risk.")}<p class="desk-muted">${esc(decision.horizon_note || "This is current research, not a validated trade for a specific holding period.")}</p></section></div><section class="desk-card"><h3>Which timeframe does this support?</h3><div class="desk-coverage-grid">${horizons.length ? horizons.map(item => `<div class="desk-coverage"><b>${esc(item.label || HORIZONS[item.horizon]?.label || item.horizon)}</b><span class="desk-tag desk-tag-research">${esc(words(item.coverage))}</span><p>${esc(item.note || "Holding-period evidence is unavailable.")}</p></div>`).join("") : '<p class="desk-muted">No holding-period validation is available. Daily stock analysis is not a same-day or LEAPS option signal.</p>'}</div></section><details class="desk-card desk-detail"><summary>Price levels, business evidence &amp; sources</summary><h3>Levels that matter</h3><div class="desk-levels">${array(decision.levels).map(levelHTML).join("") || '<p class="desk-muted">No verified price references available.</p>'}</div><p class="desk-muted">References describe the underlying stock unless explicitly marked as option premiums. They are not guaranteed stops or return forecasts.</p><div class="desk-explain-grid"><section><h3>Business health</h3>${financials.length ? `<dl class="desk-facts">${financials.map(metric => `<div><dt>${esc(metric.label)}</dt><dd>${number(metric.value) === null ? "Not available" : `${esc(Number(metric.value).toFixed(1))}%`}</dd></div>`).join("")}</dl>` : `<p class="desk-muted">${esc(intelligence.fundamentals?.limitation || "Current financial facts are not available.")}</p>`}<p class="desk-muted">${esc(company.sector || company.industry || "Sector unavailable")}${company.exchange ? ` · ${esc(company.exchange)}` : ""}</p></section><section><h3>Current coverage</h3><dl class="desk-facts">${Object.entries(dossier.coverage || {}).map(([key, value]) => `<div><dt>${esc(words(key))}</dt><dd>${esc(words(value))}</dd></div>`).join("")}</dl><p class="desk-muted">Missing fundamentals or social data are not treated as favorable. Social attention alone does not establish direction.</p></section></div><h3>Source documents</h3>${filings.length ? `<ul class="desk-source-list">${filings.map(item => `<li>${externalLink(`${item.form || "Company"} filing · ${item.filed_at || "Date unavailable"}`, item.url)}</li>`).join("")}</ul>` : '<p class="desk-muted">Official filing links are not available.</p>'}<h3>News context</h3>${news.length ? news.map(item => `<p>${externalLink(item.title, item.url || item.link)}<small class="desk-source-meta">${esc(item.source || "Source unavailable")} · ${esc(dateLabel(item.published_at))}</small></p>`).join("") : '<p class="desk-muted">No current company headlines are available.</p>'}${review ? `<h3>Independent research review</h3><p>${esc(review.status === "READY" ? review.summary : review.reason || "Not available")}</p><small>Advisory only. Cannot change deterministic posture, sizing, or execution.</small>` : ""}<p class="desk-muted">${esc(multi.summary || "")} Analysis is based on historical observations, not a promise about the future. Detailed methods and historical comparisons remain in the advanced workspace.</p></details>`;
      const trendBadge = $("deskAnalysisContent").querySelector(".desk-verdict .desk-tag");
      const visibleTrend = displayTrend(decision.trend, stale);
      trendBadge.textContent = visibleTrend.label;
      trendBadge.className = `desk-tag desk-tag-${visibleTrend.tone}`;
      const priceHistory = $("deskAnalysisContent").querySelector(".desk-analysis-hero > div:first-child > small");
      priceHistory.textContent = `${company.overview_limitation || ""} Daily price history dated ${dailyDateLabel(analysis.as_of)} · ${analysis.source || "Source unavailable"}`.trim();
      $("deskAnalysisContent").insertAdjacentHTML("beforeend", sourceObservationsHTML(decision));
      $("deskAnalysisContent").querySelectorAll("details").forEach((detail, index) => { detail.dataset.deskDetail = `analysis:${state.symbol}:${index}`; });
    }
    function renderJournal(state) {
      const journal = state.journal;
      if (!journal || journal.unavailable) { $("deskJournalContent").innerHTML = `<div class="desk-empty"><h2>${journal?.unavailable ? "Journal temporarily unavailable." : "No journal loaded yet."}</h2><p>${state.session?.authenticated ? "We will check again automatically while this page is open." : "Sign in to review shared signal and simulation history."}</p></div>`; return; }
      const rows = array(journal.automation_trades).slice(0,15);
      const paper = array(journal.paper_trades).slice(0,15);
      $("deskJournalContent").innerHTML = `<section class="desk-card"><h2>Option observations &amp; broker records</h2><p class="desk-muted">${journal.broker_history_visible ? "Real and simulated observations are labeled separately. This is a read-only journal." : "Broker-level history is restricted to administrators. No hidden records are implied by this view."}</p>${rows.length ? rows.map(row => `<article class="desk-journal-row"><div><b>${esc(row.symbol || row.underlying_symbol || "Option observation")} · ${esc(words(row.event))}</b><span class="desk-tag ${row.simulated || row.mode === "SIMULATION" ? "desk-tag-research" : "desk-tag-wait"}">${row.simulated || row.mode === "SIMULATION" ? "Simulation" : "Broker record"}</span></div><p>${esc(row.reason || "Reason not supplied")}</p><small>${esc(dateLabel(row.observed_at || row.recorded_at))} · Observed premium ${esc(money(row.option_price))}${number(row.realized_pnl) === null ? "" : ` · Recorded P&L ${esc(money(row.realized_pnl))}`}</small></article>`).join("") : '<p class="desk-muted">No option entry or exit observations are available.</p>'}</section><section class="desk-card"><h2>Underlying paper outcomes</h2><p class="desk-muted">Stock-price simulations only. They do not include option bid/ask spreads or prove option profitability. Unknown outcomes are not wins or losses.</p>${paper.length ? paper.map(row => `<article class="desk-journal-row"><div><b>${esc(row.symbol || "Paper trade")}</b><span class="desk-tag desk-tag-research">${esc(words(row.outcome))}</span></div><p>${esc(row.resolution_note || row.exit_reason || "Underlying-price paper model")}</p><small>${esc(dateLabel(row.exit_ts || row.entry_ts || row.created_at))}${number(row.realized_r) === null ? " · Result not scored" : ` · ${esc(Number(row.realized_r).toFixed(2))}R (units of model risk)`}</small></article>`).join("") : '<p class="desk-muted">No underlying paper trades recorded yet.</p>'}</section>`;
    }
    document.addEventListener("click", event => {
      const view = event.target.closest("[data-desk-view]");
      if (view) client.setView(view.dataset.deskView);
      const horizon = event.target.closest("[data-desk-horizon]");
      if (horizon) client.setHorizon(horizon.dataset.deskHorizon);
      const analyze = event.target.closest("[data-desk-analyze]");
      if (analyze) { $("deskAnalysisSymbol").value = analyze.dataset.deskAnalyze; client.setView("analyst"); client.analyze(analyze.dataset.deskAnalyze); }
      const save = event.target.closest("[data-desk-save]");
      if (save) { if (!client.state.session?.authenticated) $("deskAccountDialog").showModal(); else { save.disabled = true; client.saveWatch(save.dataset.deskSave).finally(() => { save.disabled = false; }); } }
    });
    document.querySelector(".desk-horizons").addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const tabs = [...document.querySelectorAll("[data-desk-horizon]")], index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click(); tabs[next].focus();
    });
    $("deskAnalysisForm").addEventListener("submit", event => { event.preventDefault(); if (event.currentTarget.reportValidity()) client.analyze($("deskAnalysisSymbol").value); });
    $("deskAccountButton").addEventListener("click", () => $("deskAccountDialog").showModal());
    $("deskCloseAccount").addEventListener("click", () => $("deskAccountDialog").close());
    $("deskSignInForm").addEventListener("submit", async event => {
      event.preventDefault(); if (!event.currentTarget.reportValidity()) return;
      $("deskSignInButton").disabled = true;
      const password = $("deskPassword").value; $("deskPassword").value = "";
      try { if (await client.signIn($("deskEmail").value, password)) $("deskAccountDialog").close(); }
      finally { $("deskSignInButton").disabled = false; }
    });
    $("deskSignOutButton").addEventListener("click", () => client.signOut());
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") client.pause(); else client.refresh(); });
    setInterval(() => client.refresh(), REFRESH_MS);
    render(client.state); client.refresh();
    return client;
  }
  return Object.freeze({ REFRESH_MS, HORIZONS, esc, number, dateLabel, dailyDateLabel, safeURL, trendLabel, displayTrend, actionLabel, orderedSignals, levelHTML, playbookHTML, sourceObservationsHTML, signalHTML, retryDelay, createController, mount });
});
