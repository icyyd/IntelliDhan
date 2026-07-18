"""Chronological panel regression for the technical multi-brain specialist.

Current SEC, news, and social snapshots are deliberately excluded: the repo does
not yet have point-in-time archives for those inputs. Candidate models are
selected on a development window and reported once on a later validation
window. This is a research diagnostic, not a strategy-promotion result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from intellidhan_gateway.universe import load_live_symbols
from intellidhan_ingestor.providers import YahooProvider
from intellidhan_schemas import Bar, Timeframe


MODEL_VERSION = "technical-panel-regression-v1"
FEATURE_SETS = {
    "core": ("return_6m", "return_12m", "sma_distance", "sma_slope", "high_proximity"),
    "risk_aware": (
        "return_3m",
        "return_6m",
        "return_12m",
        "sma_distance",
        "sma_slope",
        "high_proximity",
        "volatility_20d",
        "breakout_distance",
    ),
}


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return np.where(
        values >= 0,
        1.0 / (1.0 + np.exp(-values)),
        np.exp(values) / (1.0 + np.exp(values)),
    )


def _fit_logistic(x: np.ndarray, y: np.ndarray, ridge: float = 2.0) -> tuple:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = (x - mean) / scale
    design = np.column_stack([np.ones(len(x)), standardized])
    weights = np.zeros(design.shape[1])
    penalty = np.eye(design.shape[1]) * ridge
    penalty[0, 0] = 0.0
    for _ in range(40):
        probability = _sigmoid(design @ weights)
        variance = np.clip(probability * (1.0 - probability), 1e-5, None)
        gradient = design.T @ (probability - y) + penalty @ weights
        hessian = (design.T * variance) @ design + penalty
        update = np.linalg.solve(hessian, gradient)
        weights -= update
        if float(np.max(np.abs(update))) < 1e-7:
            break
    return weights, mean, scale


def _predict(model: tuple, values: np.ndarray) -> float:
    weights, mean, scale = model
    row = np.concatenate([[1.0], (values - mean) / scale])
    return float(_sigmoid(np.asarray([row @ weights]))[0])


def _vote(features: dict[str, float]) -> str:
    votes = [
        1 if features["sma_distance"] > 0 and features["sma_slope"] > 0 else -1,
        1 if sum(features[key] > 0 for key in ("return_3m", "return_6m", "return_12m")) >= 2 else -1,
        1 if features["breakout_distance"] > 0 else 0,
        1 if features["high_proximity"] >= 0.95 else -1 if features["high_proximity"] < 0.80 else 0,
    ]
    total = sum(votes)
    return "UP" if total > 0 else "DOWN" if total < 0 else "MIXED"


def _records(symbol: str, bars: list[Bar], horizon: int) -> list[dict[str, Any]]:
    ordered = sorted(bars, key=lambda bar: bar.ts_close)
    close = np.asarray([bar.close for bar in ordered], dtype=float)
    high = np.asarray([bar.high for bar in ordered], dtype=float)
    if len(close) < 252 + horizon + 1:
        return []
    records = []
    # Step by the horizon so outcomes for one symbol do not overlap.
    for index in range(252, len(close) - horizon, horizon):
        sma200 = float(close[index - 199 : index + 1].mean())
        prior_sma = float(close[index - 219 : index - 19].mean())
        returns = np.diff(np.log(close[index - 20 : index + 1]))
        prior_high = float(high[index - 55 : index].max())
        features = {
            "return_3m": float(close[index] / close[index - 63] - 1.0),
            "return_6m": float(close[index] / close[index - 126] - 1.0),
            "return_12m": float(close[index] / close[index - 252] - 1.0),
            "sma_distance": float(close[index] / sma200 - 1.0),
            "sma_slope": float(sma200 / prior_sma - 1.0),
            "high_proximity": float(close[index] / high[index - 251 : index + 1].max()),
            "volatility_20d": float(returns.std(ddof=1) * math.sqrt(252)),
            "breakout_distance": float(close[index] / prior_high - 1.0),
        }
        records.append(
            {
                "symbol": symbol,
                "origin": ordered[index].ts_close,
                "outcome_at": ordered[index + horizon].ts_close,
                "features": features,
                "vote": _vote(features),
                "outcome": int(close[index + horizon] > close[index]),
            }
        )
    return records


def _metrics(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    if not rows:
        return {
            "samples": 0,
            "brier_score": None,
            "log_loss": None,
            "directional_accuracy_pct": None,
            "calibration_error_pct": None,
        }
    probabilities = np.clip(
        np.asarray([row[probability_key] for row in rows], dtype=float), 1e-6, 1 - 1e-6
    )
    outcomes = np.asarray([row["outcome"] for row in rows], dtype=float)
    brier = float(np.mean((probabilities - outcomes) ** 2))
    log_loss = float(
        -np.mean(outcomes * np.log(probabilities) + (1.0 - outcomes) * np.log(1.0 - probabilities))
    )
    accuracy = float(np.mean((probabilities >= 0.5) == outcomes))
    calibration = 0.0
    for lower in np.linspace(0, 0.9, 10):
        mask = (probabilities >= lower) & (probabilities < lower + 0.1)
        if mask.any():
            calibration += float(mask.mean()) * abs(
                float(probabilities[mask].mean() - outcomes[mask].mean())
            )
    return {
        "samples": len(rows),
        "brier_score": round(brier, 5),
        "log_loss": round(log_loss, 5),
        "directional_accuracy_pct": round(accuracy * 100.0, 2),
        "calibration_error_pct": round(calibration * 100.0, 2),
    }


def evaluate_panel_regression(
    histories: dict[str, list[Bar]], *, horizon: int = 21, minimum_train: int = 80
) -> dict[str, Any]:
    if horizon not in {21, 63}:
        raise ValueError("horizon must be 21 or 63 sessions")
    records = [
        row
        for symbol, bars in histories.items()
        for row in _records(symbol, bars, horizon)
    ]
    if not records:
        raise ValueError("no eligible non-overlapping panel observations")
    by_origin: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_origin[row["origin"]].append(row)
    predictions = []
    for origin in sorted(by_origin):
        train = [row for row in records if row["outcome_at"] < origin]
        if len(train) < minimum_train or len({row["outcome"] for row in train}) < 2:
            continue
        base_probability = (sum(row["outcome"] for row in train) + 1.0) / (len(train) + 2.0)
        state_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in train:
            state_counts[row["vote"]][0] += row["outcome"]
            state_counts[row["vote"]][1] += 1
        fitted = {}
        for name, feature_names in FEATURE_SETS.items():
            x = np.asarray(
                [[row["features"][key] for key in feature_names] for row in train],
                dtype=float,
            )
            y = np.asarray([row["outcome"] for row in train], dtype=float)
            fitted[name] = _fit_logistic(x, y)
        for row in by_origin[origin]:
            positives, total = state_counts[row["vote"]]
            fixed_probability = (positives + 10.0 * base_probability) / (total + 10.0)
            result = {
                "symbol": row["symbol"],
                "origin": row["origin"],
                "outcome": row["outcome"],
                "base": base_probability,
                "fixed_vote": fixed_probability,
            }
            for name, feature_names in FEATURE_SETS.items():
                values = np.asarray([row["features"][key] for key in feature_names])
                result[name] = _predict(fitted[name], values)
            predictions.append(result)
    if len(predictions) < 30:
        raise ValueError("insufficient matured walk-forward predictions")
    dates = sorted({row["origin"] for row in predictions})
    split_date = dates[max(1, int(len(dates) * 0.70))]
    development = [row for row in predictions if row["origin"] < split_date]
    validation = [row for row in predictions if row["origin"] >= split_date]
    development_metrics = {
        name: _metrics(development, name) for name in ("base", "fixed_vote", *FEATURE_SETS)
    }
    selected = min(FEATURE_SETS, key=lambda name: development_metrics[name]["brier_score"])
    validation_metrics = {
        name: _metrics(validation, name) for name in ("base", "fixed_vote", selected)
    }
    selected_brier = validation_metrics[selected]["brier_score"]
    benchmark_brier = validation_metrics["fixed_vote"]["brier_score"]
    improvement = (
        (benchmark_brier - selected_brier) / benchmark_brier * 100.0
        if benchmark_brier
        else 0.0
    )
    per_symbol = {}
    for symbol in sorted(histories):
        rows = [row for row in validation if row["symbol"] == symbol]
        if rows:
            per_symbol[symbol] = {
                "selected": _metrics(rows, selected),
                "fixed_vote": _metrics(rows, "fixed_vote"),
            }
    improved_symbols = sum(
        row["selected"]["brier_score"] < row["fixed_vote"]["brier_score"]
        for row in per_symbol.values()
    )
    broad = improved_symbols >= max(1, math.ceil(len(per_symbol) * 0.60))
    positive = improvement > 0 and broad and len(validation) >= 30
    return {
        "model_version": MODEL_VERSION,
        "research_type": "chronological pooled technical probability diagnostic",
        "horizon_sessions": horizon,
        "symbols": sorted(histories),
        "trials_disclosed": len(FEATURE_SETS),
        "selection": {
            "selected_on_development_only": selected,
            "validation_start": split_date.isoformat(),
        },
        "development": development_metrics,
        "validation": validation_metrics,
        "validation_brier_improvement_vs_fixed_vote_pct": round(improvement, 2),
        "per_symbol_validation": per_symbol,
        "symbols_improved": improved_symbols,
        "promotion_status": (
            "EVIDENCE_POSITIVE_REQUIRES_FORWARD_PAPER"
            if positive
            else "NO_BENCHMARK_EDGE"
        ),
        "limitations": [
            "The current ticker universe is survivorship-biased.",
            "Current fundamentals, news, social data, and earnings estimates are excluded.",
            "Classification quality is not a trading-return or profitability result.",
            "Two feature sets were tried; selection risk is disclosed and validation is held later.",
            "Promotion still requires a point-in-time universe and an untouched forward paper period.",
        ],
    }


async def run(symbols: list[str], years: int, horizon: int) -> dict[str, Any]:
    provider = YahooProvider()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=years * 366)

    async def one(symbol: str) -> tuple[str, list[Bar]]:
        return symbol, await provider.get_bars(
            symbol, Timeframe.D1, start, end, adjusted=True
        )

    histories = dict(await asyncio.gather(*(one(symbol) for symbol in symbols)))
    return evaluate_panel_regression(histories, horizon=horizon)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=load_live_symbols())
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--horizon", type=int, choices=(21, 63), default=21)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(run(args.symbols, args.years, args.horizon))
    content = json.dumps(report, indent=2, default=str)
    if args.output:
        from pathlib import Path

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
