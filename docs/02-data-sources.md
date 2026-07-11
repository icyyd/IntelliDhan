# 02 — Data Sources & Integrations

## 1. Source Matrix

| Data need | Primary source | Fallback | Cadence | Notes |
|---|---|---|---|---|
| Index quotes (SPX, NDX, VIX, RUT) | Robinhood MCP (`get_index_quotes`) | Yahoo Finance | 5–15 s poll RTH | SPX/NDX values also derivable from SPY/QQQ ratio in a pinch (flagged as derived) |
| Equity/ETF quotes & bars | Robinhood MCP (`get_equity_quotes`, `get_equity_historicals`) | Yahoo Finance (yfinance) | quotes 5 s; 1m/5m bars on close | Yahoo used for extended history backfill |
| Option chains, quotes, greeks | Robinhood MCP (`get_option_chains`, `get_option_quotes`) | — | chains cached 5 min; alerted-strike quotes 5–10 s | Greeks + IV from RH; IV rank computed internally from stored IV history |
| Technical indicator cross-check | Robinhood MCP (`get_equity_technical_indicators`) + internal pipeline | TradingView (visual) | on demand | Internal pipeline is authoritative; RH values used as validation in CI/ops checks |
| Earnings calendar & results | Robinhood MCP (`get_earnings_calendar`, `get_earnings_results`) | Yahoo Finance | daily 6:00 ET + intraday refresh | Feeds RULE-L3 and event lockouts |
| Fundamentals (P/E, growth, margins) | Robinhood MCP (`get_equity_fundamentals`) | Yahoo Finance | daily | Powers HODL/LEAPS quality & PEG scores |
| Econ calendar (CPI, PPI, NFP, FOMC, GDP, claims, PCE) | FRED API + BLS/BEA release schedules | ForexFactory-style public calendar scrape | daily build + event-time capture | Consensus vs actual → surprise score |
| Macro series (rates, curve, DXY, oil, gold, crypto) | FRED + Yahoo Finance | — | daily; futures proxies intraday | For briefing + macro factor |
| News headlines | Yahoo Finance RSS + major-outlet RSS set | — | 5 min poll | Headline sentiment tagging (simple lexicon v1, LLM summarizer for briefing) |
| Fear/positioning | CBOE put/call ratio, VIX term structure (Yahoo), AAII (weekly) | — | daily | RULE-B2 contrarian overlay |
| Charting (visual) | TradingView Advanced Charts widget | Lightweight Charts | live | See §3 |

**Licensing note (doc 13):** Yahoo endpoints are unofficial; architecture keeps every source behind a `DataProvider` interface so paid feeds (Polygon, Tradier, dxFeed) can be dropped in without touching the engine. Accuracy-critical paths (option quotes for live alerts) run on Robinhood MCP, which is an authenticated, entitled feed.

## 2. Provider Abstraction

```python
class DataProvider(Protocol):
    async def get_bars(symbol, timeframe, start, end) -> list[Bar]
    async def get_quote(symbol) -> Quote
    async def get_option_chain(symbol, expiry_range) -> OptionChain
    async def get_option_quote(occ_symbol) -> OptionQuote
    capabilities: set[Capability]   # declares what it can serve
    health: ProviderHealth          # rolling error rate, staleness
```

- **Router** picks the healthiest provider per capability; automatic failover with a `source` stamp on every record (auditable accuracy).
- **Reconciliation job** (nightly): compares overlapping bars across providers; discrepancies > 0.1% logged and quarantined.

## 3. TradingView Integration

Two roles:

1. **Charting UI (inbound-visual):** Advanced Charts widget embedded in the web app for full-screen analysis; every alert deep-links to a chart pre-loaded with the alert's timeframe, levels (entry/stop/TP zones drawn), and indicator set. Alert cards use in-house Lightweight Charts renders for speed.
2. **Webhook signals (inbound-data, optional):** a TradingView webhook endpoint (`POST /webhooks/tradingview`, HMAC-signed) accepts alerts from user-authored Pine scripts. These enter the engine as an *external factor* — they can raise a setup's confidence or create a "community/manual signal" candidate, but they still pass the full confidence gate and liquidity checks before ever becoming an alert.

## 4. Robinhood MCP Agent

| Function | MCP tools | Use |
|---|---|---|
| Market data | `get_equity_quotes`, `get_index_quotes`, `get_option_chains`, `get_option_quotes`, `get_option_historicals` | Primary real-time feed |
| Account context | `get_accounts`, `get_portfolio`, `get_equity_positions`, `get_option_positions` | Capital-aware sizing; "you already hold X" conflict warnings on alerts |
| Performance truth | `get_pnl_trade_history`, `get_realized_pnl` | Trade log reconciliation: realized results of taken alerts |
| Discovery | `create_scan`, `run_scan`, watchlist tools | Server-side pre-screens for swing/HODL candidate universes |
| Order staging | `review_equity_order`, `review_option_order`, then `place_*` **only after in-app human confirm** | One-click ticket from an alert; never autonomous (doc 13 hard rule) |

## 5. Telegram Integration

- Dedicated bot; owner-locked chat.
- **Message types:** trade alert (with chart snapshot image), profit-milestone updates (mirrors the BABA-style +60%/+70% follow-ups: current price, % gain, "raise stop to X"), stop/TP-hit notices, daily briefing (8:30 ET), module cooldown notices, ops alerts (data degraded).
- **Inline buttons:** `✅ Took it` / `👀 Watching` / `❌ Pass` — writes disposition to the trade log (doc 10); `📈 Chart` deep-link to web app.
- Formatting spec lives in doc 09 §6.

## 6. Universe Definition (v1)

| Module | Universe |
|---|---|
| 0DTE | SPX, NDX (index options); SPY, QQQ, SMH; leveraged: TQQQ, SQQQ, SOXL, SOXS, SPXL, SPXS, UPRO |
| Swings | Top ~40 liquid large caps + above ETFs (AAPL, NVDA, MSFT, AMZN, META, GOOGL, TSLA, AMD, AVGO, NFLX, BABA, …) — config-editable watchlist |
| LEAPS | Swing universe + quality screen output; leveraged ETFs for Dynamic Collar (TQQQ primary) |
| HODL | S&P 500 + Nasdaq-100 constituents through the quality screener |

Universe files are config (`universe.yaml`), hot-reloadable, with per-symbol overrides (min liquidity, max spread, module opt-in/out).

## 7. Research, Screening & Event Sources

Curated external sources wired into specific engine inputs and UI surfaces. Each has a declared ingestion mode: **API** (programmatic), **feed** (RSS/scheduled scrape with ToS check), or **link-out** (deep link from the UI; no ingestion).

| Source | URL | Role in platform | Mode | Consumed by |
|---|---|---|---|---|
| **Finviz Screener** | finviz.com/screener.ashx | Candidate discovery: engine replicates key Finviz screens internally (rel-volume gainers, new highs, unusual volume, oversold quality) as the Swing/HODL pre-filter; UI offers "open in Finviz" with filters pre-encoded in the URL | feed (screen export) + link-out | Swing/HODL candidate pipeline; module screens |
| **Finviz Sector Heat Map** | finviz.com/map.ashx | Sector-relative performance context; engine computes its own sector RS heatmap from constituent data (so it's queryable), rendered Finviz-style on the homepage; link-out for the full map | internal replica + link-out | F4 breadth, briefing §3, homepage widget |
| **Dataroma** | dataroma.com | Superinvestor (Buffett/Ackman et al.) 13F holdings + insider clusters — the literal Investor Council portfolio tracker | feed (quarterly 13F refresh) | HODL screener overlay (§ doc 07), briefing notable-moves |
| **SEC EDGAR** | sec.gov/edgar | Ground truth for filings: 10-K/10-Q (fundamentals verification), 8-K (event catalysts), Form 4 (insider buys/sells), 13F (institutions), S-1 (IPOs) | API (EDGAR full-text + submissions API, free/official) | HODL quality screener, catalyst engine, insider factor |
| **Earnings Whispers** | earningswhispers.com | Earnings dates with **confirmed times** + whisper vs consensus EPS — more reliable timing than most calendars; whisper-beat is a documented PEAD amplifier | feed | RULE-L3 calendar, `POST_EARNINGS_DRIFT`, earnings blackouts |
| **OptionsAI Expected Move** | tools.optionsai.com/expected-move | Cross-check for internally computed (ATM-straddle) expected moves around earnings | link-out + validation feed | `EARNINGS_IV_CRUSH` gate, alert `expected_move` sanity check |
| **Benzinga Analyst Ratings** | benzinga.com/analyst-stock-ratings/upgrades | Daily upgrades/downgrades + PT changes | feed (morning pull 8:00 ET + intraday) | Catalyst factor F7, `ANALYST_UPGRADE_MOMENTUM` strategy, briefing §6 |
| **MarketWatch Econ Calendar** | marketwatch.com/economy-politics/calendar | Secondary econ-calendar source reconciled against FRED/BLS/BEA schedules (two-source agreement required for event-lockout timing) | feed | MacroContext, RULE-T12 lockouts, briefing §5 |
| **MarketBeat IPO Lockups** | marketbeat.com/ipos/lockup-expirations/ | Upcoming lockup expirations = supply-shock event risk (and occasional short catalyst) | feed (weekly) | Event-risk penalty in F7, `LOCKUP_SUPPLY_EVENT` strategy, briefing calendar |
| **Investopedia** | investopedia.com | Education layer: every technical/finance term in the UI carries a glossary tooltip whose "learn more" deep-links to the matching Investopedia article (incl. rights/warrants, SEC form types, Fed mechanics, reading earnings reports/balance sheets) | link-out | UI glossary system (doc 11), "How it works" pages |
| **HBS / Investopedia fundamentals guides** | (balance sheet / earnings-report articles) | Source material for the HODL screener's human-readable pillar explanations — each quality-pillar score links "what this measures & why" | link-out | HODL screener UI copy |

**Reconciliation rule:** externally scraped values (whispers, ratings, lockup dates) are advisory inputs — they can *raise or gate* scores but a missing/stale external feed never blocks core alerting (engine degrades gracefully to internal calculations, with the degradation noted on affected alerts).

**Fed & rates doctrine** (from the US News / Investopedia references): the MacroContext model encodes the standard transmission rules — rate expectations reprice growth/duration assets hardest (NDX > SPX sensitivity), financials benefit from steepening, and *expectation shifts* (dot plot, Fed-speak surprises) matter more than the decision itself. Implemented as: FOMC-cycle state (pre-blackout, blackout, decision week, digestion), fed-funds-futures-implied path deltas as a MacroContext input, and duration-sensitivity tags per symbol so rate shocks modulate F7 differently for TQQQ vs XLF vs XLE.
