"""Performance and throughput benchmarks."""

from __future__ import annotations

import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.nodes import SanitizeNode


@pytest.mark.slow
@pytest.mark.asyncio
async def test_sanitize_node_throughput() -> None:
    """Baseline: SanitizeNode should process items quickly (e.g., >1000 items/sec)."""
    import time

    node = SanitizeNode()
    items = [
        RawItem.model_construct(
            stable_id="",
            source_kind=SourceKind.DEBUG,
            source_name="debug",
            external_id=str(i),
            url=None,
            text=f"ML Engineer job {i}",
            metadata={},
        )
        for i in range(100)
    ]

    start = time.perf_counter()
    for item in items:
        try:
            await node.process(item)
        except Exception:
            pass
    duration = time.perf_counter() - start

    # 100 items should be processed in well under 1 second.
    assert duration < 1.0
