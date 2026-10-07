"""Horizon selection, research provenance, and premium-loss sizing regressions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from intellidhan_engine.composer import Budgets, Composer
from intellidhan_ingestor.market_clock import MarketClock
from intellidhan_ingestor.providers import yahoo_options
from intellidhan_ingestor.providers.yahoo_options import YahooOptionSelector, bs_delta
from intellidhan_schemas import Timeframe
from intellidhan_schemas.option_policy import eligible_option_expiry, option_dte
from intellidhan_schemas.signals import Direction, Module, OptionLeg, Setup, Vehicle

NOW = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


def setup(module=Module.ZDTE, direction=Direction.LONG):
    return Setup(
        setup_id="stp_option", symbol="SPY", strategy="ORB_BREAKOUT", module=module,
        direction=direction, trigger_tf=Timeframe.M5, ts=NOW,
        mtf_matrix={"5m": 70}, factors={"F1_trend": 70}, composite=80, confidence=0.78,
        entry_underlying=100, stop_underlying=98 if direction == Direction.LONG else 102,
        targets_underlying=[102, 104, 106] if direction == Direction.LONG else [98, 96, 94],
        reward_risk=2, explain="Confirmed breakout.", invalidation="Close beyond the stop.",
    )


def leg(**changes):
    values = {
        "occ_symbol": "SPY260710C00100000", "side": "BUY", "option_type": "CALL",
        "strike": 100, "expiry": "2026-07-10", "delta": 0.4, "iv": 0.25,
    }
    values.update(changes)
    return OptionLeg(**values)


class FixedSelector:
    def __init__(self, option=None, quote=(1.9, 2.0)):
        self.option = option or leg()
        self.quote = quote

    def select(self, _setup):
        return self.option

    def leg_quote(self, _leg):
        return self.quote


@pytest.mark.parametrize(("module", "expected"), [
    (Module.ZDTE, "2026-07-10"),
    (Module.SWING, "2026-08-14"),
    (Module.LEAPS, "2027-07-16"),
    (Module.HODL, None),
])
def test_expiry_selection_respects_horizon_even_for_unsorted_chain(module, expected):
    ticker = SimpleNamespace(options=[
        "2028-01-21", "2026-08-14", "2026-07-10", "2027-07-16", "2026-07-31",
    ])
    assert YahooOptionSelector()._pick_expiry(ticker, module, NOW) == expected


@pytest.mark.parametrize(("module", "expiries"), [
    (Module.ZDTE, ["2026-07-17"]),
    (Module.SWING, ["2026-07-17", "2028-01-21"]),
    (Module.LEAPS, ["2026-08-14"]),
])
def test_missing_horizon_does_not_substitute_other_expiration(module, expiries):
    assert YahooOptionSelector()._pick_expiry(SimpleNamespace(options=expiries), module, NOW) is None


def test_dte_uses_new_york_calendar_not_fractional_utc_days():
    # At 19:30 ET the next day's contract is still 1DTE, not 0DTE.
    late = datetime(2026, 7, 10, 23, 30, tzinfo=timezone.utc)
    assert option_dte("2026-07-11", late) == 1
    # UTC has advanced to Saturday while New York is still Friday.
    assert option_dte("2026-07-11", late + timedelta(hours=2)) == 1
    close = MarketClock().option_expiry_close("2026-07-10")
    assert not eligible_option_expiry(Module.ZDTE, "2026-07-10", late, expiry_close=close)
    assert not eligible_option_expiry(Module.ZDTE, "not-a-date", NOW, expiry_close=None)
    assert not eligible_option_expiry(
        Module.ZDTE, "2026-07-10", NOW.replace(tzinfo=None), expiry_close=close,
    )


def test_expiry_policy_respects_early_close_holiday_and_calendar_horizon():
    selector = YahooOptionSelector()
    after_early_close = datetime(2026, 11, 27, 19, tzinfo=timezone.utc)  # 14:00 ET
    assert selector._pick_expiry(
        SimpleNamespace(options=["2026-11-27"]), Module.ZDTE, after_early_close,
    ) is None
    thanksgiving = datetime(2026, 11, 26, 15, tzinfo=timezone.utc)
    assert selector._pick_expiry(
        SimpleNamespace(options=["2026-11-26"]), Module.ZDTE, thanksgiving,
    ) is None
    assert selector._pick_expiry(
        SimpleNamespace(options=["2028-01-21"]), Module.LEAPS, NOW,
    ) is None  # market calendar has not yet verified 2028 expirations
    alert = Composer(Budgets(), FixedSelector(leg(expiry="2026-11-27"))).compose(
        setup().model_copy(update={"ts": after_early_close}),
    )
    assert alert is None


def install_chain(monkeypatch, rows, expiry="2026-07-10"):
    frame = pd.DataFrame(rows)
    ticker = SimpleNamespace(options=[expiry], option_chain=lambda _expiry: SimpleNamespace(
        calls=frame, puts=frame,
    ))
    monkeypatch.setattr(yahoo_options.yf, "Ticker", lambda _symbol: ticker)


def quote_row(**changes):
    row = {
        "contractSymbol": "SPY260710C00100000", "strike": 100,
        "impliedVolatility": 0.25, "bid": 1.9, "ask": 2.0,
        "openInterest": 1000, "volume": 500,
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("changes", [
    {"bid": 2.1}, {"ask": 3.0}, {"bid": 0}, {"ask": float("inf")},
    {"bid": float("nan")}, {"openInterest": float("nan")},
    {"volume": float("nan")}, {"impliedVolatility": float("nan")},
    {"strike": float("inf")}, {"bid": "invalid"},
])
def test_selector_rejects_malformed_or_untradeable_quotes(monkeypatch, changes):
    install_chain(monkeypatch, [quote_row(**changes)])
    monkeypatch.setattr(yahoo_options, "bs_delta", lambda *_args: 0.375)
    assert YahooOptionSelector(clock=lambda: NOW).select(setup()) is None


def test_leaps_uses_long_horizon_delta_and_explicit_research_provenance(monkeypatch):
    install_chain(monkeypatch, [
        quote_row(strike=100), quote_row(strike=80, contractSymbol="SPY270716C00080000"),
    ], expiry="2027-07-16")
    monkeypatch.setattr(yahoo_options, "bs_delta", lambda _spot, strike, *_args:
                        0.8 if strike == 80 else 0.375)
    option = YahooOptionSelector(clock=lambda: NOW).select(setup(Module.LEAPS))
    assert option.strike == 80
    assert option.delta == 0.8
    assert option.research_only is True
    assert option.quote_source == "YAHOO_RESEARCH"
    assert option.delta_source == "BLACK_SCHOLES_ESTIMATE"


def test_cached_quote_expires_and_cannot_be_reused_indefinitely(monkeypatch):
    install_chain(monkeypatch, [quote_row()])
    monkeypatch.setattr(yahoo_options, "bs_delta", lambda *_args: 0.375)
    now = [NOW]
    selector = YahooOptionSelector(clock=lambda: now[0])
    option = selector.select(setup())
    assert selector.leg_quote(option) == (1.9, 2.0)
    now[0] += timedelta(seconds=31)
    assert selector.leg_quote(option) is None
    assert not selector._chain_cache


def test_same_day_delta_uses_actual_half_day_time_remaining(monkeypatch):
    install_chain(monkeypatch, [quote_row()], expiry="2026-11-27")
    before_close = datetime(2026, 11, 27, 17, tzinfo=timezone.utc)  # noon ET
    years = []

    def record_delta(_spot, _strike, t_years, *_args):
        years.append(t_years)
        return 0.375

    monkeypatch.setattr(yahoo_options, "bs_delta", record_delta)
    selector = YahooOptionSelector(clock=lambda: before_close)
    assert selector.select(setup().model_copy(update={"ts": before_close})) is not None
    assert years == [pytest.approx(1 / (365.25 * 24))]


@pytest.mark.parametrize("quote", [
    (2.1, 2), (0, 2), (float("nan"), 2), (1, float("inf")), (1, 2),
])
def test_composer_rechecks_quote_integrity_even_for_external_selector(quote):
    alert = Composer(Budgets(), FixedSelector(quote=quote)).compose(setup())
    assert alert.vehicle == Vehicle.EQUITY


@pytest.mark.parametrize("changes", [
    {"expiry": "2027-07-16"}, {"side": "SELL"}, {"option_type": "PUT"},
    {"delta": -0.4}, {"delta": None}, {"delta": float("nan")}, {"delta": 1.2},
])
def test_composer_does_not_build_options_with_wrong_horizon_or_direction(changes):
    alert = Composer(Budgets(), FixedSelector(leg(**changes))).compose(setup())
    assert alert.vehicle == Vehicle.EQUITY


def test_option_size_reserves_full_premium_not_linear_stop_estimate():
    budgets = Budgets()
    composer = Composer(budgets, FixedSelector())
    alert = composer.compose(setup())
    assert alert.vehicle == Vehicle.OPTION
    assert alert.contracts == 5
    assert alert.dollar_risk == alert.capital_required == 985
    assert alert.dollar_risk <= budgets.capital(Module.ZDTE) * budgets.risk_cap(Module.ZDTE)
    assert alert.stop_est_vehicle < alert.entry_limit
    assert "Full premium" in " ".join(alert.risks)
    assert "targets describe the underlying" in " ".join(alert.risks)
    composer.drawdown_multiplier = 0.5
    reduced = composer.compose(setup())
    assert reduced.contracts == 2
    assert reduced.dollar_risk <= 500


def test_option_single_contract_exceeding_risk_budget_is_suppressed():
    composer = Composer(Budgets(), FixedSelector(quote=(11.9, 12)))
    assert composer.compose(setup()) is None


def test_research_chain_cannot_promote_an_active_setup_to_live_alert():
    composer = Composer(Budgets(), FixedSelector(leg(research_only=True)))
    alert = composer.compose(setup())
    assert alert.vehicle == Vehicle.OPTION
    assert alert.research_only is True
    assert alert.status == "SHADOW"
    assert "quote freshness is unverified" in " ".join(alert.risks)


def test_black_scholes_rejects_nonfinite_inputs():
    assert bs_delta(100, 100, 0.5, float("nan"), True) == 0
    assert bs_delta(float("inf"), 100, 0.5, 0.3, True) == 0
