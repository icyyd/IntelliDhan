# 13 — Risk, Guardrails & Compliance

## 1. Hard Guardrails (non-negotiable, enforced in code)

| # | Guardrail |
|---|---|
| G1 | **No autonomous execution.** The platform never places an order without a per-order, in-app human confirmation. Robinhood MCP write path is behind a settings toggle (default OFF) + per-order confirm modal showing full ticket and dollar risk. No batch approvals. |
| G2 | **No naked short options.** The strategy schema cannot represent undefined-risk short positions (doc 08 §3). |
| G3 | **Stops are mandatory.** Alert schema requires a stop; composer cannot publish without one (Discipline #2). |
| G4 | **Daily kill switch.** Module halts new alerts at 100% daily-budget loss; platform-wide halt at configured account-level daily loss (default 3% of account). Reset requires next session + explicit re-arm. |
| G5 | **Cooldowns.** 2 consecutive stops per module/day → 90-min cooldown (anti-revenge, Discipline #4). |
| G6 | **Budget integrity.** Sizing never exceeds module budget or live buying power; zero-contract fits suppress the alert rather than stretch risk. |
| G7 | **Data honesty.** Empty, stale, gapped, mismatched, timed-out, or otherwise degraded data quarantines the affected symbol. Existing plans are retired; intent creation, approval, and claim fail closed. A claimed intent is placement-revoked without destroying the late broker-receipt path, and executed/closed broker truth is immutable under later data failures. Healthy symbols may continue, but partial health is never reported as fully ready. |
| G8 | **Calibration honesty.** Realized-vs-claimed stats are always user-visible; strategies auto-demote on drift (doc 03 §4.3); sample sizes always shown. |
| G9 | **Event lockouts.** Tier-1 macro prints and open/close windows block new entries (RULE-T12). |
| G10 | **LOTTO quarantine.** Asymmetric lottery plays are hard-capped at 5% of 0DTE budget, loudly labeled, excluded from headline stats. |

## 2. Standing Disclaimer (rendered in-app footer, every Telegram alert, every briefing)

> IntelliDhan is an analysis and alerting tool for educational and informational purposes. It is not a registered investment adviser and nothing it produces is financial advice or a recommendation. Options involve substantial risk and are not suitable for everyone — 0DTE options can lose 100% of premium in minutes. Probabilities are model estimates, not guarantees. You are solely responsible for your trading decisions. Past (and backtested) performance does not predict future results.

Short form for alerts: `⚠️ Educational tool — not financial advice. Options risk 100% loss.`

## 3. Data & API Compliance

- Yahoo Finance endpoints are unofficial → non-critical paths only (backfill, macro proxies), provider-abstracted for paid replacement (Polygon/Tradier) before any multi-user distribution.
- TradingView widgets used per their attribution/embed terms; no scraping of TradingView data.
- Robinhood access via user's own authenticated MCP session; credentials never stored by IntelliDhan; scope-minimized (read by default).
- FRED/BLS/BEA are public-domain data; SEC EDGAR has a free official API (rate limits respected: ≤ 10 req/s, declared User-Agent). Attribution included on briefing page.
- Research feeds (Finviz, Dataroma, Earnings Whispers, Benzinga, MarketWatch, MarketBeat) are ingested only within each site's ToS — prefer official APIs/RSS where offered, conservative poll rates, link-out instead of ingestion where scraping is disallowed. All are advisory-tier inputs (doc 02 §7 reconciliation rule), so losing any one of them never breaks alerting.
- Single-user personal tool in v1; any future multi-user distribution triggers a legal review (signal services may implicate investment-advice regulations — flagged as an explicit gate in the roadmap).

## 4. Operational Risk

- Missed-alert risk: delivery is at-least-once with idempotent IDs; Telegram failure falls back to web push + banner; ops alert on delivery failure.
- Wrong-data risk: dual-provider reconciliation (doc 02 §2); indicator cross-validation in CI; replay determinism tests.
- Model-drift risk: weekly recalibration + auto-demotion + monthly human review checklist (generated as a task with the stats attached).
- Security: auth required (passkey/TOTP), secrets in keychain/env, Telegram chat-ID lock, no third-party analytics/trackers, backups encrypted at rest.

## 5. Known Honest Limitations (documented to the user, in-app "How it works" page)

- Confidence is calibrated on history; regime breaks degrade it before recalibration catches up (that's what the demotion system is for).
- 0DTE fills at suggested limits are not guaranteed in fast tape; entry zones + no-chase rules mitigate, not eliminate.
- Leveraged ETFs decay in chop; the engine surfaces decay cost but can't remove it.
- The macro/news layer summarizes public information; it has no private edge and events can gap through any stop.
