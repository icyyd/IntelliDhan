# IntelliDhan gateway — single-container deploy (Koyeb-compatible, doc 01 §6).
#
# Ships the FastAPI gateway (REST+WS+dashboard) only. It runs standalone —
# Redis/TimescaleDB are optional persistence/backfill infra (deploy/docker-
# compose.yml) that the live loop does not require to serve alerts, so a
# single Nano instance is sufficient, matching the prior IntelliDhan-legacy
# Koyeb config (single-instance by design: in-memory orchestrator state
# cannot be sharded across replicas — keep scalings.max = 1).
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY shared-schemas ./shared-schemas
COPY services ./services
COPY web ./web
COPY config ./config

# Editable install: keeps `Path(__file__).resolve().parents[3] / "web"` in
# services/gateway/intellidhan_gateway/app.py resolving correctly (it depends
# on the source tree living at this path, not a site-packages copy).
RUN pip install --no-cache-dir -e .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn intellidhan_gateway.app:app --host 0.0.0.0 --port ${PORT}"]
