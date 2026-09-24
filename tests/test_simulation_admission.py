"""Research admission is not live promotion or a bypass of execution safeguards."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from intellidhan_analytics.indicators import IndicatorSnapshot
from intellidhan_analytics.levels import Level, LevelRole
from intellidhan_analytics.trend import TrendSnapshot, TrendState
from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.runner import EngineRunner
from intellidhan_engine.strategies import Ema9MtfZeroDte
from intellidhan_gateway.autotrade import AutotradeManager, AutomationMode, IntentStatus
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Module

from test_autotrade import make_alert, option_candidate


def research_alert(**overrides):
    values = {
        "strategy": "EMA9_MTF_0DTE",
        "module": Module.ZDTE,
        "confidence": 0.74,
        "research_only": True,
        "status": "SHADOW",
    }
    values.update(overrides)
    return make_alert(**values)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # Test the shared admission rules independently of the local launcher,
    # which separately enforces Simulation-only operation.
    monkeypatch.delenv("INTELLIDHAN_LOCAL_ONLY", raising=False)
    return AutotradeManager(tmp_path / "policy.yaml", tmp_path / "state.json")


def test_capped_research_alert_can_only_create_simulation_intent(manager):
    alert = research_alert()
    intent = manager.on_alert(alert)

    assert manager.policy.min_confidence == 0.75
    assert intent.mode == AutomationMode.SIMULATION
    assert intent.status == IntentStatus.SHADOW
    assert intent.confidence == 0.74
    assert not intent.reasons
    assert intent.order_plan["instrument"]["type"] == "OPTION_SELECTION_REQUIRED"
    with pytest.raises(ValueError, match="LIVE|Simulation|SIMULATION"):
        manager.claim(
            intent.intent_id, "codex", underlying_price=500.0,
            observed_at=datetime.now(timezone.utc),
        )


@pytest.mark.parametrize("confidence", [0.74, 0.75])
@pytest.mark.parametrize("research_only", [False, True])
def test_live_keeps_confidence_research_and_calibration_gates(
    manager, confidence, research_only
):
    manager.update_policy({"mode": "LIVE", "live_for_minutes": 30})
    intent = manager.on_alert(research_alert(
        confidence=confidence, research_only=research_only, status="ACTIVE",
    ))

    assert intent.mode == AutomationMode.LIVE
    assert intent.status == IntentStatus.BLOCKED
    assert ("confidence below automation minimum" in intent.reasons) == (confidence < 0.75)
    assert ("research-only strategy cannot trade live" in intent.reasons) == research_only
    assert "no calibration evidence on file" in intent.reasons
    assert "strategy is not explicitly live eligible" in intent.reasons


@pytest.mark.parametrize("overrides", [
    {"research_only": False},
    {"strategy": "OTHER_RESEARCH_STRATEGY"},
    {"module": Module.SWING},
])
def test_simulation_exception_does_not_admit_other_low_confidence_alerts(manager, overrides):
    intent = manager.on_alert(research_alert(**overrides))

    assert intent.status == IntentStatus.BLOCKED
    assert "confidence below automation minimum" in intent.reasons


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"dollar_risk": 250.01}, "per-order risk cap exceeded"),
    ({"symbol": "AAPL"}, "symbol not allowlisted"),
    ({"status": "CANCELLED"}, "alert status is cancelled"),
])
def test_research_admission_keeps_other_alert_guards(manager, overrides, reason):
    intent = manager.on_alert(research_alert(**overrides))

    assert intent.status == IntentStatus.BLOCKED
    assert reason in intent.reasons


def test_expired_research_alert_is_blocked(manager):
    intent = manager.on_alert(research_alert(
        valid_until=datetime.now(timezone.utc) - timedelta(seconds=1),
    ))
    assert intent.status == IntentStatus.BLOCKED
    assert "alert expired" in intent.reasons


@pytest.mark.parametrize("reason", ["market data is stale", "symbol is quarantined"])
def test_research_admission_keeps_symbol_data_health_gate(manager, reason):
    manager.symbol_gate = lambda _symbol: reason
    intent = manager.on_alert(research_alert())

    assert intent.status == IntentStatus.BLOCKED
    assert reason in intent.reasons


@pytest.mark.parametrize(("invalid", "reason"), [
    ({"quote_age_seconds": 31}, "quote_age"),
    ({"volume": 0}, "volume"),
    ({"open_interest": 0}, "open_interest"),
    ({"ask_price": 3.0, "bid_price": 2.95}, "capital"),
    ({"bid_price": 1.0}, "spread"),
])
def test_research_admission_does_not_skip_option_quote_checks(manager, invalid, reason):
    intent = manager.on_alert(research_alert())
    now = datetime.now(timezone.utc)
    candidate = option_candidate("paper-only-test", delta=0.6, ask=2.0)
    candidate["expiration_date"] = now.astimezone(ZoneInfo("America/New_York")).date()
    invalid = dict(invalid)
    if "quote_age_seconds" in invalid:
        candidate["quote_at"] = now - timedelta(seconds=invalid.pop("quote_age_seconds"))
    candidate.update(invalid)

    with pytest.raises(ValueError, match=reason):
        manager.attest_option_selection(
            intent.intent_id, agent="codex", buying_power=1000.0,
            observed_at=now, account_scope="ROBINHOOD_AGENTIC_ONLY",
            candidates=[candidate],
        )
    assert intent.status == IntentStatus.SHADOW
    assert intent.option_selection is None


def test_actual_strategy_engine_and_composer_emit_admissible_capped_alert(manager):
    """Synthetic completed snapshots test wiring, never profitability evidence.

    Use the actual strategy, factor scorer, veto wall, on-disk calibration, and
    Composer. No scoring/gating/confidence function is mocked or promoted.
    """
    strategy = Ema9MtfZeroDte()
    runner = EngineRunner(["SPY"], strategies=[strategy])
    state = runner.states["SPY"]
    now = datetime(2026, 7, 20, 10, 35, tzinfo=ZoneInfo("America/New_York"))
    state.last_bar = Bar(
        symbol="SPY", timeframe=Timeframe.M5, ts_close=now,
        open=100.0, high=101.2, low=99.9, close=101.0,
        volume=1_000_000, source="test",
    )
    state.recent_5m = [
        state.last_bar.model_copy(update={"ts_close": now - timedelta(minutes=5)}),
        state.last_bar,
    ]
    state.session_id = "2026-07-20"
    for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.D1):
        state.trend[tf].last_indicators = IndicatorSnapshot(
            symbol="SPY", timeframe=tf, ts_close=now, close=101.0,
            ema9=100.2, ema21=99.7, ema50=99.0, ema200=98.0,
            rsi14=62.0, macd=0.3, macd_signal=0.2, macd_histogram=0.1,
            atr14=4.0 if tf == Timeframe.D1 else 0.8,
            adx14=30.0, di_plus=30.0, di_minus=10.0,
            vwap=100.1, rel_volume=2.0,
        )
        state.trend[tf].snapshot = TrendSnapshot(
            symbol="SPY", timeframe=tf, score=100.0,
            state=TrendState.STRONG_UP, components={},
        )
    state.levels._levels.append(Level(
        price=101.0, width=0.25, role=LevelRole.SUPPORT,
        touches=3, flipped=True, last_touch=now - timedelta(days=1),
        source_tf=Timeframe.D1,
    ))

    signal = strategy.evaluate(state)
    assert signal is not None and signal.live_eligible is False
    assert runner._score_and_gate(state, signal) is None
    assert not runner.suppressed
    [setup] = runner.pop_shadow_setups()
    assert setup.confidence == 0.74 and setup.research_only
    alert = Composer(Budgets()).compose(setup)
    assert alert is not None and alert.research_only
    assert alert.status == "SHADOW" and alert.confidence == 0.74
    intent = manager.on_alert(alert, now=now)

    assert intent.mode == AutomationMode.SIMULATION
    assert intent.status == IntentStatus.SHADOW
    assert intent.confidence == 0.74
    assert not intent.reasons
    assert manager.policy.min_confidence == 0.75
    assert runner.calibration[strategy.key].meta["live_eligible"] is False
