"""Cross-universe smart-play ranking built from completed daily evidence.

The scorer is deliberately deterministic.  It turns per-symbol measurements
into comparable research setups; it does not produce orders or price targets.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SMART_SCAN_VERSION = "smart-play-v1"
MIN_PLAY_PRICE = 5.0
MIN_DOLLAR_VOLUME_20D = 5_000_000.0


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _percentile_ranks(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    """Return average-rank percentiles while handling missing values and ties."""
    observations = [
        (str(row["symbol"]), float(row[key]))
        for row in rows
        if row.get(key) is not None
    ]
    if not observations:
        return {}
    if len(observations) == 1:
        return {observations[0][0]: 100.0}
    ordered = sorted(observations, key=lambda item: item[1])
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_index = (index + end - 1) / 2.0
        percentile = round(average_index / (len(ordered) - 1) * 100.0, 1)
        for symbol, _ in ordered[index:end]:
            ranks[symbol] = percentile
        index = end
    return ranks


def _fact(
    fact_id: str,
    label: str,
    value: float | str | bool | None,
    unit: str,
    interpretation: str,
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "label": label,
        "value": value,
        "unit": unit,
        "interpretation": interpretation,
    }


def _momentum_play(row: dict[str, Any]) -> dict[str, Any]:
    ranks = row["universe_percentiles"]
    above_200 = (row.get("distance_from_sma_200_pct") or -999.0) > 0
    rising_200 = (row.get("sma_200_slope_20d_pct") or -999.0) > 0
    positive_12_1 = (row.get("return_12_1_pct") or -999.0) > 0
    positive_6_1 = (row.get("return_6_1_pct") or -999.0) > 0
    liquid = (row.get("average_dollar_volume_20d") or 0.0) >= MIN_DOLLAR_VOLUME_20D
    priced = (row.get("price") or 0.0) >= MIN_PLAY_PRICE
    eligible = bool(
        above_200 and rising_200 and positive_12_1 and positive_6_1 and liquid and priced
    )
    score = (
        ranks.get("return_12_1_pct", 0.0) * 0.40
        + ranks.get("return_6_1_pct", 0.0) * 0.25
        + ranks.get("percent_of_52w_high", 0.0) * 0.20
        + (100.0 - ranks.get("realized_volatility_20d_pct", 100.0)) * 0.15
    )
    return {
        "key": "MOMENTUM_LEADER",
        "label": "Momentum leader",
        "eligible": eligible,
        "status": "READY_TO_RESEARCH" if eligible else "NO_SETUP",
        "score": round(score, 1) if eligible else 0.0,
        "horizon": "1–3 months",
        "evidence": [
            _fact(
                "return_12_1",
                "12–1 month return",
                row.get("return_12_1_pct"),
                "percent",
                "Ranks medium-term momentum while skipping the most recent month.",
            ),
            _fact(
                "momentum_rank",
                "Configured-universe momentum rank",
                ranks.get("return_12_1_pct"),
                "percentile",
                "Compares this symbol only with the configured scan universe.",
            ),
            _fact(
                "sma200_slope",
                "200-day trend slope",
                row.get("sma_200_slope_20d_pct"),
                "percent",
                "A positive slope reduces the chance of treating a bounce as a trend.",
            ),
        ],
        "confirmation": "Remain above a rising 50-day and 200-day average.",
        "invalidation": "Daily close below the 200-day average or negative 12–1 momentum.",
    }


def _breakout_play(row: dict[str, Any]) -> dict[str, Any]:
    distance = row.get("distance_to_prior_55d_high_pct")
    volume_ratio = row.get("volume_ratio_5d_to_prior_20d")
    contraction = row.get("volatility_ratio_20d_to_60d")
    above_200 = (row.get("distance_from_sma_200_pct") or -999.0) > 0
    rising_200 = (row.get("sma_200_slope_20d_pct") or -999.0) > 0
    near_breakout = distance is not None and -4.0 <= distance <= 3.0
    liquid = (row.get("average_dollar_volume_20d") or 0.0) >= MIN_DOLLAR_VOLUME_20D
    priced = (row.get("price") or 0.0) >= MIN_PLAY_PRICE
    eligible = bool(above_200 and rising_200 and near_breakout and liquid and priced)
    confirmed = bool(
        eligible and distance is not None and distance > 0 and (volume_ratio or 0.0) >= 1.2
    )
    proximity_score = _clip(100.0 - abs(distance or 99.0) * 20.0)
    volume_score = _clip(((volume_ratio or 0.0) - 0.6) / 1.0 * 100.0)
    contraction_score = _clip((1.2 - (contraction or 2.0)) / 0.6 * 100.0)
    trend_score = row["universe_percentiles"].get("return_6_1_pct", 0.0)
    score = proximity_score * 0.35 + volume_score * 0.25 + contraction_score * 0.20 + trend_score * 0.20
    return {
        "key": "BREAKOUT_WATCH",
        "label": "Breakout watch",
        "eligible": eligible,
        "status": "CONFIRMED" if confirmed else "WAIT_FOR_CONFIRMATION" if eligible else "NO_SETUP",
        "score": round(score, 1) if eligible else 0.0,
        "horizon": "2–8 weeks",
        "evidence": [
            _fact(
                "breakout_distance",
                "Distance to prior 55-day high",
                distance,
                "percent",
                "A close above zero is a breakout; proximity alone is only a watch setup.",
            ),
            _fact(
                "volume_confirmation",
                "Recent volume versus prior average",
                volume_ratio,
                "ratio",
                "A ratio of 1.2x or higher confirms broader participation.",
            ),
            _fact(
                "volatility_contraction",
                "20-day versus 60-day volatility",
                contraction,
                "ratio",
                "A ratio below 1.0 indicates recent volatility contraction.",
            ),
        ],
        "confirmation": "Close above the prior 55-day high with recent volume at least 1.2x its prior average.",
        "invalidation": "Close below the 50-day average or the prior 20-day low.",
    }


def _pullback_play(row: dict[str, Any]) -> dict[str, Any]:
    high_distance = row.get("distance_from_52w_high_pct")
    sma50_distance = row.get("distance_from_sma_50_pct")
    above_200 = (row.get("distance_from_sma_200_pct") or -999.0) > 0
    rising_200 = (row.get("sma_200_slope_20d_pct") or -999.0) > 0
    positive_momentum = (row.get("return_12_1_pct") or -999.0) > 0
    orderly_pullback = bool(
        high_distance is not None
        and -18.0 <= high_distance <= -3.0
        and sma50_distance is not None
        and -6.0 <= sma50_distance <= 6.0
    )
    liquid = (row.get("average_dollar_volume_20d") or 0.0) >= MIN_DOLLAR_VOLUME_20D
    priced = (row.get("price") or 0.0) >= MIN_PLAY_PRICE
    eligible = bool(
        above_200 and rising_200 and positive_momentum and orderly_pullback and liquid and priced
    )
    location_score = _clip(100.0 - abs((sma50_distance or 99.0)) * 12.0)
    momentum_score = row["universe_percentiles"].get("return_12_1_pct", 0.0)
    risk_score = 100.0 - row["universe_percentiles"].get(
        "realized_volatility_20d_pct", 100.0
    )
    score = location_score * 0.35 + momentum_score * 0.40 + risk_score * 0.25
    return {
        "key": "TREND_PULLBACK",
        "label": "Trend pullback",
        "eligible": eligible,
        "status": "WAIT_FOR_TURN" if eligible else "NO_SETUP",
        "score": round(score, 1) if eligible else 0.0,
        "horizon": "2–12 weeks",
        "evidence": [
            _fact(
                "pullback_depth",
                "Pullback from 52-week high",
                high_distance,
                "percent",
                "A measured pullback can improve entry location without assuming reversal.",
            ),
            _fact(
                "sma50_distance",
                "Distance from 50-day average",
                sma50_distance,
                "percent",
                "The setup looks for price near, not far above, the medium-term trend.",
            ),
            _fact(
                "return_12_1",
                "12–1 month return",
                row.get("return_12_1_pct"),
                "percent",
                "Positive medium-term momentum is required before treating weakness as a pullback.",
            ),
        ],
        "confirmation": "Reclaim the prior 20-day high while the 200-day average remains rising.",
        "invalidation": "Close below the 200-day average or prior 20-day low.",
    }


def rank_smart_plays(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add cross-sectional ranks and three auditable play families to rows."""
    enriched = deepcopy(rows)
    rank_keys = (
        "return_12_1_pct",
        "return_6_1_pct",
        "percent_of_52w_high",
        "realized_volatility_20d_pct",
    )
    ranks = {key: _percentile_ranks(enriched, key) for key in rank_keys}
    for row in enriched:
        symbol = str(row["symbol"])
        row["universe_percentiles"] = {
            key: values[symbol] for key, values in ranks.items() if symbol in values
        }
        plays = [_momentum_play(row), _breakout_play(row), _pullback_play(row)]
        row["plays"] = {play["key"]: play for play in plays}
        eligible = [play for play in plays if play["eligible"]]
        row["best_play"] = max(eligible, key=lambda play: play["score"]) if eligible else {
            "key": "NO_SETUP",
            "label": "No current setup",
            "eligible": False,
            "status": "NO_SETUP",
            "score": 0.0,
            "horizon": None,
            "evidence": [],
            "confirmation": "Wait for a supported trend, breakout, or pullback setup.",
            "invalidation": None,
        }
        row["smart_scan_version"] = SMART_SCAN_VERSION
    enriched.sort(key=lambda item: item["best_play"]["score"], reverse=True)
    return enriched
