"""Configured universe loading and normalized security metadata.

The live engine and discovery module share this loader so the symbols shown in
the terminal cannot silently drift from the symbols evaluated by the engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_SYMBOLS = [
    "QQQ",
    "SPY",
    "SMH",
    "TQQQ",
    "AAPL",
    "NVDA",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "TSLA",
]


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "universe.yaml"


def load_universe_config(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else _config_path()
    if not target.exists():
        return {}
    return yaml.safe_load(target.read_text()) or {}


def load_live_symbols(path: str | Path | None = None) -> list[str]:
    raw = load_universe_config(path)
    configured = raw.get("live", {}).get("symbols", [])
    symbols = [str(symbol).strip().upper() for symbol in configured if str(symbol).strip()]
    return list(dict.fromkeys(symbols)) or list(DEFAULT_SYMBOLS)


def security_records(path: str | Path | None = None) -> list[dict[str, Any]]:
    raw = load_universe_config(path)
    modules = raw.get("modules", {})
    etfs = {
        str(symbol).upper()
        for symbol in modules.get("0dte", {}).get("etfs", [])
    }
    leveraged = {
        str(symbol).upper()
        for symbol in modules.get("0dte", {}).get("leveraged", [])
    }
    live = load_live_symbols(path)
    return [
        {
            "symbol": symbol,
            "name": symbol,
            "asset_type": "ETF" if symbol in etfs | leveraged else "EQUITY",
            "exchange": None,
            "sector": None,
            "industry": None,
            "active": True,
            "source": "config/universe.yaml",
        }
        for symbol in live
    ]
