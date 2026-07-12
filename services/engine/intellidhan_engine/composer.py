"""Alert Composer — Setup → complete trade plan (doc 09).

Vehicle selection: an OptionSelector (live Yahoo chains) resolves a contract at
0.30–0.45 delta when available; otherwise the alert ships as EQUITY with the
same underlying levels. Sizing: risk budget = module budget × risk cap ×
drawdown multiplier; zero-fit suppresses rather than stretches risk (G6).
"""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path
from typing import Protocol

import yaml

from intellidhan_engine.voice import complement_line, lint
from intellidhan_schemas.signals import (
    Action,
    Alert,
    Direction,
    Module,
    OptionLeg,
    Setup,
    TakeProfit,
    Vehicle,
)

TRANCHES = (0.33, 0.33, 0.34)
VALIDITY = {Module.ZDTE: timedelta(minutes=10), Module.SWING: timedelta(hours=8),
            Module.LEAPS: timedelta(days=3), Module.HODL: timedelta(days=3)}


class OptionSelector(Protocol):
    def select(self, setup: Setup) -> OptionLeg | None: ...

    def leg_quote(self, leg: OptionLeg) -> tuple[float, float] | None:
        """(bid, ask) or None if unavailable."""


class Budgets:
    """Hot-reloads config/budgets.yaml on mtime change — edits from the web
    settings panel (or by hand) apply to the next alert with no restart."""

    def __init__(self, path: str = "config/budgets.yaml") -> None:
        self.path = Path(path)
        self._mtime: float | None = None
        self._b: dict = {}
        self._load()

    def _load(self) -> None:
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._b = yaml.safe_load(self.path.read_text())["budgets"]
            self._mtime = mtime

    def capital(self, module: Module) -> float:
        self._load()
        b = self._b[module.value]
        return float(b.get("daily_capital") or b.get("standing_capital"))

    def risk_cap(self, module: Module) -> float:
        self._load()
        return float(self._b[module.value]["risk_cap_pct"])

    def as_dict(self) -> dict:
        self._load()
        return self._b

    def update(self, new_budgets: dict) -> None:
        """Validate + persist a full budgets dict, then reload (used by the
        gateway's settings endpoint)."""
        for module_key, cfg in new_budgets.items():
            if module_key not in {m.value for m in Module}:
                raise ValueError(f"unknown module {module_key!r}")
            cap_key = "daily_capital" if "daily_capital" in cfg else "standing_capital"
            capital = float(cfg[cap_key])
            risk_cap = float(cfg["risk_cap_pct"])
            if capital <= 0:
                raise ValueError(f"{module_key}: capital must be positive")
            if not (0 < risk_cap <= 1):
                raise ValueError(f"{module_key}: risk_cap_pct must be in (0, 1]")
        self.path.write_text(yaml.safe_dump({"budgets": new_budgets}, sort_keys=False))
        self._mtime = None
        self._load()


class Composer:
    def __init__(self, budgets: Budgets, option_selector: OptionSelector | None = None) -> None:
        self.budgets = budgets
        self.options = option_selector
        self.drawdown_multiplier = 1.0  # behavior plane will drive this (doc 17 §5)
        self._seq = 0

    def compose(self, setup: Setup) -> Alert | None:
        self._seq += 1
        alert_id = f"alr_{setup.ts.strftime('%Y%m%d_%H%M')}_{setup.symbol.lower()}_{self._seq}"
        risk_budget = (self.budgets.capital(setup.module) * self.budgets.risk_cap(setup.module)
                       * self.drawdown_multiplier)

        leg = self.options.select(setup) if self.options is not None else None
        if leg is not None:
            alert = self._compose_option(alert_id, setup, leg, risk_budget)
        else:
            alert = self._compose_equity(alert_id, setup, risk_budget)
        if alert is not None:
            lint(alert.thesis)
        return alert

    # ----- equity vehicle -----

    def _compose_equity(self, alert_id: str, setup: Setup, risk_budget: float) -> Alert | None:
        per_share_risk = abs(setup.entry_underlying - setup.stop_underlying)
        if per_share_risk <= 0:
            return None
        qty = math.floor(risk_budget / per_share_risk)
        capital = qty * setup.entry_underlying
        # drawdown multiplier shrinks total exposure too (RULE-C3), not just risk
        max_capital = self.budgets.capital(setup.module) * self.drawdown_multiplier
        if capital > max_capital:
            qty = math.floor(max_capital / setup.entry_underlying)
            capital = qty * setup.entry_underlying
        if qty <= 0:
            return None  # G6: never force size
        dollar_risk = qty * per_share_risk
        action = Action.EQUITY_BUY if setup.direction == Direction.LONG else Action.EQUITY_SELL
        zone_pad = per_share_risk * 0.15
        return self._build(
            alert_id, setup, Vehicle.EQUITY, action, [], qty, None,
            entry_limit=round(setup.entry_underlying, 2),
            entry_zone=(round(setup.entry_underlying - zone_pad, 2),
                        round(setup.entry_underlying + zone_pad, 2)),
            stop_est_vehicle=round(setup.stop_underlying, 2),
            capital=round(capital, 2), dollar_risk=round(dollar_risk, 2),
            budget_note=(f"{qty} shares risk ${dollar_risk:,.0f} = "
                         f"{dollar_risk / self.budgets.capital(setup.module):.0%} of "
                         f"${self.budgets.capital(setup.module):,.0f} {setup.module.value} budget"),
        )

    # ----- option vehicle -----

    def _compose_option(
        self, alert_id: str, setup: Setup, leg: OptionLeg, risk_budget: float
    ) -> Alert | None:
        quote = self.options.leg_quote(leg)
        if quote is None:
            return self._compose_equity(alert_id, setup, risk_budget)
        bid, ask = quote
        mid = (bid + ask) / 2
        if mid <= 0 or (ask - bid) / mid > 0.10:  # RULE-T8 liquidity gate
            return self._compose_equity(alert_id, setup, risk_budget)
        entry_limit = round(mid + 0.4 * (ask - mid), 2)  # doc 09 §2 slippage model
        delta = abs(leg.delta or 0.4)
        underlying_risk = abs(setup.entry_underlying - setup.stop_underlying)
        stop_est = max(round(entry_limit - delta * underlying_risk, 2), 0.05)
        per_contract_risk = (entry_limit - stop_est) * 100
        if per_contract_risk <= 0:
            return None
        contracts = math.floor(risk_budget / per_contract_risk)
        capital = contracts * entry_limit * 100
        max_capital = self.budgets.capital(setup.module) * self.drawdown_multiplier
        while contracts > 0 and capital > max_capital:
            contracts -= 1
            capital = contracts * entry_limit * 100
        if contracts <= 0:
            return None
        dollar_risk = contracts * per_contract_risk
        action = Action.BTO
        zone_hi = round(entry_limit * 1.06, 2)
        return self._build(
            alert_id, setup, Vehicle.OPTION, action, [leg], None, contracts,
            entry_limit=entry_limit, entry_zone=(round(mid * 0.98, 2), zone_hi),
            stop_est_vehicle=stop_est,
            capital=round(capital, 2), dollar_risk=round(dollar_risk, 2),
            budget_note=(f"{contracts} contract{'s' if contracts != 1 else ''} risk "
                         f"${dollar_risk:,.0f} = "
                         f"{dollar_risk / self.budgets.capital(setup.module):.0%} of "
                         f"${self.budgets.capital(setup.module):,.0f} {setup.module.value} budget"),
        )

    # ----- shared assembly -----

    def _build(self, alert_id, setup, vehicle, action, legs, qty, contracts, *,
               entry_limit, entry_zone, stop_est_vehicle, capital, dollar_risk,
               budget_note) -> Alert:
        tps = [
            TakeProfit(zone_low=None, zone_high=None, underlying=round(t, 2),
                       tranche=tr, basis=f"target {i + 1}")
            for i, (t, tr) in enumerate(zip(setup.targets_underlying, TRANCHES))
        ]
        management = [
            f"Trim {TRANCHES[0]:.0%} at T1 ({setup.targets_underlying[0]:.2f}), stop → breakeven",
            f"Trim {TRANCHES[1]:.0%} at T2 ({setup.targets_underlying[1]:.2f})",
            "Runner trails the trigger-TF 9EMA",
        ]
        if setup.module == Module.ZDTE:
            management.append("Hard flatten by 15:55 ET")
        thesis = f"{setup.explain} {complement_line(setup.confidence)}"
        return Alert(
            alert_id=alert_id, created_at=setup.ts, module=setup.module,
            strategy=setup.strategy, action=action, symbol=setup.symbol,
            underlying_price=setup.entry_underlying, vehicle=vehicle, legs=legs,
            equity_qty=qty, entry_limit=entry_limit, entry_zone=entry_zone,
            stop_underlying=setup.stop_underlying, stop_est_vehicle=stop_est_vehicle,
            stop_rule=setup.invalidation, take_profits=tps, contracts=contracts,
            capital_required=capital, dollar_risk=dollar_risk,
            reward_risk=setup.reward_risk, budget_note=budget_note,
            confidence=setup.confidence, factors=setup.factors,
            trend_matrix={k: _state_word(v) for k, v in setup.mtf_matrix.items()},
            thesis=thesis, invalidation=setup.invalidation, management=management,
            risks=["Model confidence is uncalibrated (v0) — SHADOW grading in effect"],
            valid_until=setup.ts + VALIDITY[setup.module],
        )


def _state_word(score: float) -> str:
    if score >= 60:
        return "STRONG_UP"
    if score >= 20:
        return "UP"
    if score <= -60:
        return "STRONG_DOWN"
    if score <= -20:
        return "DOWN"
    return "NEUTRAL"
