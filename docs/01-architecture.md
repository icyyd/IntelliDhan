# 01 — Unified Platform Architecture (v2)

This document ties every subsystem in docs 00–17 into a single coherent platform. The organizing idea is an **event-sourced analytics pipeline around one canonical market-state model**, with a closed behavioral feedback loop:

> **Data** flows in → **Analytics** derive state → **Signals** fire against state → **Delivery** reaches the human → the human's **Behavior** is observed → **Learning** recalibrates the engine → better signals.

Six planes, one bus, one schema package. Every arrow below is a typed event on Redis Streams; every box is independently testable and replayable.

```
╔════════════════════════════ INTELLIDHAN PLATFORM ════════════════════════════╗
║                                                                              ║
║  ① DATA PLANE            ② ANALYTICS PLANE           ③ SIGNAL PLANE          ║
║  ┌─────────────┐   bars  ┌──────────────────┐ state ┌──────────────────┐    ║
║  │ Providers    │ ──────▶│ Derivation DAG    │ ────▶ │ Strategy Registry │    ║
║  │  RH MCP      │ quotes │  L1 indicators    │       │  trigger→score→   │    ║
║  │  Yahoo       │ chains │  L1 levels        │       │  calibrate→gate   │    ║
║  │  FRED/BLS    │ filings│  L1 profile (TPO) │       └────────┬─────────┘    ║
║  │  EDGAR       │ macro  │  L1 patterns      │                │ Setup        ║
║  │  Research*   │ news   │  L2 trend matrix  │       ┌────────▼─────────┐    ║
║  │  TV webhooks │        │  L2 auction state │       │ Veto & Suppressor │    ║
║  └─────┬───────┘         │  L2 stage/macro   │       │ Wall (16 §2, 03§5)│    ║
║        │                 └────────┬─────────┘        └────────┬─────────┘    ║
║  ┌─────▼───────┐                  ▼                  ┌────────▼─────────┐    ║
║  │ DQ Sentinel  │        ┌──────────────────┐        │ Alert Composer    │    ║
║  │ (quarantine) │        │ MARKET STATE      │        │ price/size/copy  │    ║
║  └─────────────┘         │ STORE (per symbol)│        └────────┬─────────┘    ║
║                          └──────────────────┘                 │ Alert        ║
║  ⑥ LEARNING PLANE         ⑤ BEHAVIOR PLANE            ④ DELIVERY PLANE       ║
║  ┌─────────────┐         ┌──────────────────┐        ┌──────────────────┐    ║
║  │ Trade Log    │◀───────│ Error Detectors   │◀───────│ Outbox → WS      │    ║
║  │ paper+real   │ events │ (17 §3)           │  user  │ Telegram, Push   │    ║
║  │ settlement   │        │ Intervention      │ events │ Trackers          │    ║
║  │ calibration  │        │ Ladder (17 §5)    │        │ Briefings (12)   │    ║
║  └──────┬──────┘         └────────┬─────────┘        └──────────────────┘    ║
║         │ calibration maps        │ discipline state (cooldowns, tier,       ║
║         ▼                         ▼  sample mode, size multipliers)          ║
║   Strategy Registry  ◀────  Signal Plane throttles                           ║
║                                                                              ║
║  ⑦ EXPERIENCE: React app ◀── API Gateway (REST + WebSocket, typed) ──▶ all   ║
╚══════════════════════════════════════════════════════════════════════════════╝
   * Research feeds: Finviz screens, Dataroma, Earnings Whispers, Benzinga,
     MarketWatch calendar, MarketBeat lockups (doc 02 §7)
```

## 1. The Spine: Event Bus & Canonical Schemas

All inter-plane communication is **Redis Streams** topics with Pydantic-validated payloads. The schema package (`shared-schemas/`) is the single source of truth, code-generated to TypeScript for the frontend. Nothing crosses a plane boundary untyped.

### 1.1 Topic taxonomy

| Topic | Producer → Consumer | Payload | Cadence |
|---|---|---|---|
| `md.bar.{tf}` | Ingestor → Analytics | `Bar` | on bar close per TF |
| `md.quote` / `md.option_quote` | Ingestor → Composer, UI | `Quote` | 5–15 s |
| `md.chain.{symbol}` | Ingestor → Composer, Analytics | `OptionChain` | 5 min + on demand |
| `md.event.{econ\|earnings\|filing\|rating\|lockup}` | Ingestor → MacroContext, Catalyst engine | typed event | as published |
| `state.indicators.{symbol}.{tf}` | L1 → L2, UI | `IndicatorSnapshot` | bar close |
| `state.levels.{symbol}` | Levels engine → all | `LevelMap` | on change |
| `state.profile.{symbol}` | Profile builder → all | `ProfileState` (VA/POC/IB/open-type/day-type/shape/one-TF) | 30-min period close + intraday deltas |
| `state.patterns.{symbol}.{tf}` | Pattern detectors → engine, UI | `PatternEvent` | bar close |
| `state.composite.{symbol}` | State assembler → Signal plane, UI | `MarketStateSnapshot` | on any input change (debounced) |
| `state.macro` | Macro analyzer → all | `MacroContext` | 5 min + event-driven |
| `sig.setup` | Strategy registry → veto wall | `Setup` (doc 03 §6) | on trigger |
| `sig.suppressed` | Veto wall → UI ("why we're quiet"), log | `SuppressedSetup` | on veto |
| `alert.created` / `alert.updated` / `alert.milestone` | Composer/Trackers → Delivery, Trade log | `Alert` (doc 09 §1) | on emit |
| `user.action` | API gateway → Behavior plane | ack/pass/track/order-staged/stop-moved/override + app telemetry | real-time |
| `behavior.detection` | Error detectors → Intervention, Trade log | `ErrorTag` (doc 17 §3) | on detection |
| `discipline.state` | Intervention ladder → Signal plane, UI | cooldowns, tier, sample-mode, size multiplier | on change |
| `log.settlement` | Settlement worker → Calibration | trade outcomes (paper + real) | EOD + real-time stops/TPs |
| `ops.health` | All services → Ops monitor | heartbeats, staleness, gaps | 10 s |

Streams give us: consumer groups (each plane scales independently), replay from any offset (the replay harness *is* the production code path fed from recorded streams), and at-least-once delivery with idempotent consumers (all payloads carry deterministic IDs).

### 1.2 `MarketStateSnapshot` — the one object everything reads

The **State Assembler** merges every L1/L2 output into a per-symbol composite — the platform's working memory. Strategies, the composer, the UI, and the briefing generator all read *this*, never raw feeds:

```jsonc
{
  "symbol": "NDX", "as_of": "2026-07-10T14:35:00Z", "quality": "OK",   // OK | DEGRADED | QUARANTINED
  "quote": {...},
  "trend": { "M": 78, "W": 71, "D": 62, "4H": 55, "1H": 71, "15m": 80, "5m": 85 },   // doc 03 §2
  "stage": "ADVANCING",                                                  // doc 15 §1
  "indicators": { "per_tf": {...} },                                     // EMA/VWAP/RSI/MACD/ATR/relvol...
  "levels": { "zones": [...], "walls": [...], "expected_move": 0.82 },   // doc 15 §2 + gamma walls
  "profile": {                                                           // doc 16
    "open_type": "OPEN_DRIVE_UP", "day_type_prob": {"trend": .62, "range": .21, ...},
    "ib": {"h": 23180, "l": 23095}, "va": {"h": 23210, "l": 23130, "poc": 23165, "poc_prominent": true},
    "one_timeframing": "UP", "shape": "ELONGATED", "excess": [...], "overnight_inventory": "SHORT",
    "va_relation_prior": "OVERLAPPING_HIGHER", "yesterday_score": 71
  },
  "patterns": [ {"kind": "BULL_FLAG", "tf": "15m", "target_zone": [...], "status": "CONFIRMED"} ],
  "pressure": { "score": 64, "absorption_warning": false },              // doc 15 §7
  "catalysts": { "earnings": null, "ratings": [...], "lockup": null, "filings": [...] },  // doc 02 §7
  "fundamentals": { "quality_score": 82, "valuation_zone": "FAIR", "lynch_tag": "FAST_GROWER" },  // doc 07
  "smart_money": { "insider_cluster": null, "superinvestors": [...] },
  "macro_ref": "state.macro@offset"                                      // joined at read time
}
```

Stored as a Redis hash per symbol (hot) with every version appended to TimescaleDB (audit + replay + backtest features come from the *same* snapshots the live engine saw — this is what makes backtests honest).

## 2. Plane-by-Plane Specification

### ① Data Plane (docs 02, 13)
- **Provider adapters** behind the `DataProvider` protocol; capability-based router with health scoring and failover; every record stamped `source`+`fetched_at`.
- **Feed tiers:** `CRITICAL` (RH MCP quotes/chains — alerting halts without them), `CORE` (bars, econ calendar — degrade gracefully), `ADVISORY` (research feeds — never block anything, doc 02 §7).
- **DQ Sentinel** subscribes to everything it emits: gap/stale/crossed-quote detection → sets `quality` on affected symbols in the State Store → the veto wall drops setups on non-OK symbols automatically (G7).
- **Ingestion scheduling** is owned by the **Market Clock service** (exchange calendar, session states: `PRE`, `RTH`, `POST`, `CLOSED`, half-days) — every poller and every job keys off its published session-state events, so DST/holidays are handled in exactly one place.

### ② Analytics Plane (docs 03 §2, 15, 16)
A **derivation DAG** — pure, incremental functions over streams; no I/O inside computations:

```
bars ─▶ L1-indicators ─┬▶ L2-trend-matrix ─┐
bars ─▶ L1-levels ─────┤                   ├─▶ State Assembler ─▶ state.composite
bars ─▶ L1-profile ────┼▶ L2-auction-state ┤
bars ─▶ L1-patterns ───┤                   │
bars ─▶ L1-candle-CLV ─┴▶ L2-stage-model ──┘
events ─▶ L2-macro-context ────────────────┘
```

- Each node: `f(prev_state, event) → new_state` — O(1) incremental updates, deterministic, no wall-clock reads (market clock injected). Same code runs live, in replay, and in backtests.
- **Profile builder** is a genuine state machine (open-type resolves over the first 1–2 periods, day-type probabilities update per period, spike rule spans sessions) — its states are explicit enum transitions with golden-file tests against the 30 hand-labeled sessions (Phase 3.5 gate).
- Node outputs are versioned: a bug fix bumps the node version, and calibration tables record which analytics version they were fit against (recalibration is forced on version bumps — accuracy guardrail).

### ③ Signal Plane (docs 03, 04–08)
- **Strategy registry**: each `StrategyDef` (doc 08 §1) subscribes to its trigger TF's `state.composite` updates for its universe. Evaluation: trigger predicate → factor scoring (F1–F8 read *only* from the snapshot) → calibration map → confidence.
- **Veto & Suppressor Wall** — single choke point, evaluated in order: data quality → event lockout → auction-state vetoes (trend-day / one-timeframing / p-b shape / non-conviction) → M.A.E. grammar gate → liquidity → extension → R:R → **discipline state** (cooldowns, caps, sample-mode locks, drawdown multiplier from ⑤) → correlation/concurrency caps. Every rejection emits `sig.suppressed` with the failing gate — powering the "why we're quiet" feed and giving the learning plane sub-threshold setups for calibration.
- **Alert Composer**: resolves concrete legs against the live chain, prices entry/stop/TPs, sizes from budgets × discipline multipliers, binds the thesis copy through the **probabilistic-voice linter** (banned-word list, complement display — doc 17 §1 enforced at build time *and* runtime), renders chart snapshot, writes `alert.created`.
- **Trackers**: one lightweight actor per live alert — watches quotes/state for milestone/stop/TP/thesis-break/flatten events → `alert.milestone` (the BABA-style follow-up chain, stop ratchets per doc 05 §4).

### ④ Delivery Plane (docs 09, 12)
- **Outbox pattern**: `alert.*` events land in a delivery outbox table; per-channel workers (WebSocket broadcast, Telegram, Web Push) consume with per-channel retry + failure fallbacks (Telegram down → push + banner + ops alert). Idempotent by `alert_id` + channel.
- **Briefing generator** (doc 12): scheduled by market clock; reads `state.composite` for the index complex + `MacroContext` + research events; LLM composer runs with **fact-binding** — the prompt contains only engine-computed fact objects with IDs, output must cite every claim to a fact ID, non-binding output is rejected and the structured-table fallback ships instead.
- **Telegram bot** inbound: button presses → `user.action` events (same stream the web app feeds) — channel-agnostic behavior capture.

### ⑤ Behavior Plane (docs 10, 17) — the closed loop
- Consumes `user.action` + `alert.*` + fill reconciliations (RH MCP `get_pnl_trade_history` poller).
- **Error detectors**: streaming rules over the joined (alerts × actions × fills) timeline → `ErrorTag` events (revenge, chase, stop-tamper, hesitation, euphoria-size… doc 17 §3), each linked to its root-cause family.
- **Intervention ladder**: consumes detections + P&L state → publishes `discipline.state`: module cooldowns, friction requirements (breathing interstitial before next order), size multipliers (drawdown *and* euphoria guards), user tier (MECHANICAL/SUBJECTIVE/INTUITIVE), 20-trade-sample locks. The Signal plane's veto wall and the Composer's sizing both subscribe — **psychology literally throttles the engine**, one loop, no side channels.
- **Mental-game store**: A/B/C tags, emotional maps, mental hand histories, warmup/cooldown completions — relational tables keyed to sessions and trades; warmup-gate state feeds the UI's module-activation locks.

### ⑥ Learning Plane (docs 03 §4, 10)
- **Trade log writer**: every alert → immutable log record; **paper-track executor** simulates fills/management per the printed plan for *all* alerts (uniform, unbiased); real-track reconciler matches user fills from RH.
- **Settlement worker**: terminal-state resolution (EOD + real-time), MAE/MFE computation, process-adherence + Consistency Score grading.
- **Calibration service** (weekly + on-demand): isotonic fits per strategy class from settled paper outcomes (+ sub-threshold setups), drift detection → auto-demotion events → strategy registry hot-reloads thresholds. Publishes the claimed-vs-realized curves the UI renders (G8).
- **Replay/backtest harness**: same DAG + Signal plane fed from recorded `md.*` streams at accelerated clock; CI runs the 20 curated sessions; walk-forward backtests for new strategies (doc 08 §4 governance) run here too.

### ⑦ Experience Plane (doc 11)
- **API Gateway** (FastAPI): REST for CRUD/history, WebSocket with topic-scoped subscriptions mirroring the bus (`state.composite.{symbol}`, `alert.*`, `discipline.state`, briefing channel). Auth plus the fail-closed auto-trade policy, intent, claim, and receipt contract live here. Robinhood credentials and MCP write tools do not: Codex owns the only active broker write path under doc 27.
- **React app**: TanStack Query for REST, WS client hydrating Zustand stores per topic; all payload types generated from `shared-schemas`. Chart racks (TradingView Advanced + Lightweight), profile panes, pattern overlays, mental-game surfaces per doc 11.

## 3. Storage Model

| Store | Contents | Notes |
|---|---|---|
| **Redis** | Streams (bus), State Store hashes, cooldown/throttle state, caches | Hot path; everything reconstructible from Timescale on restart |
| **TimescaleDB hypertables** | bars (per TF, continuous aggregates), quotes samples, option-quote history (IV history → IVR), snapshots archive, profile states | 10y D / 2y 1H / 90d 1m retention (doc 01 v1 targets) |
| **PostgreSQL relational** | alerts, trade log (immutable, append-only corrections), calibration tables + versions, strategy registry, error tags, mental-game entities, users/settings/budgets, universe & glossary config, briefings archive, outbox | Same cluster as Timescale |
| **Object storage (local FS v1)** | chart snapshots, briefing renders, replay recordings | Content-addressed |

## 4. End-to-End Sequences (with latency budget)

**A. 0DTE alert (bar close → Telegram ≤ 5 s):**
```
14:35:00.0  5m bar closes (NDX)                       [ingestor: ≤300 ms]
      .3    L1 nodes update; State Assembler debounce  [analytics: ≤400 ms]
      .7    ORB_BREAKOUT trigger fires → Setup scored, calibrated (0.78)
      .9    Veto wall: 11 gates pass (one-timeframing=UP helps F1)  [signal: ≤600 ms]
     1.3    Composer: strike resolve → chain re-quote → price/size/copy/linter
     1.9    alert.created → outbox                     [composer: ≤800 ms]
     2.1    WS pushed (web paint ≤ 150 ms after)       [delivery: ≤1 s web]
     3.5    Telegram message + chart snapshot delivered
```

**B. Behavioral loop (stop-out → intervention ≤ 2 s):** fill reconciled → settlement marks STOPPED → detector joins timeline, sees re-entry attempt at 1.6× size in 4 min → `REVENGE` tag → intervention ladder publishes friction requirement → veto wall blocks the module pending interstitial; UI shows the user's own Injecting Logic statement; Telegram note sent. Trade log gains the tag; Sunday's weekly review aggregates it.

**C. 8:30 briefing:** market-clock event 07:50 → data pulls → 08:00 analytics warm → 08:15 fact-pack assembled → LLM compose + fact-binding validation → 08:25 render web/Telegram → 08:30 deliver → 08:30 print lands → surprise-score addendum ≤ 3 min.

## 5. Degradation Matrix (what fails how)

| Failure | Behavior |
|---|---|
| RH MCP down | CRITICAL tier: new alerts halt (G7), open-alert trackers continue on Yahoo quotes flagged `DEGRADED`; ops alert |
| Yahoo down | Backfill/macro proxies pause; no alerting impact |
| Research feed down | ADVISORY: affected factors fall back to neutral; alerts note "analyst/insider data unavailable" |
| Redis restart | State Store rebuilt from Timescale snapshots (≤ 60 s); streams resume from persisted offsets |
| Telegram down | Web push + banner fallback; outbox retries with backoff |
| LLM unavailable | Briefing ships as structured tables (fact pack renders directly); alert copy is template-based anyway — no LLM in the alert path |
| Analytics node crash | Supervisor restarts from last snapshot + stream replay; bar-close SLA monitored, symbols marked DEGRADED past 30 s |

## 6. Monorepo Layout

```
intellidhan/
├── shared-schemas/          # Pydantic models → generated TS (single source of truth)
├── services/
│   ├── ingestor/            # providers, DQ sentinel, market clock
│   ├── analytics/           # DAG nodes: indicators, levels, profile, patterns, stage, macro
│   ├── engine/              # strategy registry, veto wall, composer, trackers
│   ├── behavior/            # error detectors, intervention ladder, mental-game API
│   ├── learning/            # trade log, paper executor, settlement, calibration, replay
│   ├── delivery/            # outbox workers: ws, telegram, push; briefing generator
│   └── gateway/             # FastAPI REST+WS, auth, auto-trade intent/receipt contract
├── web/                     # React app (Vite, TanStack, Zustand, charts, design system)
├── config/                  # universe.yaml, strategies/, glossary.yaml, budgets
├── fixtures/                # golden sessions, labeled profiles, indicator test vectors
└── deploy/                  # docker-compose, Caddy, Prometheus/Grafana, backup jobs
```

v1 deploys as **one Docker Compose host** (services are separate containers sharing Redis/Postgres); the plane boundaries mean any service can later be scaled out or sharded by symbol group without design change.

## 7. Cross-Cutting Concerns

- **Determinism contract:** no service reads wall-clock or random directly; market clock + seeded IDs injected. Any recorded day replays to byte-identical alerts — this is CI's core assertion and the accuracy claim's foundation.
- **Config as data:** universes, strategy defs, factor weights, glossary, budgets are versioned YAML in `config/`, hot-reloaded via a config service with schema validation; every alert records the config version that produced it (full auditability).
- **Observability:** RED metrics per service + domain metrics (`signal_latency_seconds`, `alerts_emitted_total`, `veto_total{gate}`, `calibration_gap`, `delivery_lag_seconds`, `detector_fires_total{tag}`); one Grafana board per plane; ops alerts to owner Telegram.
- **Security:** account auth at the gateway; application secrets in the deployment environment; Telegram chat-ID lock. Robinhood credentials remain in the authenticated Codex host, and official-MCP write scope is isolated to the doc 27 execution loop and dedicated Agentic account—no IntelliDhan service holds broker credentials or tools. Backups are encrypted.
- **Testing pyramid:** schema round-trip tests → analytics golden files (indicators vs TA-Lib, profiles vs hand labels) → strategy unit tests on fixture snapshots → full-session replay assertions → calibration regression checks → UI 5-second-rule + Lighthouse budgets.

## 8. Traceability Matrix (doc → component)

| Spec doc | Primary components |
|---|---|
| 00 principles | Veto wall rules, copy linter, discipline layer defaults |
| 02 data sources | Provider adapters, feed tiers, research pollers, reconciliation jobs |
| 03 signal engine | Strategy registry, factor scorer, calibration service, suppressors |
| 04–07 modules | Strategy defs (config), module UI routes, module-specific analytics subscriptions |
| 08 strategy library | `config/strategies/*.yaml`, governance pipeline in learning plane |
| 09 alerts & sizing | Alert Composer, outbox, Telegram formatter, sizing service |
| 10 trade log | Learning plane: log writer, paper executor, settlement, views API |
| 11 UI/UX | Web app, design system, WS topic map |
| 12 briefing | Briefing generator, fact-binding composer, scheduler |
| 13 guardrails | Gateway policy/intent controls, Codex execution loop, veto wall, kill switches, DQ sentinel, linter |
| 15 technical playbook | L1 patterns/candles/levels nodes, stage model, pressure score |
| 16 market profile | Profile builder state machine, auction vetoes, profile pane API |
| 17 psychology | Behavior plane end-to-end, copy linter rules, mental-game store |
