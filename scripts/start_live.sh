#!/bin/zsh
# IntelliDhan go-live launcher. Run from repo root before 8:25 AM ET.
# Keeps the Mac awake through the close (caffeinate) and starts the gateway.
set -e
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "ERROR: .env missing. Create it with POSTGRES_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID."
  exit 1
fi
grep -q TELEGRAM_BOT_TOKEN .env || echo "WARN: TELEGRAM_BOT_TOKEN not in .env — alerts print to console only."

echo "[1/3] Docker (optional persistence layer)..."
colima start 2>/dev/null || true
docker-compose -f deploy/docker-compose.yml --env-file .env up -d 2>/dev/null || \
  echo "  (docker unavailable — live loop runs fine without it)"

echo "[2/3] Tests (fast sanity)..."
.venv/bin/pytest -q >/dev/null && echo "  tests green"

echo "[3/3] Gateway + live loop on http://localhost:8321 (Ctrl-C to stop)"
echo "  Briefing at 8:30 ET · alerts during RTH · machine stays awake"
exec caffeinate -is .venv/bin/uvicorn intellidhan_gateway.app:app --port 8321
