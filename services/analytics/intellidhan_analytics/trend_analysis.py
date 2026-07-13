"""Simple, auditable daily trend analysis for arbitrary tickers.

The module intentionally uses a small set of parameter-stable methods with
published evidence: a 200-day regime filter, 12-month time-series momentum,
a 55/20 trading-range breakout, and 52-week-high proximity.  Every component
is reported separately; consensus is a vote, not an opaque model score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev

from intellidhan_schemas import Bar

ANALYTICS_VERSION = "trend-analysis-v2"
MIN_ANALYSIS_BARS = 260
MIN_BACKTEST_BARS = 300
FORECAST_HORIZONS = {"one_month": 21, "three_months": 63}
FORECAST_PRIOR_STRENGTH = 10.0
MIN_FORECAST_STATE_SAMPLES = 12
MIN_WALK_FORWARD_SAMPLES = 12


def _pct(value: float) -> float:
    return round(value * 100.0, 2)


def _return(closes: list[float], days: int) -> float:
    return closes[-1] / closes[-1 - days] - 1.0


def _true_ranges(bars: list[Bar]) -> list[float]:
    out: list[float] = []
    for i, bar in enumerate(bars):
        previous = bars[i - 1].close if i else bar.close
        out.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
    return out


def _vote_label(vote: int) -> str:
    return "BULLISH" if vote > 0 else "BEARISH" if vote < 0 else "NEUTRAL"


def _direction(net_vote: int) -> str:
    return "UP" if net_vote > 0 else "DOWN" if net_vote < 0 else "MIXED"


def _historical_net_vote(
    closes: list[float], highs: list[float], lows: list[float], index: int
) -> int:
    """Rebuild the fixed current-state vote using only data known at ``index``."""
    sma200 = mean(closes[index - 199:index + 1])
    sma200_prior = mean(closes[index - 219:index - 19])
    distance = closes[index] / sma200 - 1.0
    slope = sma200 / sma200_prior - 1.0
    regime = 1 if distance > 0 and slope > 0 else -1 if distance < 0 and slope < 0 else 0

    returns = [closes[index] / closes[index - days] - 1.0 for days in (63, 126, 252)]
    positive = sum(value > 0 for value in returns)
    momentum = 1 if positive >= 2 else -1 if positive == 0 else 0

    prior_high = max(highs[index - 55:index])
    prior_low = min(lows[index - 20:index])
    channel = 1 if closes[index] > prior_high else -1 if closes[index] < prior_low else 0

    proximity = closes[index] / max(highs[index - 251:index + 1])
    yearly_high = 1 if proximity >= 0.95 else -1 if proximity < 0.80 else 0
    return regime + momentum + channel + yearly_high


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _wilson_interval(successes: float, total: float) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    z = 1.959963984540054
    probability = successes / total
    denominator = 1.0 + z * z / total
    center = (probability + z * z / (2.0 * total)) / denominator
    radius = z * math.sqrt(
        probability * (1.0 - probability) / total + z * z / (4.0 * total * total)
    ) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def _walk_forward_skill(observations: list[tuple[str, float]]) -> dict:
    """Prequential Brier skill; every prediction sees prior outcomes only."""
    state_counts: dict[str, list[int]] = {}
    overall_count = overall_positive = 0
    model_errors: list[float] = []
    baseline_errors: list[float] = []
    for state, forward_return in observations:
        outcome = int(forward_return > 0)
        if overall_count >= 8:
            baseline = (overall_positive + 1.0) / (overall_count + 2.0)
            state_positive, state_total = state_counts.get(state, [0, 0])
            predicted = (
                state_positive + FORECAST_PRIOR_STRENGTH * baseline
            ) / (state_total + FORECAST_PRIOR_STRENGTH)
            model_errors.append((predicted - outcome) ** 2)
            baseline_errors.append((baseline - outcome) ** 2)
        bucket = state_counts.setdefault(state, [0, 0])
        bucket[0] += outcome
        bucket[1] += 1
        overall_positive += outcome
        overall_count += 1
    if len(model_errors) < MIN_WALK_FORWARD_SAMPLES:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "evaluation_samples": len(model_errors),
            "brier_score": None,
            "baseline_brier_score": None,
            "brier_skill_pct": None,
        }
    model_brier = mean(model_errors)
    baseline_brier = mean(baseline_errors)
    skill = 1.0 - model_brier / baseline_brier if baseline_brier else 0.0
    return {
        "status": "OUTPERFORMS_BASE" if skill > 0 else "NO_BENCHMARK_EDGE",
        "evaluation_samples": len(model_errors),
        "brier_score": round(model_brier, 4),
        "baseline_brier_score": round(baseline_brier, 4),
        "brier_skill_pct": _pct(skill),
    }


def _forward_outlook(
    closes: list[float], highs: list[float], lows: list[float], current_net_vote: int
) -> dict:
    """Estimate conditional forward outcomes without overlapping observations."""
    current_state = _direction(current_net_vote)
    horizons = {}
    for name, days in FORECAST_HORIZONS.items():
        # Anchor blocks at the latest fully observed outcome.  Stepping by the
        # horizon prevents overlapping returns from masquerading as independent samples.
        origins: list[int] = []
        index = len(closes) - 1 - days
        while index >= 252:
            origins.append(index)
            index -= days
        origins.reverse()
        observations = [
            (
                _direction(_historical_net_vote(closes, highs, lows, origin)),
                closes[origin + days] / closes[origin] - 1.0,
            )
            for origin in origins
        ]
        matched = [value for state, value in observations if state == current_state]
        all_positive = sum(value > 0 for _, value in observations)
        base_probability = (all_positive + 1.0) / (len(observations) + 2.0)
        matched_positive = sum(value > 0 for value in matched)
        smoothed_successes = (
            matched_positive + FORECAST_PRIOR_STRENGTH * base_probability
        )
        effective_total = len(matched) + FORECAST_PRIOR_STRENGTH
        probability = smoothed_successes / effective_total
        interval_low, interval_high = _wilson_interval(smoothed_successes, effective_total)
        validation = _walk_forward_skill(observations)
        edge = probability - base_probability
        median_return = _quantile(matched, 0.5)

        evidence_ready = (
            len(matched) >= MIN_FORECAST_STATE_SAMPLES
            and validation["status"] == "OUTPERFORMS_BASE"
        )
        if not evidence_ready:
            label = "UNCONFIRMED"
        elif edge >= 0.05 and (median_return or 0.0) > 0:
            label = "FAVORABLE"
        elif edge <= -0.05 and (median_return or 0.0) < 0:
            label = "UNFAVORABLE"
        else:
            label = "NO_CLEAR_EDGE"

        interval_width = interval_high - interval_low
        skill = validation.get("brier_skill_pct")
        if (
            evidence_ready
            and len(matched) >= 30
            and (skill or 0.0) >= 5.0
            and interval_width <= 0.30
        ):
            confidence = "HIGH"
        elif evidence_ready and interval_width <= 0.50:
            confidence = "MODERATE"
        else:
            confidence = "LOW"

        horizons[name] = {
            "trading_days": days,
            "label": label,
            "confidence": confidence,
            "probability_positive_pct": _pct(probability),
            "probability_interval_95_pct": [_pct(interval_low), _pct(interval_high)],
            "unconditional_probability_positive_pct": _pct(base_probability),
            "conditional_edge_percentage_points": round(edge * 100.0, 2),
            "median_forward_return_pct": (
                _pct(median_return) if median_return is not None else None
            ),
            "forward_return_iqr_pct": [
                _pct(value) if value is not None else None
                for value in (_quantile(matched, 0.25), _quantile(matched, 0.75))
            ],
            "matched_state_samples": len(matched),
            "total_non_overlapping_samples": len(observations),
            "walk_forward_validation": validation,
        }

    usable = [item for item in horizons.values() if item["label"] != "UNCONFIRMED"]
    return {
        "current_state": current_state,
        "method": "fixed-state conditional forward returns with empirical-Bayes shrinkage",
        "sampling": "non-overlapping forward windows",
        "horizons": horizons,
        "strategy_context_status": "VALIDATED_CONTEXT" if usable else "UNCONFIRMED",
        "interpretation": (
            "At least one horizon has enough matched history and positive walk-forward skill."
            if usable
            else "No horizon currently clears both sample-size and walk-forward benchmark gates."
        ),
        "guardrails": [
            "Probabilities are conditional historical frequencies, not price targets or guarantees.",
            "Every walk-forward prediction uses only outcomes available at that historical date.",
            "Non-overlapping windows reduce dependence and intentionally lower the sample count.",
            "Conditional probabilities are shrunk toward the ticker base rate to limit small-sample extremes.",
            "A forecast is unconfirmed unless it beats the expanding base-rate Brier benchmark.",
        ],
    }


def analyze_daily_trend(bars: list[Bar], risk_budget: float | None = None) -> dict:
    """Return a fact-based trend report using completed adjusted daily bars."""
    bars = sorted(bars, key=lambda item: item.ts_close)
    if len(bars) < MIN_ANALYSIS_BARS:
        raise ValueError(f"at least {MIN_ANALYSIS_BARS} daily bars are required")
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    price = closes[-1]

    sma200 = mean(closes[-200:])
    sma200_prior = mean(closes[-220:-20])
    sma_distance = price / sma200 - 1.0
    sma_slope = sma200 / sma200_prior - 1.0
    regime_vote = 1 if sma_distance > 0 and sma_slope > 0 else (
        -1 if sma_distance < 0 and sma_slope < 0 else 0
    )

    momentum_returns = {days: _return(closes, days) for days in (63, 126, 252)}
    positive_momentum = sum(value > 0 for value in momentum_returns.values())
    momentum_vote = 1 if positive_momentum >= 2 else -1 if positive_momentum == 0 else 0

    prior_55_high = max(highs[-56:-1])
    prior_20_low = min(lows[-21:-1])
    if price > prior_55_high:
        breakout_vote, breakout_state = 1, "BREAKOUT"
    elif price < prior_20_low:
        breakout_vote, breakout_state = -1, "BREAKDOWN"
    else:
        breakout_vote, breakout_state = 0, "INSIDE_CHANNEL"

    high_52w = max(highs[-252:])
    low_52w = min(lows[-252:])
    high_proximity = price / high_52w
    high_vote = 1 if high_proximity >= 0.95 else -1 if high_proximity < 0.80 else 0

    true_ranges = _true_ranges(bars)
    atr14 = mean(true_ranges[-14:])
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    realized_vol20 = pstdev(log_returns[-20:]) * math.sqrt(252)
    risk_per_share = max(price - prior_20_low, 2.0 * atr14)
    quantity = math.floor(risk_budget / risk_per_share) if risk_budget else None

    votes = {
        "sma_200_regime": regime_vote,
        "time_series_momentum": momentum_vote,
        "donchian_55_20": breakout_vote,
        "high_52w_proximity": high_vote,
    }
    net_vote = sum(votes.values())
    if net_vote >= 3:
        consensus = "STRONG_UPTREND"
    elif net_vote >= 1:
        consensus = "UPTREND"
    elif net_vote <= -3:
        consensus = "STRONG_DOWNTREND"
    elif net_vote <= -1:
        consensus = "DOWNTREND"
    else:
        consensus = "MIXED"

    return {
        "analytics_version": ANALYTICS_VERSION,
        "symbol": bars[-1].symbol,
        "as_of": bars[-1].ts_close.isoformat(),
        "source": bars[-1].source,
        "daily_bars": len(bars),
        "price": round(price, 4),
        "consensus": {
            "label": consensus,
            "net_vote": net_vote,
            "bullish_votes": sum(value > 0 for value in votes.values()),
            "bearish_votes": sum(value < 0 for value in votes.values()),
            "neutral_votes": sum(value == 0 for value in votes.values()),
            "method_votes": {key: _vote_label(value) for key, value in votes.items()},
        },
        "methods": {
            "sma_200_regime": {
                "signal": _vote_label(regime_vote),
                "sma_200": round(sma200, 4),
                "price_distance_pct": _pct(sma_distance),
                "sma_20d_slope_pct": _pct(sma_slope),
                "rule": "bullish only when price is above a rising 200-day average",
            },
            "time_series_momentum": {
                "signal": _vote_label(momentum_vote),
                "return_3m_pct": _pct(momentum_returns[63]),
                "return_6m_pct": _pct(momentum_returns[126]),
                "return_12m_pct": _pct(momentum_returns[252]),
                "positive_horizons": positive_momentum,
                "rule": "bullish when at least two of 3/6/12-month returns are positive",
            },
            "donchian_55_20": {
                "signal": _vote_label(breakout_vote),
                "state": breakout_state,
                "prior_55d_high": round(prior_55_high, 4),
                "prior_20d_low": round(prior_20_low, 4),
                "distance_to_breakout_pct": _pct(prior_55_high / price - 1.0),
                "rule": "enter on a 55-day closing breakout; exit below the prior 20-day low",
            },
            "high_52w_proximity": {
                "signal": _vote_label(high_vote),
                "high_52w": round(high_52w, 4),
                "low_52w": round(low_52w, 4),
                "percent_of_52w_high": _pct(high_proximity),
                "rule": "95% or more of the 52-week high is positive momentum context",
                "backtest_note": "context only; the published effect is cross-sectional",
            },
        },
        "risk": {
            "atr14": round(atr14, 4),
            "atr14_pct": _pct(atr14 / price),
            "realized_volatility_20d_annualized_pct": _pct(realized_vol20),
            "prior_20d_low": round(prior_20_low, 4),
            "two_atr_reference": round(price - 2.0 * atr14, 4),
            "risk_per_share_reference": round(risk_per_share, 4),
            "risk_budget": risk_budget,
            "reference_quantity": quantity,
        },
        "forecast": _forward_outlook(closes, highs, lows, net_vote),
        "interpretation": _interpretation(consensus, breakout_state, high_proximity),
        "limitations": [
            "Forward probabilities are historical conditional estimates and do not guarantee returns.",
            "Single-stock results contain idiosyncratic, gap, earnings, and delisting risk.",
            "Backtests must use corporate-action-adjusted data, costs, next-session execution, and no lookahead.",
            "Parameters are fixed across tickers; do not tune them per symbol after seeing results.",
        ],
    }


def _interpretation(consensus: str, breakout_state: str, proximity: float) -> str:
    if consensus in {"STRONG_UPTREND", "UPTREND"}:
        if breakout_state == "BREAKOUT":
            return "Trend and momentum agree, and price has confirmed a fresh channel breakout."
        if proximity >= 0.95:
            return "Trend and momentum are constructive; price is near its yearly high but not confirmed."
        return "The broader trend is positive, but there is no fresh breakout confirmation."
    if consensus in {"STRONG_DOWNTREND", "DOWNTREND"}:
        return "Trend evidence is defensive; avoid treating a short bounce as a confirmed reversal."
    return "The methods disagree; wait for clearer trend or breakout confirmation."


@dataclass(frozen=True)
class BacktestResult:
    method: str
    total_return_pct: float
    cagr_pct: float
    annual_volatility_pct: float
    sharpe: float | None
    max_drawdown_pct: float
    exposure_pct: float
    trades: int
    turnover_events: int
    cost_bps: float


def backtest_trend_methods(bars: list[Bar], cost_bps: float = 10.0) -> dict:
    """Long/cash, close-signal→next-close comparison with fixed parameters."""
    bars = sorted(bars, key=lambda item: item.ts_close)
    if len(bars) < MIN_BACKTEST_BARS:
        raise ValueError(f"at least {MIN_BACKTEST_BARS} daily bars are required for backtesting")
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    positions = _method_positions(closes, highs, lows)
    start = 252
    results = {
        name: _performance(name, closes, signal, start, cost_bps)
        for name, signal in positions.items()
    }
    buy_hold = _performance("buy_and_hold", closes, [1] * len(closes), start, 0.0)
    return {
        "analytics_version": ANALYTICS_VERSION,
        "assumptions": {
            "execution": "signal at adjusted daily close; exposure starts next close-to-close period",
            "position": "long or cash; no leverage or shorting",
            "round_trip_cost_bps": cost_bps,
            "parameters": {
                "sma_200": 200,
                "time_series_momentum_days": 252,
                "donchian_entry_exit_days": [55, 20],
                "consensus_votes_required": 2,
            },
        },
        "methods": {key: result.__dict__ for key, result in results.items()},
        "buy_and_hold": buy_hold.__dict__,
    }


def _method_positions(
    closes: list[float], highs: list[float], lows: list[float]
) -> dict[str, list[int]]:
    n = len(closes)
    sma = [0] * n
    momentum = [0] * n
    donchian = [0] * n
    channel_position = 0
    for i in range(n):
        if i >= 199:
            sma[i] = int(closes[i] > mean(closes[i - 199:i + 1]))
        if i >= 252:
            momentum[i] = int(closes[i] > closes[i - 252])
        if i >= 55:
            if not channel_position and closes[i] > max(highs[i - 55:i]):
                channel_position = 1
            elif channel_position and i >= 20 and closes[i] < min(lows[i - 20:i]):
                channel_position = 0
        donchian[i] = channel_position
    consensus = [int(sma[i] + momentum[i] + donchian[i] >= 2) for i in range(n)]
    return {
        "sma_200": sma,
        "time_series_momentum_12m": momentum,
        "donchian_55_20": donchian,
        "majority_consensus": consensus,
    }


def _performance(
    name: str, closes: list[float], positions: list[int], start: int, cost_bps: float
) -> BacktestResult:
    cost = cost_bps / 10_000.0
    daily: list[float] = []
    equity = 1.0
    curve = [equity]
    turnover = 0
    trades = 0
    for i in range(start + 1, len(closes)):
        prior_position = positions[i - 1]
        previous_prior = positions[i - 2]
        change = abs(prior_position - previous_prior)
        turnover += change
        if prior_position == 1 and previous_prior == 0:
            trades += 1
        value = prior_position * (closes[i] / closes[i - 1] - 1.0) - change * cost
        daily.append(value)
        equity *= 1.0 + value
        curve.append(equity)
    years = len(daily) / 252.0
    cagr = equity ** (1.0 / years) - 1.0 if years > 0 and equity > 0 else -1.0
    avg = mean(daily) if daily else 0.0
    vol = pstdev(daily) * math.sqrt(252) if len(daily) > 1 else 0.0
    sharpe = avg / pstdev(daily) * math.sqrt(252) if len(daily) > 1 and pstdev(daily) else None
    peak = curve[0]
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0)
    return BacktestResult(
        method=name,
        total_return_pct=_pct(equity - 1.0),
        cagr_pct=_pct(cagr),
        annual_volatility_pct=_pct(vol),
        sharpe=round(sharpe, 3) if sharpe is not None else None,
        max_drawdown_pct=_pct(max_drawdown),
        exposure_pct=_pct(mean(positions[start:-1])),
        trades=trades,
        turnover_events=turnover,
        cost_bps=cost_bps,
    )
