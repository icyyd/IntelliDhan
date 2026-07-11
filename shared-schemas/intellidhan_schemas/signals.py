"""Signal-plane payloads: Setup, Alert (doc 03 §6, doc 09 §1)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from intellidhan_schemas.market import Timeframe


class Module(str, Enum):
    ZDTE = "0DTE"
    SWING = "SWING"
    LEAPS = "LEAPS"
    HODL = "HODL"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Action(str, Enum):
    BTO = "BTO"
    STO = "STO"
    EQUITY_BUY = "EQUITY_BUY"
    EQUITY_SELL = "EQUITY_SELL"


class Vehicle(str, Enum):
    OPTION = "OPTION"
    EQUITY = "EQUITY"


class Setup(BaseModel):
    """Raw scored candidate from the strategy registry (doc 03 §6)."""

    model_config = ConfigDict(frozen=True)

    setup_id: str
    module: Module
    strategy: str
    symbol: str
    direction: Direction
    trigger_tf: Timeframe
    ts: datetime
    mtf_matrix: dict[str, float]          # tf.value -> trend score −100..100
    factors: dict[str, float]             # F1..F8 0..100
    composite: float
    confidence: float                     # calibrated 0..1
    entry_underlying: float
    stop_underlying: float
    targets_underlying: list[float]
    reward_risk: float
    explain: str
    invalidation: str


class SuppressedSetup(BaseModel):
    """sig.suppressed payload — the 'why we're quiet' feed (doc 03 §5)."""

    model_config = ConfigDict(frozen=True)

    setup_id: str
    strategy: str
    symbol: str
    ts: datetime
    gate: str
    detail: str
    composite: float | None = None
    confidence: float | None = None


class OptionLeg(BaseModel):
    model_config = ConfigDict(frozen=True)

    occ_symbol: str
    side: str            # BUY | SELL
    option_type: str     # CALL | PUT
    strike: float
    expiry: str          # YYYY-MM-DD
    delta: float | None
    iv: float | None


class TakeProfit(BaseModel):
    model_config = ConfigDict(frozen=True)

    zone_low: float | None
    zone_high: float | None
    underlying: float | None
    tranche: float
    basis: str


class Alert(BaseModel):
    """Complete trade plan — the product (doc 09 §1, v1 subset)."""

    model_config = ConfigDict(frozen=True)

    alert_id: str
    created_at: datetime
    module: Module
    strategy: str
    action: Action
    symbol: str
    underlying_price: float
    vehicle: Vehicle
    legs: list[OptionLeg] = []
    equity_qty: int | None = None
    entry_limit: float
    entry_zone: tuple[float, float]
    stop_underlying: float
    stop_est_vehicle: float
    stop_rule: str
    take_profits: list[TakeProfit]
    contracts: int | None = None
    capital_required: float
    dollar_risk: float
    reward_risk: float
    budget_note: str
    confidence: float
    factors: dict[str, float]
    trend_matrix: dict[str, str]          # tf.value -> TrendState
    thesis: str
    invalidation: str
    management: list[str]
    risks: list[str]
    valid_until: datetime
    status: str = "ACTIVE"
