# Smart-play scanner and evidence-grounded AI thesis

**Status:** implemented on the stacked `codex/ai-smart-play-scanner` branch

**Promotion status:** research only; never execution eligible

**Agent contract:** this file is the source of truth for scanner intent, evidence boundaries, and follow-up work.

## 1. Product outcome

Discover is a decision queue for signal-oriented stock research. Each candidate must answer:

1. What setup is present?
2. Why did it rank within the configured universe?
3. What confirms the setup?
4. What invalidates it?
5. What still needs research?

The deterministic scanner owns market measurements and ranking. OpenAI may synthesize a thesis from those facts and optionally retrieve current public context, but it cannot change rank, create an order, arm auto-trade, or invent a market value.

```text
settled adjusted daily bars
        │
        ├── data-quality gate
        │
        ├── per-symbol measurements
        │     12–1 / 6–1 momentum, SMA slope, 55-day level,
        │     volume participation, volatility, ATR, liquidity
        │
        ├── cross-universe percentile ranks
        │
        ├── fixed smart-play families
        │     momentum leader / breakout watch / trend pullback
        │
        ├── card-first Discover queue
        │
        └── optional OpenAI evidence selection
              research only; no execution path
```

## 2. Fixed strategy families

All three families require a price of at least $5 and 20-day average dollar
volume of at least $5 million before a name can become an eligible play.

### 2.1 Momentum leader

Eligibility:

- positive 12–1 and 6–1 month returns;
- price above a rising 200-day moving average;
- ranking combines configured-universe 12–1 momentum (40%), 6–1 momentum (25%), 52-week-high proximity (20%), and inverse 20-day volatility rank (15%).

Skipping the most recent month reduces reliance on very short-term reversal. The cross-sectional momentum premise is based on Jegadeesh and Titman's fixed 3–12 month winner/loser evidence. Their result is evidence of a historical effect, not proof of future profitability. See [The Journal of Finance paper](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1993.tb04702.x).

### 2.2 Breakout watch

Eligibility:

- price is between 4% below and 3% above the prior 55-day high;
- price is above a rising 200-day average;
- the score combines breakout proximity, recent-versus-prior volume, volatility contraction, and 6–1 momentum rank.

The card says `WAIT FOR CONFIRMATION` until price closes above the prior 55-day high with recent volume at least 1.2x its prior average. Proximity alone is not called a breakout. A 55/20 trend rule already has a fixed-rule, next-period backtest in the on-demand analysis module.

### 2.3 Trend pullback

Eligibility:

- positive 12–1 momentum and a rising 200-day average;
- price remains above the 200-day average;
- price is 3–18% below its 52-week high and within 6% of its 50-day average.

This is explicitly `WAIT FOR TURN`. It requires a reclaim of the prior 20-day high and is invalid below the 200-day average or prior 20-day low. It must not be represented as catching a bottom.

### 2.4 Why the 52-week high remains context

George and Hwang found that proximity to the 52-week high helped explain momentum profits and improved return forecasting in their sample. IntelliDhan uses proximity as one transparent input, not a standalone trade trigger. See [The Journal of Finance paper](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2004.00695.x).

Time-series trend evidence across liquid futures also supports retaining a simple trend regime rather than adding a high-dimensional predictor. See [Moskowitz, Ooi, and Pedersen](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf). That paper is cross-asset evidence and does not validate every individual equity.

## 3. Backtest result and interpretation

Command:

```bash
.venv/bin/python -m intellidhan_learning.research_smart_scan \
  --years 5 --cost-bps 10 --top-n 3
```

Data fetched on 2026-07-13: 12 current configured symbols, common adjusted-daily history from 2021-07-12 through 2026-07-13. The evaluation window is fixed before observing any selection and runs from the first scheduled execution on 2022-07-13 through 2026-07-13 (1,003 sessions), including 210 initial cash sessions. The fixed strategy rebalances every 21 sessions, chooses at most three eligible momentum leaders, charges 10 bps per one-way turnover, forms signals at a completed close, executes at the next close, and begins applying returns on the following session. The SPY benchmark is also entered at that execution close.

| Result | Smart momentum | SPY benchmark |
|---|---:|---:|
| Total return | 174.53% | 108.62% |
| CAGR | 28.88% | 20.29% |
| Annual volatility | 31.15% | 16.38% |
| Sharpe | 0.970 | 1.210 |
| Maximum drawdown | -38.95% | -18.76% |

The exact ten-year command is not published as a result because repeated Yahoo adjusted-history requests intermittently fail strict OHLC validation on older corporate-action rows. The harness fails closed rather than repairing or dropping those bars. A reproducible ten-year result requires a validated adjusted-data source or a reviewed tolerance contract first.

Interpretation:

- The return spread confirms **improvement potential worth continued research**.
- It does not confirm a superior risk-adjusted strategy: SPY had the better Sharpe and roughly half the maximum drawdown.
- The result is survivorship-biased, technology-heavy, includes leveraged TQQQ, and has only a current 12-symbol universe. It is not a promotion backtest and must never be presented as expected profit.
- The harness applies the $5 price floor but accepts closes only, so it cannot reproduce the live $5 million point-in-time dollar-volume gate. The default configured symbols are liquid today; this is still a parity limitation for custom `--symbols` runs.

Required before promotion:

1. point-in-time Russell 1000 or S&P 1500 membership with delisted securities;
2. at least three calendar walk-forward folds, including 2022 and a momentum-reversal period;
3. comparisons against SPY, equal-weight universe, and a simple 200-day regime;
4. cost sensitivity at 10/25/50 bps and turnover/market-impact review;
5. sector, size, liquidity, and leveraged-product subgroup results;
6. a minimum 30-day shadow/paper period with no parameter changes.

## 4. OpenAI thesis contract

Endpoint:

```text
POST /api/discover/thesis
{
  "symbol": "AAPL",
  "play_key": "MOMENTUM_LEADER",
  "web_research": false
}
```

The endpoint is account-only and rate-limited. It re-reads the server candidate rather than trusting client-supplied market facts and refuses to run when any configured-universe symbol failed, because a partial universe would make percentile ranks misleading. Configuration:

```text
OPENAI_API_KEY=...
OPENAI_SMART_SCAN_MODEL=gpt-5.6-luna
```

The model name is configurable because current model availability changes. The implementation uses the Responses API, low reasoning effort, `store: false`, a privacy-preserving safety identifier, and a strict JSON schema. OpenAI's current guidance recommends the Responses API for reasoning/tool workflows; Structured Outputs is used to constrain the response shape. See the official [latest model guide](https://developers.openai.com/api/docs/guides/latest-model) and [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

The model cannot return user-visible prose. Its closed output contains only a verdict, assessment, confidence, supplied evidence IDs, and fixed research-focus enums. The server validates every field and then renders the headline, thesis, evidence facts, risks, timing, confirmation, and invalidation from deterministic server data. A malicious or malformed provider response with extra prose, an unknown evidence ID, or an unsupported enum fails closed. The server also downgrades `RESEARCH` to `WATCH` while deterministic confirmation is pending, forces a weak assessment to `AVOID`, and caps model-selected confidence at `MEDIUM` while fundamental/event evidence is unavailable.

`web_research: true` is intentionally rejected. The hosted `web_search` tool will remain disabled until the UI preserves OpenAI annotation positions and renders claim-level inline citations that are both visible and clickable. See the official [web search guide](https://developers.openai.com/api/docs/guides/tools-web-search).

Allowed verdicts are `RESEARCH`, `WATCH`, and `AVOID`; there is no `BUY`. The model must:

- select only supplied evidence IDs;
- choose only fixed research-focus enums;
- never return narrative, numeric facts, targets, recommendations, or instructions;
- leave all displayed facts and prose to deterministic server rendering.

If the key/provider/model is unavailable or output violates the evidence contract, the endpoint fails with an explicit message and leaves the deterministic candidate unchanged.

## 5. UI contract

Each Discover card shows, in order:

1. setup family, ticker, horizon, and play score;
2. plain-language status (`READY TO RESEARCH`, `WAIT FOR CONFIRMATION`, or `WAIT FOR TURN`);
3. three evidence facts;
4. confirmation and invalidation rules;
5. compact supporting market metrics;
6. actions: full analysis, immediate in-app AI thesis, optional ChatGPT Workspace
   Agent dispatch, and watchlist.

The layout stays card- and alert-centric. Status always includes text and never relies on color alone. AI content expands inside the originating card and uses an `aria-live` status region.
Workspace Agent dispatch is a separate fire-and-forget research path whose
output cannot currently return through OpenAI's trigger API; see
[ChatGPT Workspace Agent dispatch](23-chatgpt-workspace-agent-dispatch.md).

## 6. Strategies intentionally not activated

Gross profitability combined with value has strong published evidence; see [Novy-Marx's NBER paper](https://www.nber.org/papers/w15940). Earnings revisions and post-earnings drift are also attractive research directions. They are not implemented as scores because the platform lacks point-in-time fundamentals, estimates, earnings timestamps, restatement history, and survivorship-safe coverage. Adding them to a backtest using today's fundamentals would create lookahead bias.

The next strategy expansion should therefore be only:

1. **quality at a reasonable price:** gross-profit-to-assets plus valuation, using filing-effective dates;
2. **earnings revision drift:** estimate direction, surprise, and post-event price confirmation, using timestamped revisions and earnings releases.

Do not add social sentiment, opaque ML price targets, per-ticker parameter tuning, or dozens of correlated indicators before those data contracts exist.

## 7. Code map

- `services/analytics/intellidhan_analytics/smart_scan.py` — pure cross-sectional ranking and setup rules.
- `services/gateway/intellidhan_gateway/discovery.py` — completed-bar measurements, provider/data-quality gate, presets, and server scan.
- `services/learning/intellidhan_learning/research_smart_scan.py` — fixed walk-forward diagnostic.
- `services/gateway/intellidhan_gateway/ai_thesis.py` — optional Responses API synthesis and citation extraction.
- `services/gateway/intellidhan_gateway/app.py` — account-only thesis endpoint.
- `web/index.html` — card-first decision queue and on-demand thesis rendering.
- `tests/test_smart_scan.py` — ranking, confirmation, lookahead, structured-output, and fail-safe contracts.
