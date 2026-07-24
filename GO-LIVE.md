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

## Codex broker cutover (separate change window)

Do not combine this procedure with an ordinary application deployment. The
intent bridge is implemented, but execution remains `OFF` and no strategy is
currently live-eligible.

1. Confirm the persisted auto-trade policy is `OFF` before deployment.
2. Create a new deployment secret named `AUTOTRADE_CODEX_AGENT_TOKEN`. Do not
   reuse the retired `AUTOTRADE_AGENT_TOKEN` value or expose the replacement to
   the retired runner. Remove the retired variable only after rollback review.
3. Deploy the reviewed commit from `main`, then verify `/api/liveness` and the
   authenticated intent-list endpoint. It must report contract `1.1` and
   effective mode `OFF`; the retired bearer must be rejected.
4. Authenticate Codex to Robinhood's official Trading MCP on the trusted host.
   Verify the runtime-advertised tools and the dedicated Robinhood Agentic
   account without placing an order. Other accounts remain read-only.
5. Exercise the full intent, claim, review, receipt, and reconciliation loop in
   IntelliDhan `SHADOW` mode and review the resulting audit evidence.
6. Use `SUPERVISED` only after explicit approval for each intent. Enabling
   unattended `ARMED` mode is a separate decision that requires explicit user
   instruction after reviewing the policy, account, current MCP tools, risk
   limits, strategy qualification, and shadow results.

The authoritative execution loop and rollback rules are in
[docs/27-codex-robinhood-execution.md](docs/27-codex-robinhood-execution.md).

## Every trading morning (one command, before 8:25 AM ET)

```bash
cd ~/Documents/IntelliDhan && ./scripts/start_live.sh
```

- 8:30 ET: daily briefing arrives (Telegram + `/api/briefing`).
- 9:30–16:00 ET: engine polls every minute; alerts + stop/TP follow-ups deliver as they occur.
- Dashboard: http://localhost:8321 (trend matrix, alerts, suppression tape, paper P&L).
- Laptop lid must stay open (caffeinate keeps it awake, not un-slept).

## What to expect — read this part

- **Zero strategies are currently live-eligible. Expect NO trade alerts
  tomorrow, by design.** A 2026-07-12 pre-launch review (see
  [docs/18-enhancement-review.md](docs/18-enhancement-review.md) for the full
  audit) found that `PULLBACK_CONTINUATION` — the one strategy that had
  cleared the 75% research bar — had two real bugs in how the *production*
  engine expressed that research:
  1. **Trigger-bar identity bug**: the strategy was reading touch/entry
     prices from a 5-minute price slice instead of the true completed
     1-hour bar the research was validated on. Fixed and regression-tested.
  2. **Risk-state wiring gap**: the concurrency, correlation, and duplicate-
     signal caps were specified in the design docs but never actually wired
     to fire — so correlated instruments (e.g. QQQ and its 3x-leveraged
     sibling TQQQ) could both alert on the same underlying move, inflating
     apparent signal count. Fixed and regression-tested.
  3. **After both fixes**, forward-style checks on the trailing 55 days
     still showed a weak, inconclusive result on a very small sample. A
     parallel, more rigorous statistical analysis (bootstrap confidence
     intervals, a separate out-of-universe confirmation test, predeclared
     eligibility criteria) concluded the direct-H1 research population and
     the production 5m-rollup population do not yet demonstrably agree, and
     recommended blocking the strategy from live delivery until that parity
     is proven. **I implemented that recommendation tonight**:
     `PULLBACK_CONTINUATION` is now structurally incapable of producing a
     gated/live alert (verified by both a unit test and a real 55-day
     backtest showing zero alerts) — it still runs in the background,
     harvesting forward-paper evidence, but it cannot reach you as a trade
     suggestion until it's re-qualified.
- **This means: run the platform tomorrow to watch it work — trend matrix,
  briefing, suppression tape, calibration — but do not expect or wait for a
  trade alert.** The "why we're quiet" tape will show `disabled` as the gate
  reason for any PULLBACK_CONTINUATION setups the engine still evaluates.
  This is the system being honest rather than shipping a number it can't
  yet stand behind.
- Alerts (whenever a strategy re-qualifies) are **decision support**: entry
  limit + no-chase zone, stop, tranche targets, size from your budget. Manual
  execution remains the default. A guarded Codex-to-official-Robinhood-MCP
  intent bridge exists, but it remains `OFF`, is not a shortcut around strategy
  qualification, and cannot be activated by an ordinary deployment.
- Not yet live (know the gaps): econ-calendar event lockouts (CPI/FOMC
  prints), options-contract suggestions on alerts (equity sizing only until
  the chain feed lands), 0DTE strategies (blocked pending calibration-grade
  intraday history), execution-cost modeling (spread/slippage/fees), full
  research/production parity for `PULLBACK_CONTINUATION`, and the broader
  risk-ledger/data-quality wiring the review flags as still open.

⚠️ Educational tool — not financial advice. Options and equities involve substantial
risk of loss. Probabilities are model estimates from historical data, carry wide
uncertainty on small samples, and are not guarantees of future performance.
