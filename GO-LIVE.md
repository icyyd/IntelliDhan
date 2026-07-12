# Go-Live Checklist

## One-time setup (you, ~10 minutes)

1. **Telegram** (skip → alerts print to the server console instead):
   - Message **@BotFather** on Telegram → `/newbot` → name it (e.g. `IntelliDhanBot`) → copy the token.
   - Send any message to your new bot, then open
     `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy
     `"chat":{"id": ...}` — that number is your chat ID.
   - Append both to `.env` in the repo root:
     ```
     TELEGRAM_BOT_TOKEN=123456:ABC-...
     TELEGRAM_CHAT_ID=123456789
     ```
2. **Budgets** — edit [config/budgets.yaml](config/budgets.yaml). The numbers there are
   PLACEHOLDERS ($4k 0DTE / $12k swing / …). Every alert's share/contract count and
   dollar risk derives from these. Set them to what you actually allocate.
3. Confirm `.env` still has `POSTGRES_PASSWORD` (generated at setup).

## Every trading morning (one command, before 8:25 AM ET)

```bash
cd ~/Documents/IntelliDhan && ./scripts/start_live.sh
```

- 8:30 ET: daily briefing arrives (Telegram + `/api/briefing`).
- 9:30–16:00 ET: engine polls every minute; alerts + stop/TP follow-ups deliver as they occur.
- Dashboard: http://localhost:8321 (trend matrix, alerts, suppression tape, paper P&L).
- Laptop lid must stay open (caffeinate keeps it awake, not un-slept).

## What to expect — read this part

- **Only PULLBACK_CONTINUATION is above the 75% gate** (held-out 76.5% TP1-win rate,
  ~1–3 alerts/week across 12 symbols, long-only swing). Every other strategy is being
  measured and is correctly silent. **A day with zero alerts is the system working.**
- Alerts are **decision support**: entry limit + no-chase zone, stop, tranche targets,
  size from your budget. Execution is manual, by you, in your broker. There is no
  auto-trading, by design (guardrail G1).
- **Recommended: treat week 1 as parallel running** — watch alerts against the paper
  track before committing capital. The trade log is measuring claimed-vs-realized
  from day one.
- Not yet live (know the gaps): econ-calendar event lockouts (CPI/FOMC prints),
  options-contract suggestions on alerts (equity sizing only until the chain feed
  lands), 0DTE strategies (blocked pending calibration-grade intraday history).

⚠️ Educational tool — not financial advice. Options and equities involve substantial
risk of loss. Probabilities are model estimates from historical data.
