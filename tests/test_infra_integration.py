"""Redis + TimescaleDB integration tests. Run explicitly: pytest -m integration
Requires: docker-compose -f deploy/docker-compose.yml up -d  (with deploy/.env)
"""

import asyncio
from pathlib import Path

import pytest

from intellidhan_analytics.replay import replay_session
from intellidhan_ingestor.backfill import read_recording
from intellidhan_ingestor.bus import RedisBus, bar_topic
from intellidhan_ingestor.storage import Storage
from intellidhan_schemas import Bar, Timeframe

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent.parent / "fixtures/golden-sessions/qqq-complex-5m.jsonl"


def test_redis_bus_roundtrip():
    async def run():
        bus = RedisBus()
        bars = read_recording(FIXTURE)[:25]
        topic = "test.md.bar.5m"
        for b in bars:
            await bus.publish(topic, b)
        entries = await bus.read(topic, Bar, count=100)
        await bus._r.delete(topic)
        await bus.aclose()
        return bars, [b for _, b in entries]

    sent, received = asyncio.run(run())
    assert received[-len(sent):] == sent  # payloads survive the wire byte-for-byte


def test_replay_through_real_redis():
    async def run():
        bus = RedisBus()
        # namespace the topics by monkey-wrapping publish
        orig_publish = bus.publish

        async def ns_publish(topic, payload):
            return await orig_publish(f"test.{topic}", payload)

        bus.publish = ns_publish
        _, digest, n = await replay_session(FIXTURE, bus=bus)
        ln = await bus.stream_len(f"test.{bar_topic('5m')}")
        for t in [f"test.{bar_topic('5m')}"]:
            await bus._r.delete(t)
        await bus.aclose()
        return digest, n, ln

    digest, n, stream_len = asyncio.run(run())
    assert n == 1248 and stream_len == 1248
    # same digest as the in-memory replay — transport must not affect results
    _, mem_digest, _ = asyncio.run(replay_session(FIXTURE))
    assert digest == mem_digest


def test_timescale_bars_roundtrip_idempotent():
    storage = Storage()
    storage.init_schema()
    bars = [b for b in read_recording(FIXTURE) if b.symbol == "TQQQ"]
    storage.write_bars(bars)
    storage.write_bars(bars)  # idempotent upsert — no dupes on replay
    stored = storage.read_bars("TQQQ", Timeframe.M5)
    assert stored == sorted(bars, key=lambda b: b.ts_close)
