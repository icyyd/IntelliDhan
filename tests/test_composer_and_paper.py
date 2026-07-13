"""Composer sizing/voice tests + paper-executor lifecycle tests."""

from datetime import datetime, timedelta, timezone

import pytest

from intellidhan_engine.composer import Budgets, Composer
from intellidhan_engine.voice import VoiceError, complement_line, lint
from intellidhan_learning.paper import Outcome, PaperExecutor, PaperTrade, performance_report
from intellidhan_schemas import Bar, Timeframe
from intellidhan_schemas.signals import Direction, Module, Setup

T0 = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


def mk_setup(entry=100.0, stop=98.0, targets=(102.0, 104.0, 107.0), module=Module.ZDTE,
             direction=Direction.LONG, conf=0.78) -> Setup:
    return Setup(
        setup_id="stp_test", module=module, strategy="ORB_BREAKOUT", symbol="QQQ",
        direction=direction, trigger_tf=Timeframe.M5, ts=T0,
        mtf_matrix={"5m": 80.0, "15m": 70.0}, factors={"F1_trend": 80.0},
        composite=82.0, confidence=conf, entry_underlying=entry, stop_underlying=stop,
        targets_underlying=list(targets), reward_risk=2.0,
        explain="Test breakout with 2-candle confirmation.",
        invalidation="2 closes back below mid.",
    )


def test_voice_linter_blocks_prediction_language():
    with pytest.raises(VoiceError):
        lint("QQQ will rally hard into the close")
    with pytest.raises(VoiceError):
        lint("This is a guaranteed winner")
    assert lint("78% historical edge; breakout confirmed") is not None
    assert "loses ~22%" in complement_line(0.78)


def test_equity_sizing_respects_risk_and_capital(tmp_path):
    budgets = Budgets("config/budgets.yaml")
    composer = Composer(budgets, option_selector=None)
    alert = composer.compose(mk_setup())
    # 0DTE: $4,000 × 25% = $1,000 risk budget; $2/share risk -> 500 shares,
    # but 500 × $100 = $50,000 > $4,000 capital -> capped to 40 shares
    assert alert.equity_qty == 40
    assert alert.capital_required == pytest.approx(4000.0)
    assert alert.dollar_risk == pytest.approx(80.0)
    assert "risk $80" in alert.budget_note
    assert "loses ~22%" in alert.thesis            # complement always present
    assert alert.valid_until == T0 + timedelta(minutes=10)


def test_zero_fit_suppresses_not_stretches():
    budgets = Budgets("config/budgets.yaml")
    composer = Composer(budgets, option_selector=None)
    # entry so expensive a single share exceeds module capital -> no alert (G6)
    alert = composer.compose(mk_setup(entry=999999.0, stop=999990.0))
    assert alert is None


def test_drawdown_multiplier_halves_size():
    budgets = Budgets("config/budgets.yaml")
    composer = Composer(budgets, option_selector=None)
    full = composer.compose(mk_setup(entry=50.0, stop=49.0))
    composer.drawdown_multiplier = 0.5
    half = composer.compose(mk_setup(entry=50.0, stop=49.0))
    assert half.dollar_risk <= full.dollar_risk * 0.55  # anti-martingale (RULE-C3)


def test_plan_identity_is_stable_when_replay_sequence_shifts():
    budgets = Budgets("config/budgets.yaml")
    first_boot = Composer(budgets, option_selector=None)
    first_alert = first_boot.compose(mk_setup())

    shifted_boot = Composer(budgets, option_selector=None)
    for index in range(7):
        shifted_boot.compose(mk_setup().model_copy(update={
            "ts": T0 - timedelta(minutes=5 * (index + 1)),
            "symbol": "SPY",
        }))
    replayed_alert = shifted_boot.compose(mk_setup())

    assert replayed_alert.alert_id == first_alert.alert_id
    assert replayed_alert.plan_key == first_alert.plan_key
    durable = PaperTrade.from_alert(first_alert, mk_setup())
    durable.alert_id = "alr_legacy_process_sequence_1"
    durable.plan_key = None
    durable.outcome = Outcome.STOPPED
    durable.realized_r = -1.0
    durable.exit_ts = T0 + timedelta(minutes=5)
    executor = PaperExecutor()
    executor.restore([durable])
    assert executor.track(PaperTrade.from_alert(replayed_alert, mk_setup())) is False
    assert len(executor.trades) == 1
    assert executor.trades[0].outcome == Outcome.STOPPED


# ---------- paper executor ----------

def bar(i, o, h, lo, c, sym="QQQ") -> Bar:
    return Bar(symbol=sym, timeframe=Timeframe.M5, ts_close=T0 + timedelta(minutes=5 * (i + 1)),
               open=o, high=h, low=lo, close=c, volume=1000, source="fx")


def mk_trade(**kw) -> PaperTrade:
    budgets = Budgets("config/budgets.yaml")
    composer = Composer(budgets, option_selector=None)
    setup = mk_setup(**kw)
    alert = composer.compose(setup)
    return PaperTrade.from_alert(alert, setup)


def test_paper_full_winner_path():
    ex = PaperExecutor()
    ex.track(mk_trade(module=Module.SWING))  # swing validity: no same-day flatten
    ex.on_bar(bar(0, 99.5, 100.5, 99.0, 100.2))            # fills at 100
    ex.on_bar(bar(1, 100.2, 102.5, 100.0, 102.2))          # T1 (102)
    ex.on_bar(bar(2, 102.2, 104.5, 101.8, 104.2))          # T2 (104)
    settled = ex.on_bar(bar(3, 104.2, 107.5, 104.0, 107.2))  # T3 (107)
    t = settled[0]
    assert t.outcome == Outcome.TP_FULL
    # R = 0.33*1 + 0.33*2 + 0.34*3.5 = 2.18
    assert t.realized_r == pytest.approx(2.18, abs=0.01)
    assert t.mfe_r > 3.0


def test_paper_stop_before_target_same_bar_is_pessimistic():
    ex = PaperExecutor()
    ex.track(mk_trade(module=Module.SWING))
    ex.on_bar(bar(0, 100.0, 100.4, 99.8, 100.1))           # fill
    settled = ex.on_bar(bar(1, 100, 103.0, 97.5, 99.0))    # touches T1 AND stop -> stop wins
    assert settled[0].outcome == Outcome.STOPPED
    assert settled[0].realized_r == pytest.approx(-1.0, abs=0.01)


def test_paper_breakeven_ratchet_after_t1():
    ex = PaperExecutor()
    ex.track(mk_trade(module=Module.SWING))
    ex.on_bar(bar(0, 99.9, 100.3, 99.7, 100.0))            # fill
    ex.on_bar(bar(1, 100.0, 102.4, 99.9, 102.1))           # T1 -> stop moves to entry
    settled = ex.on_bar(bar(2, 102.0, 102.2, 99.9, 100.0))  # returns to entry -> BE stop
    t = settled[0]
    assert t.outcome == Outcome.STOPPED_AFTER_BE
    # 0.33 tranche banked at +1R, rest flat: ≈ +0.33R
    assert t.realized_r == pytest.approx(0.33, abs=0.01)


def test_paper_expires_unfilled():
    ex = PaperExecutor()
    ex.track(mk_trade())  # 0DTE: 10-minute validity
    settled = ex.on_bar(bar(4, 104, 105, 103.5, 104.5))    # never traded near 100; past validity
    assert settled[0].outcome == Outcome.EXPIRED_UNFILLED


def test_performance_report_math():
    ex = PaperExecutor()
    for outcome_bars in (
        [bar(0, 99.5, 100.5, 99.0, 100.2), bar(1, 100, 103, 97.5, 98)],   # stopped -1R
        [bar(0, 99.5, 100.5, 99.0, 100.2), bar(1, 100.2, 102.5, 100, 102.2),
         bar(2, 102.2, 104.5, 101.8, 104.2), bar(3, 104.2, 107.5, 104.0, 107.2)],  # +2.18R
    ):
        ex2 = PaperExecutor()
        ex2.track(mk_trade(module=Module.SWING))
        for b in outcome_bars:
            ex2.on_bar(b)
        ex.trades.extend(ex2.trades)
    report = performance_report(ex.trades)
    assert report["decided"] == 2
    assert report["win_rate"] == 0.5
    assert report["profit_factor"] == pytest.approx(2.18, abs=0.01)
