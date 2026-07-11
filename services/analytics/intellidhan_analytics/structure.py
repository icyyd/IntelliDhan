"""Market structure — swing pivots and HH/HL/LH/LL classification (doc 03 §2).

Pivot = fractal high/low with `k` bars on each side. A pivot confirms only after
k subsequent bars close (no lookahead — the confirmation lag is honest and
matches what a live trader sees).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from intellidhan_schemas import Bar


class PivotKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class Pivot(BaseModel):
    kind: PivotKind
    price: float
    ts_close: datetime
    confirmed_at: datetime


class StructureState(str, Enum):
    UPTREND = "UPTREND"          # HH + HL on the last two pivot pairs
    DOWNTREND = "DOWNTREND"      # LH + LL
    RANGE = "RANGE"              # mixed
    UNKNOWN = "UNKNOWN"          # < 4 pivots


class MarketStructure:
    def __init__(self, k: int = 2, max_pivots: int = 60) -> None:
        self.k = k
        self._window: deque[Bar] = deque(maxlen=2 * k + 1)
        self.pivots: deque[Pivot] = deque(maxlen=max_pivots)

    def update(self, bar: Bar) -> Pivot | None:
        self._window.append(bar)
        if len(self._window) < 2 * self.k + 1:
            return None
        mid = self._window[self.k]
        highs = [b.high for b in self._window]
        lows = [b.low for b in self._window]
        new: Pivot | None = None
        if mid.high == max(highs) and highs.count(mid.high) == 1:
            new = Pivot(kind=PivotKind.HIGH, price=mid.high, ts_close=mid.ts_close,
                        confirmed_at=bar.ts_close)
        elif mid.low == min(lows) and lows.count(mid.low) == 1:
            new = Pivot(kind=PivotKind.LOW, price=mid.low, ts_close=mid.ts_close,
                        confirmed_at=bar.ts_close)
        if new is not None:
            # collapse consecutive same-kind pivots to the more extreme one
            if self.pivots and self.pivots[-1].kind == new.kind:
                last = self.pivots[-1]
                keep_new = (new.price > last.price) if new.kind == PivotKind.HIGH else (
                    new.price < last.price)
                if keep_new:
                    self.pivots.pop()
                    self.pivots.append(new)
                else:
                    return None
            else:
                self.pivots.append(new)
        return new

    @property
    def state(self) -> StructureState:
        highs = [p for p in self.pivots if p.kind == PivotKind.HIGH][-2:]
        lows = [p for p in self.pivots if p.kind == PivotKind.LOW][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return StructureState.UNKNOWN
        hh = highs[1].price > highs[0].price
        hl = lows[1].price > lows[0].price
        lh = highs[1].price < highs[0].price
        ll = lows[1].price < lows[0].price
        if hh and hl:
            return StructureState.UPTREND
        if lh and ll:
            return StructureState.DOWNTREND
        return StructureState.RANGE
