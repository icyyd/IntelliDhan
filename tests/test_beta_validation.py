from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from intellidhan_learning.beta_validation import (
    FROZEN_EMA9,
    FROZEN_ORR_CANDIDATE,
    FROZEN_ORR_CONTROL,
    completed_intraday,
    contiguous_sessions,
    fingerprint,
    performance,
)
from intellidhan_schemas import Bar, Timeframe


def test_historical_controls_pin_every_parameter_independently_of_live_profiles(monkeypatch):
    from intellidhan_learning.opening_range_reversal import ORR_PROFILES

    monkeypatch.setitem(ORR_PROFILES, "control", replace(FROZEN_ORR_CONTROL, runner_target_r=9))
    assert asdict(FROZEN_ORR_CONTROL) == {
        "daily_atr_period": 14, "manipulation_fraction": 0.20,
        "min_reversal_body_fraction": 0.50, "entry_cutoff_et": "11:30",
        "stop_buffer_atr5": 0.15, "partial_fraction": 0.50, "runner_target_r": 2.0,
        "prior_level_filter": False, "prior_level_tolerance_atr5": 0.25,
        "cost_bps_per_side": 2.0, "slippage_atr5": 0.02,
        "flatten_time_et": "15:55", "min_session_bars": 40,
    }
    assert asdict(FROZEN_ORR_CANDIDATE) == {
        **asdict(FROZEN_ORR_CONTROL), "manipulation_fraction": 0.25,
        "min_reversal_body_fraction": 0.25, "entry_cutoff_et": "10:30",
    }
    assert asdict(FROZEN_EMA9) == {
        "timeframe": Timeframe.M30, "ema_period": 9, "atr_period": 14,
        "initial_stop_atr": 1.25, "trail_atr": 1.50, "trail_activation_r": 0.75,
        "confirmation_bars": 2, "require_ema21_alignment": True,
        "reentry_cooldown_bars": 1, "allow_short": False, "rth_only": True,
        "flatten_time_et": "15:55", "cost_bps_per_side": 2.0, "stop_limit_offset_atr": 0.10,
    }


def trade(day, net_r, minute=0):
    opened = datetime.fromisoformat(day + "T14:00:00+00:00")
    return SimpleNamespace(
        net_r=net_r, entry_ts=opened, exit_ts=opened + timedelta(minutes=minute)
    )


def test_net_wins_and_drawdown_include_cost_losses():
    sessions = ["2026-08-03", "2026-08-04", "2026-08-05"]
    result = performance([trade(sessions[0], 1), trade(sessions[1], -1.2),
                          trade(sessions[2], -0.1)], sessions)
    assert result["net_win_rate"] == 0.3333
    assert result["max_drawdown_r"] == 1.3
    assert result["avg_net_r"] == -0.1
    assert result["trading_sessions"] == 3
    assert result["mean_net_r_95pct_session_block_interval"] is None
    assert result["positive_mean_supported"] is False


def test_bootstrap_keeps_correlated_same_day_symbols_together():
    sessions = [f"2026-08-{day:02d}" for day in range(3, 23)]
    rows = [item for day in sessions for item in (trade(day, 1), trade(day, -1))]
    result = performance(rows, sessions)
    assert result["mean_net_r_95pct_session_block_interval"] == [0, 0]
    assert result["max_drawdown_r"] == 0
    assert result["positive_mean_supported"] is False


def test_no_trade_sample_never_claims_edge():
    result = performance([], [f"2026-08-{day:02d}" for day in range(3, 23)])
    assert result["avg_net_r"] is None
    assert result["net_win_rate"] is None
    assert result["positive_mean_supported"] is False
    assert result["trading_sessions"] == 0


def test_bootstrap_does_not_inflate_active_session_count():
    sessions = [f"2026-08-{day:02d}" for day in range(3, 23)]
    assert performance([trade(sessions[0], -1)], sessions)["trading_sessions"] == 1


def test_outside_sample_trade_is_rejected():
    with pytest.raises(ValueError, match="outside"):
        performance([trade("2026-08-03", 1)], ["2026-08-04"])


def bar(at):
    return Bar(symbol="SPY", timeframe=Timeframe.M5, ts_close=datetime.fromisoformat(at),
               open=100, high=102, low=99, close=101, volume=1000, source="test")


def test_rejects_open_day_weekend_and_after_hours():
    rows = [bar("2026-09-23T20:00:00+00:00"), bar("2026-09-23T20:05:00+00:00"),
            bar("2026-09-24T14:00:00+00:00"), bar("2026-09-19T14:00:00+00:00")]
    now = datetime(2026, 9, 24, 15, tzinfo=timezone.utc)
    assert completed_intraday(rows, now) == [rows[0]]


def test_data_fingerprint_is_order_independent_and_sensitive_to_prices():
    rows = [bar("2026-09-23T19:55:00+00:00"), bar("2026-09-23T20:00:00+00:00")]
    assert fingerprint(rows)["sha256"] == fingerprint(rows[::-1])["sha256"]
    edited = [rows[0].model_copy(update={"close": 101.5}), rows[1]]
    assert fingerprint(rows)["sha256"] != fingerprint(edited)["sha256"]


def test_incomplete_or_duplicate_sessions_are_excluded():
    opened = datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc)
    rows = [bar((opened + timedelta(minutes=5 * i)).isoformat()) for i in range(1, 79)]
    assert contiguous_sessions(rows) == (rows, [])
    assert contiguous_sessions(rows[:-1]) == ([], ["SPY/5m/2026-09-23"])
    assert contiguous_sessions(rows + [rows[0]]) == ([], ["SPY/5m/2026-09-23"])
