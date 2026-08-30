import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from job_ftch.application.builder import PipelineBuilder
from job_ftch.domain import RawItem, SourceKind, source_spec_identifier
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.composite import (
    CompositeSource,
    SourceFetchResult,
    _source_identity,
)


class FakeSource:
    def __init__(self, items: list[RawItem], fail: bool = False):
        self.items = items
        self.fail = fail

    async def fetch(self) -> AsyncIterator[RawItem]:
        if self.fail:
            raise RuntimeError("Fake failure")
        for item in self.items:
            yield item


class TrackingSource:
    def __init__(
        self,
        source_id: str,
        counters: dict[str, int],
        started: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._source_id = source_id
        self._counters = counters
        self._started = started
        self._release = release

    async def fetch(self) -> AsyncIterator[RawItem]:
        self._counters["active"] += 1
        self._counters["max_active"] = max(self._counters["max_active"], self._counters["active"])
        self._started.set()
        try:
            await self._release.wait()
            yield build_item(self._source_id)
        finally:
            self._counters["active"] -= 1


class ClosableSource:
    def __init__(self, external_id: str, closed: asyncio.Event) -> None:
        self._external_id = external_id
        self._closed = closed

    async def fetch(self) -> AsyncIterator[RawItem]:
        try:
            yield build_item(self._external_id)
            await asyncio.Future()
        finally:
            self._closed.set()


class ContextSource:
    def __init__(self) -> None:
        self.spec = CareerSiteSpec(url="https://example.com/jobs", source_name="example_jobs")
        self.context: dict[str, object] = {}

    async def fetch(self) -> AsyncIterator[RawItem]:
        from structlog.contextvars import get_contextvars

        self.context = get_contextvars()
        yield build_item("context")


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
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_source_fetch_binds_canonical_observability_context(concurrency: int) -> None:
    source = ContextSource()

    items = [item async for item in CompositeSource([source], concurrency=concurrency).fetch()]
    assert len(items) == 1
    assert items[0].source_name == "example_jobs"
    assert items[0].stable_id != build_item("context").stable_id
    assert source.context["source_id"] == "career_site:example_jobs"
    assert source.context["source_kind"] == "career_site"


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
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_adapter_health_marks_technical_zero_as_failure(concurrency: int):
    source = FakeSource([])
    source.stats = SimpleNamespace(
        source_partial=False,
        truncated=False,
        zero_reason="all_monitors_exhausted",
        monitored=0,
        rich_emitted=0,
        scraped=0,
        scrape_fallback_used=0,
        browser_navigations_attempted=3,
        monitor_truncated=0,
    )
    composite = CompositeSource([source], concurrency=concurrency)

    assert [item async for item in composite.fetch()] == []

    result = next(iter(composite.source_results.values()))
    assert result.failed is True
    assert result.error == "source_zero_yield:all_monitors_exhausted"
    assert result.browser_navigations_attempted == 3
    assert composite.failed_sources == 1


@pytest.mark.asyncio
async def test_truncated_success_is_limited_not_partial() -> None:
    source = FakeSource([build_item("1")])
    source.stats = SimpleNamespace(
        source_partial=False,
        truncated=True,
        zero_reason=None,
        monitored=2,
        rich_emitted=0,
        scraped=1,
        scrape_fallback_used=0,
        monitor_truncated=1,
    )
    composite = CompositeSource([source], concurrency=1)

    assert [item async for item in composite.fetch()]

    result = next(iter(composite.source_results.values()))
    assert result.partial is False
    assert result.limited is True
    assert result.terminal_outcome == "parsed_ok"
    assert result.completion_state == "completed_limited"


def test_deadline_preserves_specific_zero_outcome_but_marks_partial() -> None:
    from job_ftch.infrastructure.sources.composite import _capture_source_stats

    source = SimpleNamespace(
        stats=SimpleNamespace(
            source_partial=False,
            truncated=False,
            monitor_truncated=0,
            zero_reason="all_scrapers_failed",
        )
    )
    result = SourceFetchResult(
        source_id="career_site:example",
        source_kind="career_site",
        source_name="example",
        deadline_exceeded=True,
    )

    _capture_source_stats(source, result)

    assert result.terminal_outcome == "detail_extraction_failed"
    assert result.completion_state == "partial"


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


@pytest.mark.asyncio
async def test_parallel_respects_concurrency_cap():
    release = asyncio.Event()
    counters = {"active": 0, "max_active": 0}
    started_events = [asyncio.Event() for _ in range(3)]
    sources = [
        TrackingSource(f"src-{idx}", counters, started_events[idx], release) for idx in range(3)
    ]
    composite = CompositeSource(sources, concurrency=2)

    task = asyncio.create_task(anext(composite.fetch().__aiter__()))
    await asyncio.gather(*(event.wait() for event in started_events[:2]))
    await asyncio.sleep(0)

    assert counters["max_active"] == 2
    assert not started_events[2].is_set()

    release.set()
    first_item = await asyncio.wait_for(task, timeout=1)

    assert first_item.external_id in {"src-0", "src-1", "src-2"}


@pytest.mark.asyncio
async def test_parallel_close_cancels_active_producers():
    closed = asyncio.Event()
    composite = CompositeSource(
        [ClosableSource("1", closed), FakeSource([build_item("2")])],
        concurrency=2,
    )

    iterator = composite.fetch().__aiter__()
    await anext(iterator)
    await iterator.aclose()

    await asyncio.wait_for(closed.wait(), timeout=1)


def test_builder_propagates_source_fetch_concurrency(monkeypatch: pytest.MonkeyPatch):
    created_specs: list[CareerSiteSpec] = []
    created_stores: list[object | None] = []
    store = object()

    def _fake_create_source_from_spec(
        spec: CareerSiteSpec,
        auth: object | None = None,
        store: object | None = None,
    ) -> FakeSource:
        del auth
        created_specs.append(spec)
        created_stores.append(store)
        return FakeSource([build_item(spec.source_name or "item")])

    monkeypatch.setattr(
        "job_ftch.application.builder.create_source_from_spec",
        _fake_create_source_from_spec,
    )
    builder = PipelineBuilder().sources(
        [
            CareerSiteSpec(url="https://example.com/1", source_name="src-1"),
            CareerSiteSpec(url="https://example.com/2", source_name="src-2"),
        ]
    )
    builder.set_source_fetch_concurrency(3)
    builder.store(store)

    source = builder._build_source_from_specs()

    assert isinstance(source, CompositeSource)
    assert source._concurrency == 3
    assert source._dynamic_enabled is True
    assert [spec.source_name for spec in created_specs] == ["src-1", "src-2"]
    assert created_stores == [store, store]


def test_no_sources_raises():
    with pytest.raises(ValueError, match="at least one child source"):
        CompositeSource([])


def test_invalid_concurrency_raises():
    s = FakeSource([build_item("1")])
    with pytest.raises(ValueError, match="concurrency must be"):
        CompositeSource([s], concurrency=0)


def test_source_identity_uses_spec_identifier():
    spec = CareerSiteSpec(url="https://example.com/jobs", source_name="jobs")

    class _SpecSource:
        def __init__(self, spec: CareerSiteSpec) -> None:
            self.spec = spec

    source_id, source_kind, source_name = _source_identity(_SpecSource(spec))

    assert source_id == source_spec_identifier(spec)
    assert source_kind == "career_site"
    assert source_name == "jobs"
