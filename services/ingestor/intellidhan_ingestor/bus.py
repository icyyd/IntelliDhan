"""Event bus — Redis Streams with typed payloads (doc 01 §1).

Topics follow the doc 01 §1.1 taxonomy (md.bar.{tf}, state.indicators.{symbol}.{tf}, …).
At-least-once delivery; consumers are idempotent by payload identity. An in-memory
implementation backs unit tests and single-process replay so determinism never
depends on a network hop.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import AsyncIterator, Protocol, Type, TypeVar

import redis.asyncio as aioredis
from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

MAXLEN = 100_000  # per-stream cap; history persistence is TimescaleDB's job, not Redis's


def bar_topic(tf: str) -> str:
    return f"md.bar.{tf}"


def indicator_topic(symbol: str, tf: str) -> str:
    return f"state.indicators.{symbol}.{tf}"


class Bus(Protocol):
    async def publish(self, topic: str, payload: BaseModel) -> str: ...

    async def read(
        self, topic: str, model: Type[M], last_id: str = "0-0", count: int = 1000
    ) -> list[tuple[str, M]]: ...


class RedisBus:
    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        self._r = aioredis.from_url(url, decode_responses=True)

    async def publish(self, topic: str, payload: BaseModel) -> str:
        return await self._r.xadd(
            topic, {"json": payload.model_dump_json()}, maxlen=MAXLEN, approximate=True
        )

    async def read(
        self, topic: str, model: Type[M], last_id: str = "0-0", count: int = 1000
    ) -> list[tuple[str, M]]:
        entries = await self._r.xrange(topic, min=f"({last_id}" if last_id != "0-0" else "-",
                                       max="+", count=count)
        return [(eid, model.model_validate_json(fields["json"])) for eid, fields in entries]

    async def stream_len(self, topic: str) -> int:
        return await self._r.xlen(topic)

    async def subscribe(
        self, topic: str, model: Type[M], last_id: str = "$", block_ms: int = 5000
    ) -> AsyncIterator[tuple[str, M]]:
        """Tail a topic forever (live consumers)."""
        cursor = last_id
        while True:
            resp = await self._r.xread({topic: cursor}, count=100, block=block_ms)
            for _, entries in resp:
                for eid, fields in entries:
                    cursor = eid
                    yield eid, model.model_validate_json(fields["json"])

    async def aclose(self) -> None:
        await self._r.aclose()


class MemoryBus:
    """Deterministic in-process bus for unit tests and replay (doc 01 §7 determinism)."""

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self._seq = 0

    async def publish(self, topic: str, payload: BaseModel) -> str:
        self._seq += 1
        eid = f"{self._seq}-0"
        self._streams[topic].append((eid, payload.model_dump_json()))
        return eid

    async def read(
        self, topic: str, model: Type[M], last_id: str = "0-0", count: int = 1000
    ) -> list[tuple[str, M]]:
        floor = int(last_id.split("-")[0])
        out = []
        for eid, raw in self._streams[topic]:
            if int(eid.split("-")[0]) > floor:
                out.append((eid, model.model_validate_json(raw)))
                if len(out) >= count:
                    break
        return out

    def topics(self) -> list[str]:
        return sorted(self._streams)

    def dump(self, topic: str) -> list[dict]:
        return [json.loads(raw) for _, raw in self._streams[topic]]
