# 07 — Module: Buy & Hold (HODL)

The Investor Council's home turf: own wonderful businesses, add during fear, sell almost never. Equity-first (options appear only as acquisition/yield tools borrowed from other modules: CSP wheel entries, collars on concentrated positions).

## 1. Philosophy Mapping

- RULE-B3/B4 (quality first, hold forever), RULE-L1/L2/L4 (know what you own, GARP, categorize), RULE-M4 (circle of competence via explicit universe), RULE-B2 (accumulate during fear), RULE-C1 (margin of safety on entry).

## 2. Quality Screener (the gate everything passes)

Scored 0–100 across four pillars (S&P 500 + NDX universe, refreshed weekly from fundamentals feed):

| Pillar | Inputs | Pass bar |
|---|---|---|
| **Moat & Returns** | ROIC > 15% (5y median), gross-margin stability, FCF margin, market-share proxy | ≥ 70 |
| **Growth (Lynch)** | Revenue & EPS growth, PEG < 2 (hard cap, RULE-L2), TAM narrative tag | ≥ 60 |
| **Balance Sheet** | Net debt/EBITDA < 2.5, interest coverage > 8, dilution rate | ≥ 65 |
| **Capital Allocation** | Buybacks at reasonable multiples, dividend growth streak, M&A discipline flags | ≥ 55 |

Output: **HODL-approved list** (~50–80 names) with Lynch category tags (stalwart / fast grower / cyclical / turnaround / asset play). Cyclicals & turnarounds get modified valuation logic (RULE-L4).

**Filings & smart-money overlay (EDGAR + Dataroma):**
- Fundamentals are verified against primary filings (10-K/10-Q via EDGAR API), not just aggregator data; a mismatch flags the name "data under review".
- 8-K stream per approved name → material-event cards (CEO change, guidance, M&A) that can trigger an off-cycle re-score.
- **Corporate actions handler:** rights offerings, warrants, spin-offs, and splits on held names generate an explainer card (what it is, dilution math, deadline dates, choices with pros/cons — grounded in the Investopedia rights-and-warrants guidance) rather than a silent position change.
- **Form 4 insider signal:** cluster buying (≥ 3 insiders, 90 days) adds up to +5 to the pillar-composite; sustained heavy selling caps Capital Allocation pillar.
- **Superinvestor signal (Dataroma):** each name shows which Council-grade managers hold it and quarterly adds/trims; a new position from ≥ 2 tracked superinvestors while the name sits in Attractive zone emits a `HODL_SMART_MONEY_CONFLUENCE` research card. 13F data is 45+ days stale by nature — used as *conviction confirmation, never timing* (the technical entry triggers still govern timing).
- Screener pillar UIs link "what this measures & why" explainers built from the HBS/Investopedia balance-sheet and earnings-report guides, with per-term Investopedia deep links.

## 3. Valuation & Entry Timing

**Fair-value band** per name: blended forward P/E vs 5y range, EV/EBITDA vs sector, PEG, and simple DCF band (config assumptions, shown transparently). Produces zones: `Expensive / Fair / Attractive / Table-pounding`.

**Accumulation alerts** fire only when quality ∧ valuation ∧ technical timing align:
| Strategy key | Trigger |
|---|---|
| `HODL_ACCUMULATE` | Price enters Attractive zone + W/M support confluence + weekly stabilization (2 W closes reclaiming 9 EMA) |
| `HODL_FEAR_BUY` | Market-wide fear (VIX percentile > 80 or index −15%+) + name in Fair-or-better zone → tranche ladder like LEAPS drawdown ladder (RULE-B2) |
| `HODL_CSP_ENTRY` | Delegates to Swing `CSP_WHEEL_ENTRY`: sell cash-secured puts at the Attractive-zone strike — get paid to place the bid |
| `HODL_NEW_APPROVAL` | Name newly passes quality screen while in Fair zone → research card (not a buy alert) |

**Sell/trim alerts** (rare by design, RULE-B4): thesis break (quality score drops > 20 pts or pillar failure two quarters), Table-pounding-inverse overvaluation (> 95th percentile of its own 10y valuation range) → trim advisory only, position > 25% of HODL portfolio → concentration trim, tax-lot aware via RH `get_equity_tax_lots`.

## 4. Position & Portfolio View

- Target-weight builder from user's HODL budget; DCA scheduler ("accumulate $X weekly into approved-list names currently Attractive") — suggestions only.
- Portfolio dashboard: weights vs targets, quality-score drift, valuation-zone map (scatter: quality × valuation, quadrant-colored), dividend calendar, "fear fund" cash tracker.
- Robinhood positions imported read-only; alerts flag conflicts ("adding would exceed 25% concentration").

## 5. Cadence

Weekly screen refresh (Sunday), quarterly deep re-score post-earnings (RULE-L3), event-driven re-score on guidance cuts/major news. HODL alerts are inherently low-frequency: expected 2–6/month total. The module UI leads with *portfolio health*, not alert volume.

## 6. Success Metrics

Approved-list rolling 3y total return vs SPY; accumulate-alert forward 12m hit rate (> SPY); realized max drawdown vs SPY; % of buys later stopped by thesis-break (target < 15%).
