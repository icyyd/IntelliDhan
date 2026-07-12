"""Market Profile / auction-theory layer (doc 16).

TPO profiles from 5m bars bucketed into 30-min periods (letters), per-price
volume alongside. Emits: POC, 70% value area, Initial Balance, open-type
classification, one-timeframing state, day-type probabilities, p/b shape —
the veto inputs of doc 16 §2. Deterministic and replay-safe like every
analytics node.
"""

from __future__ import annotations

from datetime import datetime, time
from enum import Enum

from pydantic import BaseModel

from intellidhan_ingestor.market_clock import ET
from intellidhan_schemas import Bar


class OpenType(str, Enum):
    OPEN_DRIVE = "OPEN_DRIVE"
    OPEN_TEST_DRIVE = "OPEN_TEST_DRIVE"
    OPEN_REJECTION_REVERSE = "OPEN_REJECTION_REVERSE"
    OPEN_AUCTION = "OPEN_AUCTION"
    UNKNOWN = "UNKNOWN"


class Shape(str, Enum):
    P_SHAPE = "P_SHAPE"          # short covering — fat upper bulge
    B_SHAPE = "B_SHAPE"          # long liquidation — fat lower bulge
    ELONGATED = "ELONGATED"      # initiative trend profile
    BALANCED = "BALANCED"


class OneTimeframing(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"


class ProfileState(BaseModel):
    symbol: str
    session: str
    period_count: int
    poc: float | None = None
    poc_prominent: bool = False
    va_high: float | None = None
    va_low: float | None = None
    ib_high: float | None = None
    ib_low: float | None = None
    open_price: float | None = None
    open_type: OpenType = OpenType.UNKNOWN
    one_timeframing: OneTimeframing = OneTimeframing.NONE
    shape: Shape = Shape.BALANCED
    trend_day_probability: float = 0.0
    range_ext_up: bool = False
    range_ext_down: bool = False


class ProfileBuilder:
    """One per symbol; feed 5m bars in order. Tick size adapts to price scale."""

    PERIODS = [(time(9, 30 + 0), time(10, 0)), ]  # computed dynamically below

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._session: str | None = None
        self._reset("", None)

    def _reset(self, session: str, first_bar: Bar | None) -> None:
        self._session = session
        self._tpo: dict[float, set[int]] = {}
        self._vol: dict[float, float] = {}
        self._period_hl: list[tuple[float, float]] = []   # per 30-min period
        self._cur_period: int | None = None
        self._cur_hi: float | None = None
        self._cur_lo: float | None = None
        self._open: float | None = first_bar.open if first_bar else None
        self._open_crossed_back = False
        self._first_two_dir = 0
        self._day_hi: float | None = None
        self._day_lo: float | None = None
        self._closes: list[float] = []
        self._tick = 0.0

    @staticmethod
    def _period_index(ts: datetime) -> int:
        local = ts.astimezone(ET)
        minutes = (local.hour - 9) * 60 + local.minute - 30
        return max(0, (minutes - 1) // 30)

    def update(self, bar: Bar, session_id: str) -> ProfileState:
        if session_id != self._session:
            self._reset(session_id, bar)
            self._tick = max(round(bar.close * 0.0005, 2), 0.01)
        period = self._period_index(bar.ts_close)
        if period != self._cur_period:
            if self._cur_period is not None and self._cur_hi is not None:
                self._period_hl.append((self._cur_hi, self._cur_lo))
            self._cur_period, self._cur_hi, self._cur_lo = period, bar.high, bar.low
        else:
            self._cur_hi = max(self._cur_hi, bar.high)
            self._cur_lo = min(self._cur_lo, bar.low)
        # TPO + volume per price tick (volume spread uniformly across the bar's range)
        lo_t = round(bar.low / self._tick) * self._tick
        hi_t = round(bar.high / self._tick) * self._tick
        n_ticks = max(int(round((hi_t - lo_t) / self._tick)) + 1, 1)
        for i in range(n_ticks):
            px = round(lo_t + i * self._tick, 4)
            self._tpo.setdefault(px, set()).add(period)
            self._vol[px] = self._vol.get(px, 0.0) + bar.volume / n_ticks
        self._day_hi = bar.high if self._day_hi is None else max(self._day_hi, bar.high)
        self._day_lo = bar.low if self._day_lo is None else min(self._day_lo, bar.low)
        self._closes.append(bar.close)
        if self._open is not None and len(self._period_hl) >= 1:
            # crossed back through the open after the first period?
            if min(bar.low, bar.close) <= self._open <= max(bar.high, bar.close):
                self._open_crossed_back = True
        return self.state()

    # ----- derived state -----

    def state(self) -> ProfileState:
        poc, va_hi, va_lo, prominent = self._value_area()
        completed = self._period_hl + (
            [(self._cur_hi, self._cur_lo)] if self._cur_hi is not None else [])
        ib = completed[:2] if len(completed) >= 2 else None
        return ProfileState(
            symbol=self.symbol, session=self._session or "",
            period_count=len(completed),
            poc=poc, poc_prominent=prominent, va_high=va_hi, va_low=va_lo,
            ib_high=max(h for h, _ in ib) if ib else None,
            ib_low=min(lo for _, lo in ib) if ib else None,
            open_price=self._open,
            open_type=self._classify_open(),
            one_timeframing=self._one_timeframing(completed),
            shape=self._shape(),
            trend_day_probability=self._trend_day_prob(completed),
            range_ext_up=(ib is not None and self._day_hi is not None
                          and self._day_hi > max(h for h, _ in ib)),
            range_ext_down=(ib is not None and self._day_lo is not None
                            and self._day_lo < min(lo for _, lo in ib)),
        )

    def _value_area(self) -> tuple[float | None, float | None, float | None, bool]:
        if not self._vol:
            return None, None, None, False
        prices = sorted(self._vol)
        poc = max(prices, key=lambda p: (self._vol[p], -abs(p - prices[len(prices) // 2])))
        total = sum(self._vol.values())
        target = total * 0.70
        acc = self._vol[poc]
        i = j = prices.index(poc)
        while acc < target and (i > 0 or j < len(prices) - 1):
            up_pair = sum(self._vol[prices[k]] for k in (j + 1, j + 2) if k < len(prices))
            dn_pair = sum(self._vol[prices[k]] for k in (i - 1, i - 2) if k >= 0)
            if up_pair >= dn_pair and j < len(prices) - 1:
                j = min(j + 2, len(prices) - 1)
                acc += up_pair
            elif i > 0:
                i = max(i - 2, 0)
                acc += dn_pair
            else:
                j = min(j + 2, len(prices) - 1)
                acc += up_pair
        mean_vol = total / len(prices)
        prominent = self._vol[poc] > 2.5 * mean_vol
        return poc, prices[j], prices[i], prominent

    def _classify_open(self) -> OpenType:
        if len(self._period_hl) < 2 or self._open is None or not self._closes:
            return OpenType.UNKNOWN
        (h1, l1), (h2, l2) = self._period_hl[0], self._period_hl[1]
        rng1 = h1 - l1
        if rng1 <= 0:
            return OpenType.UNKNOWN
        open_near_low = (self._open - l1) / rng1 <= 0.25
        open_near_high = (h1 - self._open) / rng1 <= 0.25
        directional = (h2 > h1 and l2 >= l1) or (l2 < l1 and h2 <= h1)
        if not self._open_crossed_back and directional and (open_near_low or open_near_high):
            return OpenType.OPEN_DRIVE
        if self._open_crossed_back and directional:
            return OpenType.OPEN_TEST_DRIVE
        if self._open_crossed_back:
            return OpenType.OPEN_REJECTION_REVERSE
        return OpenType.OPEN_AUCTION

    def _one_timeframing(self, periods: list[tuple[float, float]]) -> OneTimeframing:
        if len(periods) < 3:
            return OneTimeframing.NONE
        recent = periods[-4:] if len(periods) >= 4 else periods
        up = all(b[1] >= a[1] for a, b in zip(recent, recent[1:]))       # no lower lows
        down = all(b[0] <= a[0] for a, b in zip(recent, recent[1:]))     # no higher highs
        if up and not down:
            return OneTimeframing.UP
        if down and not up:
            return OneTimeframing.DOWN
        return OneTimeframing.NONE

    def _shape(self) -> Shape:
        if not self._vol or self._day_hi is None or self._day_hi == self._day_lo:
            return Shape.BALANCED
        rng = self._day_hi - self._day_lo
        third = rng / 3
        upper = sum(v for p, v in self._vol.items() if p >= self._day_hi - third)
        lower = sum(v for p, v in self._vol.items() if p <= self._day_lo + third)
        total = sum(self._vol.values())
        if total <= 0:  # zero-volume bars (thin pre-open prints) — no shape read
            return Shape.BALANCED
        # elongation: range vs widest TPO row
        max_tpo = max(len(s) for s in self._tpo.values())
        n_periods = max(len(self._period_hl), 1)
        if max_tpo <= max(2, n_periods * 0.35) and n_periods >= 4:
            return Shape.ELONGATED
        if upper / total >= 0.60:
            return Shape.P_SHAPE
        if lower / total >= 0.60:
            return Shape.B_SHAPE
        return Shape.BALANCED

    def _trend_day_prob(self, periods: list[tuple[float, float]]) -> float:
        if len(periods) < 2 or not self._closes or self._day_hi == self._day_lo:
            return 0.0
        directional = sum(
            1 for a, b in zip(periods, periods[1:])
            if (b[0] > a[0] and b[1] > a[1]) or (b[0] < a[0] and b[1] < a[1])
        ) / max(len(periods) - 1, 1)
        close_pos = (self._closes[-1] - self._day_lo) / (self._day_hi - self._day_lo)
        extreme_close = max(close_pos, 1 - close_pos)  # near either extreme
        elongated = 1.0 if self._shape() == Shape.ELONGATED else 0.4
        return round(min(directional * 0.5 + (extreme_close - 0.5) * 0.6 + elongated * 0.25, 1.0), 3)
