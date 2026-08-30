from __future__ import annotations

import asyncio

import pytest

from job_ftch.sinks.buffering import BufferSink
from job_ftch.sinks.counted import CountedSink, LimitedSink


@pytest.mark.asyncio
async def test_limited_sink_caps_concurrent_final_emits() -> None:
    inner: BufferSink[int] = BufferSink()
    counted = CountedSink(inner)
    limited = LimitedSink(counted, 2)

    await asyncio.gather(*(limited.emit(value) for value in range(5)))

    assert len(inner.items) == counted.emit_count == 2
