# 11 — UI / UX Specification

A card- and alert-first real-time decision terminal—not a chart wall or a
form-and-modal web page. Design north star: **professional market density ×
consumer-app clarity × calm, rounded surfaces**. Charts are progressive detail;
the first viewport answers what matters, why it matters, and what invalidates it.

## 1. Design System

### Theme & Color
- **Dark-first** (near-black `#0B0E14` canvas, elevated panels `#12161F`, hairline borders `#1E2430`); light theme provided but secondary.
- Accent language taken from the rule posters: **gold** `#D4A537` for emphasis/headers/discipline elements; module identity colors — 0DTE **amber**, Swing **cyan**, LEAPS **violet**, HODL **green**.
- Semantic: bullish `#22C55E`, bearish `#EF4444`, neutral `#94A3B8`, warning `#F59E0B`. Long/short badges: BTO green-filled, STO purple-filled (options-desk convention), never rely on color alone (icons + labels; colorblind-safe pairs).
- Typography: **Inter** UI, **JetBrains Mono** for prices/numbers (tabular figures everywhere numbers align). Type scale 12/13/15/18/24/32.
- Motion: 150–200 ms ease-out; live-updating numbers tick with a brief flash (green/red) — no layout shift, ever.

### Core Components
| Component | Spec |
|---|---|
| **Alert Card** | Content contract in doc 09 §6; readable without opening a chart; shows ticker/underlying, vehicle or option contract/expiry, entry, stop, all staged targets, max risk, validity, evidence status, and score. A compact score label replaces decorative radial gauges. |
| **Trend Matrix chips** | Row of TF chips (M W D 4H 1H 15m 5m) each ▲▲/▲/►/▼/▼▼ with color; tap → mini popover explaining the state's components |
| **Confidence breakdown** | Horizontal stacked bar of the 8 factors, hover for rubric detail |
| **Mini-chart** | Optional price context, collapsed by default; charts support a decision but never displace the alert plan. |
| **Level ladder** | Vertical price axis widget with walls, ORB, VWAP, prior H/L pinned; used in 0DTE cockpit |
| **Stat tiles** | KPI tiles with sparkline + delta chip (used across dashboard/log) |
| **Ticker tape** | Top bar: SPX NDX VIX SMH ES/NQ futures + user positions, live |
| **Profile pane** | TPO/volume profile with value-area shading, POC line, tails, p/b badges, open-type chip, one-timeframing indicator, IB brackets (doc 16 §6) |
| **Pattern overlays** | Detected chart patterns drawn on charts with measured-move target zones (doc 15 §5); stage label (Accumulation/Advancing/Distribution/Declining) on every chart header |
| **Mental Game surfaces** | Pre-market warmup checklist (gates module activation), A/B/C session tagger, emotional-map quick-capture, intervention interstitial (breathing/journal + user's Injecting Logic), RISK-FREE shield state on trade cards (doc 17) |

## 2. Information Architecture

```
◧ Left nav (icon rail): Home · 0DTE · Swings · LEAPS · HODL · Trade Log · Briefings · Settings
◨ Persistent right rail (collapsible): Live Alert Feed (all modules, newest first)
▁ Top bar: ticker tape · market clock/session state · macro-event countdown · data-health dot · cooldown badges
```

### 2.1 Today / Decision Home
- **Decision hero:** active plans, held-back count, and next evaluation—not vanity
  market statistics.
- **Benchmark pulse:** SPX, SPY, and QQQ with freshness/source state.
- **Macro pulse:** `/api/news` supplies up to eight cached, source-linked
  headlines with `HIGH`/`WATCH`/`MARKET` labels. The feed is contextual only,
  filters unsafe links, and renders an explicit unavailable state without
  changing ranking, signals, or execution.
- **Top 3 in focus:** deterministic configured-universe rank with technical,
  financial, and overall research scores, coverage, confirmation, and latest
  filing context.
- **Curated radar:** eight compact candidates; explicit AI review can add
  eligible, non-avoided names to the Research watchlist but cannot rerank or
  trade.
- **Strategy lanes:** 0DTE, Swing, and gated LEAPS research communicate which
  evidence families are active without implying profitability.
- **Rich signal cards:** complete trade plan before any chart. Opening the card
  reveals invalidation, management, risks, and optional price context.
- **"No ready signal" state:** shows what was evaluated and why candidates were
  held back. Silence remains an intentional result.

### 2.2 Module Screens (shared template, tuned per module)
Three-zone cockpit:
1. **Alert stack:** module's active and held-back plans, newest first.
2. **Selected decision:** entry, invalidation, targets, sizing, management,
   historical evidence, data freshness, and event risk.
3. **Optional context drawer:** chart and specialist tables. A chart rack may be
   offered as a secondary expert view, never as the default home hierarchy.

### 2.3 Trade Log — views per doc 10 §3 (ledger/performance/calibration/discipline/journal as tabs).

### 2.4 Settings
Account, per-user capital and risk limits, confidence threshold (raise-only),
Telegram pairing (QR), auto-trade policy (`SIMULATION` by default), read-only Codex MCP
connection status, universe editor, alert sounds, and theme. MCP authentication
and Robinhood credentials remain on the trusted Codex host and never enter the
web app. Use the plain-language UI vocabulary in
`docs/21-accounts-and-personal-settings.md`; keep research terms in methodology
details only.

## 3. Interaction Principles

- **5-second rule:** any alert comprehensible collapsed in ≤ 5 s (tested in usability pass).
- **No dead modals:** progressive disclosure via expansion, drawers, and
  popovers. Manual staged-order actions show the full ticket, risk sentence, and
  explicit confirmation. The separate policy dialog must make the difference
  between `SIMULATION` and time-limited `LIVE` unmistakable; doc 27 remains
  authoritative.
- **Keyboard-first power use:** `g 0` (0DTE), `g s` (swings), `j/k` alert nav, `t` track, `c` chart, `/` command palette (jump to symbol, action search).
- **Latency honesty:** every live number carries a staleness indicator when > 5 s old; degraded data grays out affected cards with reason.
- **Mobile PWA:** responsive down to 390 px — alert feed + briefing + log first; chart rack collapses to single chart; cards optimized for one-thumb triage (swipe: track/pass).
- **Accessibility:** WCAG AA contrast (checked against the dark palette), full keyboard nav, ARIA-live for new alerts, reduced-motion mode.
- **Glossary everywhere (education layer):** every technical/finance term in alerts, briefings, and screener UIs (IV rank, PEG, credit spread, gamma wall, Form 4, lockup…) renders with a subtle dotted underline → hover/tap popover with a one-paragraph in-house definition + "learn more" deep link to the matching Investopedia article. Glossary is a maintained YAML map (`glossary.yaml`: term → definition, investopedia_url); a "beginner mode" setting makes definitions render inline on alert cards.

## 4. Graphic Signature Moments (the "rich" part)

- Signal score is a compact text label paired with the evidence-status badge;
  it must never resemble a guaranteed probability.
- Trend alignment matrix as an at-a-glance "spine" on every context (cards, chart header, briefing).
- The price-ladder graphic on each card mirrors a collar/PL diagram: risk visible spatially, not as text.
- Daily briefing renders as a designed page (poster-like hierarchy echoing the user's rules posters: bold gold headers, card grid), not a wall of text.
- Collar dashboard: animated payoff diagram (long shares + put floor − call cap) that re-shapes live as legs roll.

## 5. Frontend Performance Budget

Initial JS ≤ 350 KB gz (charts lazy-loaded); route change < 100 ms; WebSocket-to-paint < 150 ms; chart interactions 60 fps; virtualized lists everywhere (log, feeds); Lighthouse ≥ 90 perf/accessibility on dashboard.
