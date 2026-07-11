# 11 — UI / UX Specification

A graphically rich, real-time trading workstation — not a form-and-modal web page. Design north star: **Bloomberg density × consumer-app clarity × the dark/gold discipline aesthetic** of the user's rule posters.

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
| **Alert Card** | Content contract in doc 09 §6; collapsed height ~160 px; confidence dial (radial, gold ring at 75+); vertical price-ladder mini-graphic showing entry zone/stop/TPs to scale with live price marker |
| **Trend Matrix chips** | Row of TF chips (M W D 4H 1H 15m 5m) each ▲▲/▲/►/▼/▼▼ with color; tap → mini popover explaining the state's components |
| **Confidence breakdown** | Horizontal stacked bar of the 8 factors, hover for rubric detail |
| **Mini-chart** | Lightweight-Charts candle panel with levels drawn; present on every card, 60 fps pan/zoom |
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

### 2.1 Home / Command Center
- **Hero: Daily Briefing panel** (doc 12 content, rendered rich): market bias meter (risk-on↔risk-off), index cards (SPX/NDX/SMH/VIX) each with trend matrix + key levels, macro calendar strip with countdown chips, headline & ratings digest.
- **Sector Heat Map widget:** Finviz-style treemap (internally computed; 1D/1W/1M toggle), cell click → sector drill-down with constituent RS ranks; "open full map on Finviz" link-out.
- **Today row:** active alerts across modules (horizontal card scroll), P&L-today tile, budget-used gauges per module, cooldown/kill-switch status.
- **Calibration strip:** claimed-vs-realized sparkline + "engine honesty" badge (RULE-C2, always visible).
- **"No edge today" state** is designed, not empty: shows what was evaluated and why nothing passed (suppression tape) — reinforces RULE-M3.

### 2.2 Module Screens (shared template, tuned per module)
Three-zone cockpit:
1. **Chart rack** (left ⅔): TradingView Advanced Chart with alert overlays; TF switcher synced to trend matrix; 0DTE gets a multi-chart 2×2 rack option (SPX/NDX/SMH/TQQQ).
2. **Alert stack** (right ⅓): module's active + recent alerts; sub-section tabs (Directional / Advanced-Income); each card expandable in place — no modals for reading (modals reserved solely for order-stage confirmation).
3. **Bottom drawer:** module vitals — 0DTE: ORB table, expected move, walls, breadth pack; Swing: catalyst calendar, sector RS heatmap; LEAPS: collar dashboards (doc 06 §2 card), IVR table; HODL: quality×valuation quadrant scatter.

### 2.3 Trade Log — views per doc 10 §3 (ledger/performance/calibration/discipline/journal as tabs).

### 2.4 Settings
Budgets (per-module daily/standing capital — the sizing inputs), risk caps, confidence threshold (raise-only), Telegram pairing (QR), Robinhood MCP connection + order-staging toggle (off by default), universe editor, alert sounds, theme.

## 3. Interaction Principles

- **5-second rule:** any alert comprehensible collapsed in ≤ 5 s (tested in usability pass).
- **No dead modals:** progressive disclosure via expansion, drawers, popovers; modal only for irreversible actions (order staging), and that modal shows the full ticket + risk sentence + explicit confirm.
- **Keyboard-first power use:** `g 0` (0DTE), `g s` (swings), `j/k` alert nav, `t` track, `c` chart, `/` command palette (jump to symbol, action search).
- **Latency honesty:** every live number carries a staleness indicator when > 5 s old; degraded data grays out affected cards with reason.
- **Mobile PWA:** responsive down to 390 px — alert feed + briefing + log first; chart rack collapses to single chart; cards optimized for one-thumb triage (swipe: track/pass).
- **Accessibility:** WCAG AA contrast (checked against the dark palette), full keyboard nav, ARIA-live for new alerts, reduced-motion mode.
- **Glossary everywhere (education layer):** every technical/finance term in alerts, briefings, and screener UIs (IV rank, PEG, credit spread, gamma wall, Form 4, lockup…) renders with a subtle dotted underline → hover/tap popover with a one-paragraph in-house definition + "learn more" deep link to the matching Investopedia article. Glossary is a maintained YAML map (`glossary.yaml`: term → definition, investopedia_url); a "beginner mode" setting makes definitions render inline on alert cards.

## 4. Graphic Signature Moments (the "rich" part)

- Confidence dial fills gold as it crosses 75 with a subtle glow — alerts feel earned.
- Trend alignment matrix as an at-a-glance "spine" on every context (cards, chart header, briefing).
- The price-ladder graphic on each card mirrors a collar/PL diagram: risk visible spatially, not as text.
- Daily briefing renders as a designed page (poster-like hierarchy echoing the user's rules posters: bold gold headers, card grid), not a wall of text.
- Collar dashboard: animated payoff diagram (long shares + put floor − call cap) that re-shapes live as legs roll.

## 5. Frontend Performance Budget

Initial JS ≤ 350 KB gz (charts lazy-loaded); route change < 100 ms; WebSocket-to-paint < 150 ms; chart interactions 60 fps; virtualized lists everywhere (log, feeds); Lighthouse ≥ 90 perf/accessibility on dashboard.
