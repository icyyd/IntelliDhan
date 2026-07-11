"""Level engine — S/R zones from confirmed pivots (doc 15 §2).

Zones, not lines: width = 0.25 × ATR of the source timeframe. Strength grows
with touch count and decays with age; a broken level flips role (support →
resistance) and keeps its history — flipped levels score HIGHER in confluence
(trapped-trader logic).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from intellidhan_analytics.structure import Pivot, PivotKind
from intellidhan_schemas import Timeframe


class LevelRole(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class Level(BaseModel):
    price: float                 # zone center
    width: float                 # zone half-width
    role: LevelRole
    touches: int
    flipped: bool = False        # broke and swapped role at least once
    last_touch: datetime
    source_tf: Timeframe

    def contains(self, price: float) -> bool:
        return abs(price - self.price) <= self.width

    @property
    def strength(self) -> float:
        """0–100; obviousness gate — weak levels never surface (doc 15 §2)."""
        base = min(self.touches * 25.0, 75.0)
        return min(base + (15.0 if self.flipped else 0.0) + 10.0, 100.0)


class LevelMap:
    def __init__(self, timeframe: Timeframe, min_strength: float = 50.0) -> None:
        self.timeframe = timeframe
        self.min_strength = min_strength
        self._levels: list[Level] = []

    def on_pivot(self, pivot: Pivot, atr: float | None) -> None:
        if atr is None or atr <= 0:
            return
        width = 0.25 * atr
        role = LevelRole.RESISTANCE if pivot.kind == PivotKind.HIGH else LevelRole.SUPPORT
        for lvl in self._levels:
            if abs(pivot.price - lvl.price) <= max(width, lvl.width):
                # merge: re-center toward the new touch, count it
                lvl.price = (lvl.price * lvl.touches + pivot.price) / (lvl.touches + 1)
                lvl.touches += 1
                lvl.last_touch = pivot.ts_close
                lvl.width = max(lvl.width, width)
                return
        self._levels.append(
            Level(price=pivot.price, width=width, role=role, touches=1,
                  last_touch=pivot.ts_close, source_tf=self.timeframe)
        )

    def on_close(self, close: float, ts: datetime) -> None:
        """Role-flip detection: a decisive close through a zone swaps its role."""
        for lvl in self._levels:
            beyond = close > lvl.price + lvl.width if lvl.role == LevelRole.RESISTANCE else (
                close < lvl.price - lvl.width)
            if beyond:
                lvl.role = (
                    LevelRole.SUPPORT if lvl.role == LevelRole.RESISTANCE
                    else LevelRole.RESISTANCE
                )
                lvl.flipped = True
                lvl.last_touch = ts

    def visible(self) -> list[Level]:
        return sorted(
            (lvl for lvl in self._levels if lvl.strength >= self.min_strength),
            key=lambda lvl: lvl.price,
        )

    def nearest(self, price: float, role: LevelRole | None = None) -> Level | None:
        pool = [lvl for lvl in self.visible() if role is None or lvl.role == role]
        return min(pool, key=lambda lvl: abs(lvl.price - price), default=None)

    def confluence_score(self, price: float) -> float:
        """F3 input (doc 03 §3): 0–100 by proximity to the strongest nearby zone."""
        best = 0.0
        for lvl in self.visible():
            dist = abs(price - lvl.price)
            if dist <= lvl.width:
                best = max(best, lvl.strength)
            elif dist <= 3 * lvl.width:
                best = max(best, lvl.strength * (1 - (dist - lvl.width) / (2 * lvl.width)))
        return round(best, 2)
