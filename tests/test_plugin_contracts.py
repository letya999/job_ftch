from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import pytest

from job_ftch.application.contracts import Sink, Source, Store
from job_ftch.domain import JobRecord, RawItem, SourceKind
from job_ftch.infrastructure.stores.in_memory import InMemoryStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def minimal_job() -> JobRecord:
    return JobRecord(
        raw_item_id="raw-1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title="Software Engineer",
        company="Test Corp",
        description="Testing plugins.",
    )


class TestSourcePluginContract(ABC):
    @abstractmethod
    def get_source(self) -> Source[RawItem]:
        """Return the source instance to test."""

    @pytest.mark.asyncio
    async def test_fetch_returns_iterator_of_raw_items(self) -> None:
        source = self.get_source()
        items = []
        async for item in source.fetch():
            if isinstance(item, RawItem):
                items.append(item)
            if len(items) > 10:  # safety break
                break

        for item in items:
            assert isinstance(item, RawItem)
            assert item.source_kind
            assert item.source_name
            assert item.text


class TestSinkPluginContract(ABC):
    @abstractmethod
    def get_sink(self) -> Sink[JobRecord]:
        """Return the sink instance to test."""

    @pytest.mark.asyncio
    async def test_emit_accepts_job_record(self, minimal_job: JobRecord) -> None:
        sink = self.get_sink()
        await sink.emit(minimal_job)

    @pytest.mark.asyncio
    async def test_flush_is_idempotent(self) -> None:
        sink = self.get_sink()
        if hasattr(sink, "flush"):
            await sink.flush()
            await sink.flush()


def test_in_memory_store_satisfies_store_contract() -> None:
    # Example of a concrete test verifying a reference implementation
    store: Store = InMemoryStore()
    assert isinstance(store, Store)
