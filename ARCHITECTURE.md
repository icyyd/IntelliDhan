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

Build phases and exit criteria: [docs/14-roadmap.md](docs/14-roadmap.md).

## Development quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                    # unit tests (fast, offline)
.venv/bin/pytest -m integration     # Redis/TimescaleDB/Yahoo integration tests

# Infra (Docker via Colima on this machine):
colima start
docker-compose -f deploy/docker-compose.yml --env-file .env up -d
#   .env holds POSTGRES_PASSWORD (gitignored, generated locally — regenerate if missing:
#   python3 -c "import secrets; print('POSTGRES_PASSWORD='+secrets.token_urlsafe(24))" > .env)

# Gateway + dashboard (http://localhost:8321):
.venv/bin/uvicorn intellidhan_gateway.app:app --port 8321

# Research loop:
python -m intellidhan_learning.backtest --days 55 --shadow --fit-calibration  # harvest + fit
python -m intellidhan_learning.backtest --days 55                             # gated evaluation

# Optional live Telegram delivery: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.
```

**Phase 0 status: COMPLETE.** Implemented and verified end-to-end:
- Schemas (`Bar`/`Quote`/`OptionQuote`), market clock (sessions/holidays/half-days/DST), `DataProvider` protocol + Yahoo provider, session-aware DQ sentinel, incremental indicator engine (EMA/RSI/MACD/ATR/session-VWAP/rel-volume) — golden-tested at 1e-9 vs independent references.
- Event bus (Redis Streams + deterministic in-memory twin), TimescaleDB persistence (hypertables, idempotent upserts), backfill CLI (`python -m intellidhan_ingestor.backfill`), session recorder + replay harness.
- **Exit criterion met:** the committed golden session (`fixtures/golden-sessions/qqq-complex-5m.jsonl`, 1,248 real 5m bars × 4 symbols) replays bus→engine→snapshots deterministically (sha256-asserted), with the identical digest over real Redis and in-memory transports, and persists to TimescaleDB idempotently.

Next (Phase 1): MTF trend engine + level maps, factor framework, first Swing strategies, Codex + official Robinhood MCP bridge, Alert Composer + Telegram bot.
