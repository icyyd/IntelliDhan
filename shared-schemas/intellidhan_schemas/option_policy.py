"""Deterministic research-option horizons; these are not profitability claims."""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from intellidhan_schemas.signals import Module

MARKET_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OptionHorizon:
    min_dte: int
    max_dte: int
    preferred_min_dte: int
    preferred_max_dte: int
    delta_range: tuple[float, float]


# Keep the existing short-horizon delta profile. LEAPS research uses deeper
# in-the-money exposure and cannot silently inherit the swing expiry window.
OPTION_HORIZONS = {
    Module.ZDTE: OptionHorizon(0, 1, 0, 1, (0.30, 0.45)),
    Module.SWING: OptionHorizon(21, 90, 30, 45, (0.30, 0.45)),
    Module.LEAPS: OptionHorizon(365, 1095, 365, 730, (0.70, 0.90)),
}


def option_dte(expiry: str, as_of: datetime) -> int | None:
    """Calendar DTE in the market's timezone, never rounded timedelta days."""
    if as_of.tzinfo is None:
        return None
    try:
        return (date.fromisoformat(expiry) - as_of.astimezone(MARKET_TZ).date()).days
    except (TypeError, ValueError):
        return None


def eligible_option_expiry(
    module: Module, expiry: str, as_of: datetime, *, expiry_close: datetime | None,
) -> bool:
    policy = OPTION_HORIZONS.get(module)
    dte = option_dte(expiry, as_of)
    if policy is None or dte is None or not policy.min_dte <= dte <= policy.max_dte:
        return False
    # The market-clock owner supplies the authoritative close, including
    # holidays/half-days. Missing calendar coverage fails closed.
    if expiry_close is None or expiry_close.tzinfo is None:
        return False
    return (expiry_close.astimezone(MARKET_TZ).date() == date.fromisoformat(expiry)
            and as_of < expiry_close)
