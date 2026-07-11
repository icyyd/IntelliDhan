"""Replay harness — Phase 0 exit criterion (doc 14).

Drives recorded bars through the pipeline exactly as live ingestion would:
recording -> bus (md.bar.{tf}) -> IndicatorEngine -> state.indicators.{symbol}.{tf}.

Determinism contract (doc 01 §7): identical input recordings must produce
byte-identical snapshot streams — asserted via sha256 digest of the ordered
snapshot payloads.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from intellidhan_analytics.indicators import IndicatorEngine
from intellidhan_ingestor.backfill import read_recording
from intellidhan_ingestor.bus import Bus, MemoryBus, bar_topic, indicator_topic
from intellidhan_ingestor.market_clock import MarketClock


async def replay_session(recording: Path, bus: Bus | None = None) -> tuple[Bus, str, int]:
    """Replay a recorded session. Returns (bus, snapshot-stream sha256, snapshot count)."""
    bus = bus or MemoryBus()
    clock = MarketClock()
    engines: dict[tuple[str, str], IndicatorEngine] = {}
    hasher = hashlib.sha256()
    count = 0

    for bar in read_recording(recording):
        await bus.publish(bar_topic(bar.timeframe.value), bar)
        key = (bar.symbol, bar.timeframe.value)
        if key not in engines:
            engines[key] = IndicatorEngine(bar.symbol, bar.timeframe)
        snap = engines[key].update(bar, session_id=clock.session_id(bar.ts_close))
        await bus.publish(indicator_topic(bar.symbol, bar.timeframe.value), snap)
        hasher.update(snap.model_dump_json().encode())
        count += 1

    return bus, hasher.hexdigest(), count
