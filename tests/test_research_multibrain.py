from datetime import datetime, timedelta, timezone

from intellidhan_learning.research_multibrain import evaluate_panel_regression
from intellidhan_schemas import Bar, Timeframe


def history(symbol: str, drift: float, jump_index: int | None = None) -> list[Bar]:
    start = datetime(2015, 1, 1, tzinfo=timezone.utc)
    price = 50.0
    rows = []
    for index in range(900):
        price *= 1.0 + drift + ((index % 17) - 8) * 0.00015
        if jump_index is not None and index == jump_index:
            price *= 4.0
        rows.append(
            Bar(
                symbol=symbol,
                timeframe=Timeframe.D1,
                ts_close=start + timedelta(days=index, hours=6),
                open=price * 0.997,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1_000_000,
                source="fixture_adjusted",
            )
        )
    return rows


def test_panel_regression_is_chronological_and_discloses_trials():
    report = evaluate_panel_regression(
        {
            "UP": history("UP", 0.0008),
            "FLAT": history("FLAT", 0.0),
            "DOWN": history("DOWN", -0.0005),
            "CHOP": history("CHOP", 0.0001),
        },
        minimum_train=20,
    )
    assert report["trials_disclosed"] == 2
    assert report["selection"]["selected_on_development_only"] in {"core", "risk_aware"}
    assert report["validation"]["fixed_vote"]["samples"] >= 30
    assert report["promotion_status"] in {
        "EVIDENCE_POSITIVE_REQUIRES_FORWARD_PAPER",
        "NO_BENCHMARK_EDGE",
    }
    assert "Current fundamentals" in report["limitations"][1]


def test_future_jump_does_not_change_earlier_validation_boundary():
    base = {
        "A": history("A", 0.0005),
        "B": history("B", 0.0002),
        "C": history("C", -0.0002),
        "D": history("D", 0.0),
    }
    jumped = {**base, "D": history("D", 0.0, jump_index=890)}
    first = evaluate_panel_regression(base, minimum_train=20)
    second = evaluate_panel_regression(jumped, minimum_train=20)
    assert first["selection"]["validation_start"] == second["selection"]["validation_start"]
    assert first["trials_disclosed"] == second["trials_disclosed"]
