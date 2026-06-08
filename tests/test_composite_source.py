from collections.abc import AsyncIterator

import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.sources.composite import CompositeSource


class FakeSource:
    def __init__(self, items: list[RawItem], fail: bool = False):
        self.items = items
        self.fail = fail

    async def fetch(self) -> AsyncIterator[RawItem]:
        if self.fail:
            raise RuntimeError("Fake failure")
        for item in self.items:
            yield item


def build_item(id_: str) -> RawItem:
    return RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fake",
        external_id=id_,
        url=f"http://fake.com/{id_}",
        text=f"text {id_}",
        metadata={},
    )


@pytest.mark.asyncio
async def test_sequential_ordering():
    s1 = FakeSource([build_item("1"), build_item("2")])
    s2 = FakeSource([build_item("3")])
    composite = CompositeSource([s1, s2], concurrency=1)

    results = [item async for item in composite.fetch()]
    assert [r.external_id for r in results] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_sequential_child_failure_isolation():
    s1 = FakeSource([], fail=True)
    s2 = FakeSource([build_item("2")])
    composite = CompositeSource([s1, s2], concurrency=1)

    results = [item async for item in composite.fetch()]
    assert [r.external_id for r in results] == ["2"]
    assert composite.failed_sources == 1


@pytest.mark.asyncio
async def test_sequential_empty_child():
    s1 = FakeSource([])
    s2 = FakeSource([build_item("2")])
    composite = CompositeSource([s1, s2], concurrency=1)

    results = [item async for item in composite.fetch()]
    assert [r.external_id for r in results] == ["2"]
    assert composite.failed_sources == 0


@pytest.mark.asyncio
async def test_parallel_order_independence():
    s1 = FakeSource([build_item("1"), build_item("2")])
    s2 = FakeSource([build_item("3")])
    composite = CompositeSource([s1, s2], concurrency=2)

    results = [item async for item in composite.fetch()]
    # Order not guaranteed in parallel, but all must be present
    ids = sorted([r.external_id for r in results])
    assert ids == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_parallel_error_isolation():
    s1 = FakeSource([], fail=True)
    s2 = FakeSource([build_item("2")])
    composite = CompositeSource([s1, s2], concurrency=2)

    results = [item async for item in composite.fetch()]
    assert [r.external_id for r in results] == ["2"]
    assert composite.failed_sources == 1


def test_no_sources_raises():
    with pytest.raises(ValueError, match="at least one child source"):
        CompositeSource([])


def test_invalid_concurrency_raises():
    s = FakeSource([build_item("1")])
    with pytest.raises(ValueError, match="concurrency must be"):
        CompositeSource([s], concurrency=0)
