"""Live-network smoke tests. Run explicitly: pytest -m integration"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from intellidhan_ingestor.providers import YahooProvider
from intellidhan_ingestor.sentinel import check_bars
from intellidhan_schemas import DataQuality, Timeframe

pytestmark = pytest.mark.integration


def test_daily_bars_and_quote_roundtrip():
    provider = YahooProvider()
    end = datetime.now(timezone.utc)
    bars = asyncio.run(provider.get_bars("QQQ", Timeframe.D1, end - timedelta(days=30), end))
    assert len(bars) >= 15
    assert all(b.symbol == "QQQ" and b.source == "yahoo" for b in bars)
    assert check_bars("QQQ", bars).quality == DataQuality.OK
    quote = asyncio.run(provider.get_quote("SPX"))  # index alias ^SPX
    assert quote.last > 1000
    assert provider.health.error_rate == 0.0
