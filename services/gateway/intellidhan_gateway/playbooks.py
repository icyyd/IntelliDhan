"""Beginner-facing research explanations, never strategy or execution policy.

The catalog does not evaluate a ticker, consume a quote, infer a current signal,
or promote calibration. Holding-period targets are distinct from option expiry.
Changing this copy cannot change the engine, sizing, or broker permissions.
"""

from __future__ import annotations

from intellidhan_schemas.option_policy import OPTION_HORIZONS
from intellidhan_schemas.signals import Module


CATALOG_VERSION = "research-playbooks-v1"


def _horizon(
    module: Module, label: str, holding_label: str, *, min_sessions: int | None,
    max_sessions: int | None, status: str, current_behavior: str,
) -> dict:
    expiry = OPTION_HORIZONS[module]
    return {
        "id": module.value,
        "label": label,
        "holding_period": {
            "label": holding_label,
            "min_sessions": min_sessions,
            "max_sessions": max_sessions,
            "status": status,
            "unit": "TRADING_SESSIONS",
        },
        "option_expiry": {
            "min_dte": expiry.min_dte,
            "max_dte": expiry.max_dte,
            "preferred_min_dte": expiry.preferred_min_dte,
            "preferred_max_dte": expiry.preferred_max_dte,
            "unit": "CALENDAR_DAYS",
            "status": "RESEARCH_SELECTION_WINDOW",
        },
        "current_behavior": current_behavior,
        "execution_authorized": False,
    }


def build_playbook_catalog() -> dict:
    """Return a fresh, JSON-ready educational catalog with no execution authority."""
    return {
        "version": CATALOG_VERSION,
        "status": "RESEARCH_ONLY",
        "execution_authorized": False,
        "guidance": [
            "These are research playbooks, not current signals or proven profitable strategies.",
            "A bullish or bearish trend is context, not permission to trade. Missing or stale "
            "evidence means wait; use completed candles and the separate data-health checks.",
            "Days held and days until an option expires are different. A 2–5-session swing "
            "does not require a 2–5-day option.",
            "A target holding period never requires staying in a losing or invalidated trade. "
            "Stops, thesis failure, and risk limits can require an earlier exit.",
            "Win rate alone is insufficient. Validation needs realistic costs, later unseen "
            "data, adequate samples, drawdown checks, and separate option-price evidence.",
        ],
        "action_language": {
            "BUY": "Bullish research case to review; not an order or an entry-ready signal.",
            "SELL": "Bearish or risk-reduction research case; not permission to open a short.",
            "HOLD": "A neutral research view with no clear new opportunity. It is not an "
            "instruction to retain an existing position; your holdings have not been assessed.",
            "WAIT": "No qualified action, insufficient evidence, or conflicting information.",
        },
        "horizons": [
            _horizon(
                Module.ZDTE, "Intraday", "Same trading session; no planned overnight hold",
                min_sessions=0, max_sessions=1, status="SAME_SESSION_EXIT_REQUIRED",
                current_behavior="The 9EMA scalp uses completed 5-minute candles with "
                "15-minute, hourly, and daily context. Paper outcomes use the exchange-session "
                "cutoff; option Simulation requires observed quotes and the broker sellout "
                "deadline. A next-day option is not permission to hold overnight.",
            ),
            _horizon(
                Module.SWING, "Swing", "Target 2–5 trading sessions; not yet enforced or validated",
                min_sessions=2, max_sessions=5, status="REQUESTED_NOT_ENFORCED_OR_VALIDATED",
                current_behavior="Existing swing research uses hourly pullbacks and daily "
                "context. Filled paper positions currently exit on price rules, not a "
                "2–5-session deadline. Alert entry validity is not a maximum holding period.",
            ),
            _horizon(
                Module.LEAPS, "Long-term / LEAPS", "Months; exact holding policy not implemented",
                min_sessions=None, max_sessions=None, status="PROPOSED_NOT_IMPLEMENTED",
                current_behavior="Long-dated option research has an expiry filter, but there "
                "is no registered LEAPS entry strategy or automated holding policy. Expiry "
                "one year away is not a one-year forecast or a guaranteed holding period.",
            ),
        ],
        "playbooks": [
            {
                "id": "ema9-mtf-scalp",
                "horizon_id": Module.ZDTE.value,
                "name": "9EMA trend pullback",
                "summary": "Watch SPY or QQQ resume an established intraday trend after a pullback.",
                "setup": "A completed 5-minute candle reclaims the 9-period exponential moving "
                "average in the trend direction. The existing strategy also checks 15-minute "
                "and hourly alignment, daily context, volume, VWAP, momentum, and entry time.",
                "why_it_may_help": "Several timeframes agreeing can filter isolated crosses; "
                "that rationale is a hypothesis, not evidence of profitability.",
                "when_to_avoid": [
                    "Repeated crosses in a flat or choppy market, or opposing higher timeframes.",
                    "Stale bars, poor option liquidity, wide spreads, or insufficient capital.",
                    "An entry outside the setup window or too close to the sellout deadline.",
                ],
                "exit_review": "The option lifecycle monitors a completed 5-minute close "
                "against the 9EMA, opposing 15-minute trend, hard invalidation, stale data, "
                "and the sellout deadline. Protection is not a guaranteed fill or loss limit.",
                "implementation_status": "SIMULATION_GATED",
                "validation_status": "NOT_LIVE_ELIGIBLE",
                "evidence_note": "The multi-timeframe strategy remains live_eligible=false. "
                "Results from a simple EMA crossover are not validation of this strategy.",
                "gaps": [
                    "Adequate independent out-of-sample and forward Simulation evidence.",
                    "Historical option bid/ask replay and realistic entry/exit costs.",
                ],
                "execution_authorized": False,
            },
            {
                "id": "opening-range-reversal",
                "horizon_id": Module.ZDTE.value,
                "name": "Opening-range reversal",
                "summary": "Study a failed opening push rather than chase it.",
                "setup": "Mark the first 15-minute high and low. The control research profile "
                "requires that range to reach 20% of the prior completed daily ATR, then a "
                "full opposite-direction 5-minute candle and a break on the next candle. "
                "This is separate from the engine's opening-range breakout strategy.",
                "why_it_may_help": "It makes a failed early move measurable. The price pattern "
                "does not prove that institutions manipulated the market.",
                "when_to_avoid": [
                    "An incomplete opening range, missing daily ATR, or no next-candle break.",
                    "Treating a research profile or a short-window winning run as a live signal.",
                ],
                "exit_review": "The underlying-only test uses a stop outside the reversal "
                "candle, a partial at the range boundary, a runner target, and a session exit. "
                "These are test assumptions, not observed option fills.",
                "implementation_status": "RESEARCH_BACKTEST_ONLY",
                "validation_status": "LATER_SAMPLE_DID_NOT_CONFIRM_EDGE",
                "evidence_note": "The September frozen-parameter check was negative after "
                "modeled costs for both ORR profiles. Neither is wired into live delivery.",
                "gaps": [
                    "Longer point-in-time intraday history and independent later validation.",
                    "Production-engine parity, option-price replay, and forward monitoring.",
                ],
                "execution_authorized": False,
            },
            {
                "id": "swing-trend-pullback",
                "horizon_id": Module.SWING.value,
                "name": "Swing trend pullback",
                "summary": "Study a pullback in an upward daily trend before a possible recovery.",
                "setup": "The existing long-only rule combines an upward daily trend with an "
                "hourly pullback toward the 21/50-period moving averages and renewed momentum. "
                "The MACD-confirmed variant is a separate research population.",
                "why_it_may_help": "The daily trend provides direction while the hourly "
                "pullback defines a closer invalidation level. This does not establish an edge.",
                "when_to_avoid": [
                    "A broken daily trend, incomplete bars, or an unclear invalidation level.",
                    "Unreviewed earnings or macro-event exposure and unaffordable overnight gaps.",
                ],
                "exit_review": "Review stops and thesis failure first. A maximum five-session "
                "exit, calendar-aware deadline, and restart persistence must be implemented "
                "and tested before this can be called a 2–5-session system.",
                "implementation_status": "RESEARCH_ENGINE_PARTIAL",
                "validation_status": "NOT_VALIDATED_FOR_REQUESTED_HOLD",
                "evidence_note": "Historical first-target hit rates are not net profitable "
                "trade probabilities. The baseline and MACD variant remain blocked from Live; "
                "the requested 2–5-session policy has not been tested.",
                "gaps": [
                    "Enforced session-count holding limits and earnings-event rules.",
                    "Research/production bar-construction parity and gap-aware execution.",
                    "Independent validation after fees, spreads, slippage, and option decay.",
                ],
                "execution_authorized": False,
            },
            {
                "id": "leaps-quality-trend",
                "horizon_id": Module.LEAPS.value,
                "name": "Business quality + long-term trend",
                "summary": "Build a long-term research case before considering a long-dated option.",
                "setup": "Proposed: combine filed financial strength, valuation, earnings "
                "risks, and a completed weekly price trend. Separate the business thesis "
                "from the option's price, liquidity, expiry, and downside.",
                "why_it_may_help": "Business evidence and price behavior answer different "
                "questions; agreement may support research, not a guaranteed return.",
                "when_to_avoid": [
                    "Missing or stale filings, unsupported growth claims, or poor option liquidity.",
                    "Using current fundamentals in a historical test without dated archives.",
                ],
                "exit_review": "Proposed: review after earnings and material thesis changes; "
                "define price invalidation and an option review/exit horizon before entry. "
                "A falling premium is not a reason to average down.",
                "implementation_status": "PROPOSED_NOT_IMPLEMENTED",
                "validation_status": "UNVALIDATED",
                "evidence_note": "An option-expiry research filter exists. A LEAPS strategy, "
                "weekly confirmation rule, and holding/exit lifecycle are not implemented.",
                "gaps": [
                    "Point-in-time filings and estimates, dated option quotes, and weekly rules.",
                    "A separately specified and validated entry, sizing, review, and exit policy.",
                ],
                "execution_authorized": False,
            },
            {
                "id": "defined-risk-credit-spread-concept",
                "horizon_id": Module.ZDTE.value,
                "name": "Defined-risk credit spread — learning only",
                "summary": "A course concept to evaluate, not a supported trade type.",
                "setup": "The reviewed course material discusses a same-expiry short option "
                "paired with a protective long option. The main entry lesson was not "
                "transcribed, so no exact entry rules are claimed here.",
                "why_it_may_help": "The protective leg defines the payoff risk of an intact "
                "spread. That is not evidence of a profitable strategy or guaranteed execution.",
                "when_to_avoid": [
                    "Any attempt to use the current long-option executor for a short spread.",
                    "Unreviewed assignment, expiration, settlement, liquidity, or multi-leg fill risk.",
                ],
                "exit_review": "The reviewed simplified course rule is to close when the "
                "underlying touches the short strike, without averaging down or rolling. "
                "A touch is not a guaranteed fill, and costs or gaps can worsen the result.",
                "implementation_status": "UNSUPPORTED",
                "validation_status": "UNVALIDATED",
                "evidence_note": "No executable entry rules, spread backtest, broker support, "
                "or live eligibility is established. Current automation permits single long "
                "calls or puts only, not short-opening or multi-leg spread orders.",
                "gaps": [
                    "An explicit, reproducible rule set and point-in-time option-spread history.",
                    "Separate broker capability, collateral, assignment, and multi-leg controls.",
                ],
                "execution_authorized": False,
            },
        ],
    }
