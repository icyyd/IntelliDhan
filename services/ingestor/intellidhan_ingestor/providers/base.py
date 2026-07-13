"""DataProvider protocol — every feed sits behind this so sources are swappable (doc 02 §2)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from intellidhan_schemas import Bar, Quote, Timeframe


class Capability(str, Enum):
    BARS = "BARS"
    QUOTES = "QUOTES"
    OPTION_CHAINS = "OPTION_CHAINS"
    OPTION_QUOTES = "OPTION_QUOTES"
    FUNDAMENTALS = "FUNDAMENTALS"


class FeedTier(str, Enum):
    """doc 01 §2①: CRITICAL halts alerting when down; ADVISORY never blocks anything."""

    CRITICAL = "CRITICAL"
    CORE = "CORE"
    ADVISORY = "ADVISORY"


class ProviderHealth(BaseModel):
    ok_count: int = 0
    error_count: int = 0
    last_success: datetime | None = None
    last_error: str | None = None

    @property
    def error_rate(self) -> float:
        total = self.ok_count + self.error_count
        return 0.0 if total == 0 else self.error_count / total

    def record_ok(self, ts: datetime) -> None:
        self.ok_count += 1
        self.last_success = ts

    def record_error(self, err: str) -> None:
        self.error_count += 1
        self.last_error = err


@runtime_checkable
class DataProvider(Protocol):
    name: str
    tier: FeedTier
    capabilities: set[Capability]
    health: ProviderHealth

    async def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        adjusted: bool = False,
    ) -> list[Bar]: ...

    async def get_quote(self, symbol: str) -> Quote: ...
