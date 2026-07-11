# Repository Layout

This repo houses both the **specification** ([README.md](README.md) + [docs/](docs/)) and the **implementation** (structure below, per [docs/01-architecture.md](docs/01-architecture.md) §6).

```
intellidhan/
├── docs/                    # The v0.1 specification (docs 00–17) — read first
├── shared-schemas/          # Pydantic models → generated TS types (single source of truth)
├── services/
│   ├── ingestor/            # Data plane: providers, DQ sentinel, market clock
│   ├── analytics/           # Derivation DAG: indicators, levels, profile, patterns, stage, macro
│   ├── engine/              # Signal plane: strategy registry, veto wall, composer, trackers
│   ├── behavior/            # Behavior plane: error detectors, intervention ladder, mental game
│   ├── learning/            # Learning plane: trade log, paper executor, settlement, calibration
│   ├── delivery/            # Delivery plane: outbox workers (WS/Telegram/push), briefings
│   └── gateway/             # API gateway: FastAPI REST+WS, auth, order-staging confirm flow
├── web/                     # React app (Vite, TanStack, Zustand, TradingView/Lightweight charts)
├── config/                  # universe.yaml, strategies/, glossary.yaml, budgets — versioned data
├── fixtures/                # Golden sessions, hand-labeled profiles, indicator test vectors
└── deploy/                  # docker-compose, Caddy, Prometheus/Grafana, backup jobs
```

Build phases and exit criteria: [docs/14-roadmap.md](docs/14-roadmap.md). Phase 0 starts with `shared-schemas`, `services/ingestor`, and `deploy`.
