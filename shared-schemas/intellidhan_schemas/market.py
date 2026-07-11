"""Market-data payloads flowing on md.* topics. All timestamps are UTC-aware."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1H"
    H4 = "4H"
    D1 = "D"
    W1 = "W"
    MN1 = "M"

    @property
    def seconds(self) -> int:
        return _TF_SECONDS[self]


_TF_SECONDS = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
    Timeframe.MN1: 2592000,
}


class SessionState(str, Enum):
    PRE = "PRE"
    RTH = "RTH"
    POST = "POST"
    CLOSED = "CLOSED"


class DataQuality(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _reject_naive_datetimes(cls, v):
        if isinstance(v, datetime) and v.tzinfo is None:
            raise ValueError("naive datetime rejected — all timestamps must be tz-aware UTC")
        return v


class Bar(_Frozen):
    """One OHLCV bar, keyed by its close time (bar-close semantics, RULE-T4)."""

    symbol: str
    timeframe: Timeframe
    ts_close: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str

    @model_validator(mode="after")
    def _sane_ohlc(self) -> "Bar":
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"OHLC out of order for {self.symbol}@{self.ts_close}")
        if self.volume < 0:
            raise ValueError("negative volume")
        return self


class Quote(_Frozen):
    symbol: str
    ts: datetime
    bid: float | None = None
    ask: float | None = None
    last: float
    source: str

    @property
    def mid(self) -> float:
        if self.bid is not None and self.ask is not None and self.bid > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def crossed(self) -> bool:
        return self.bid is not None and self.ask is not None and self.bid > self.ask


class OptionQuote(_Frozen):
    occ_symbol: str
    underlying: str
    option_type: OptionType
    strike: float
    expiry: datetime
    ts: datetime
    bid: float
    ask: float
    last: float | None = None
    volume: float = 0
    open_interest: float = 0
    delta: float | None = None
    iv: float | None = None
    source: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct_of_mid(self) -> float:
        """Liquidity gate input (RULE-T8)."""
        m = self.mid
        return float("inf") if m <= 0 else (self.ask - self.bid) / m
