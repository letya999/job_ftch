from __future__ import annotations

from typing import TYPE_CHECKING, Any

from application import LLMProvider, ProcessingNode, SanitizingNode, Sink, Source, Stage, Store
from domain import (
    DedupKeyKind,
    DuplicateRecord,
    DuplicateRejectionReason,
    RawItem,
    RememberedDedupKey,
    SourceKind,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class MinimalSource:
    def fetch(self) -> AsyncIterator[RawItem]:
        async def _items() -> AsyncIterator[RawItem]:
            yield RawItem(
                source_kind=SourceKind.DEBUG,
                source_name="contract",
                external_id="1",
                text="contract item",
            )

        return _items()


class MinimalSanitizeNode:
    async def process(self, item: RawItem) -> RawItem | None:
        return item


class MinimalProcessingNode:
    async def process(self, item: RawItem) -> RawItem | None:
        return item


class MinimalSink:
    def __init__(self) -> None:
        self.items: list[RawItem] = []

    async def emit(self, item: RawItem) -> None:
        self.items.append(item)


class MinimalStore:
    def __init__(self) -> None:
        self._processed: set[str] = set()
        self._dedup: dict[str, RememberedDedupKey] = {}
        self._duplicates: list[DuplicateRecord] = []
        self._state: dict[str, str] = {}

    async def has_processed(self, item_id: str) -> bool:
        return item_id in self._processed

    async def mark_processed(self, item_id: str) -> None:
        self._processed.add(item_id)

    async def has_dedup_key(self, key: str) -> bool:
        return key in self._dedup

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        self._dedup[record.key] = record

    async def list_dedup_keys(self, kind: str | None = None) -> tuple[RememberedDedupKey, ...]:
        values = tuple(self._dedup.values())
        if kind is None:
            return values
        return tuple(record for record in values if record.kind.value == kind)

    async def record_duplicate(self, record: DuplicateRecord) -> None:
        self._duplicates.append(record)

    async def list_duplicate_records(self) -> tuple[DuplicateRecord, ...]:
        return tuple(self._duplicates)

    async def get_run_state(
        self,
        key: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> str | None:
        actual_key = key
        if source_kind and source_name:
            actual_key = f"{source_kind}:{source_name}:{key}"
        return self._state.get(actual_key)

    async def set_run_state(
        self,
        key: str,
        value: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> None:
        actual_key = key
        if source_kind and source_name:
            actual_key = f"{source_kind}:{source_name}:{key}"
        self._state[actual_key] = value


class MinimalLLMProvider:
    async def extract(self, text: str, schema: type[Any]) -> Any:
        return schema(text=text)


def test_protocol_contracts_runtime_checkable() -> None:
    assert isinstance(MinimalSource(), Source)
    assert isinstance(MinimalSanitizeNode(), Stage)
    assert isinstance(MinimalSanitizeNode(), SanitizingNode)
    assert isinstance(MinimalProcessingNode(), ProcessingNode)
    assert isinstance(MinimalSink(), Sink)
    assert isinstance(MinimalStore(), Store)
    assert isinstance(MinimalLLMProvider(), LLMProvider)


def test_minimal_store_implements_namespaced_run_state() -> None:
    store = MinimalStore()
    import asyncio

    asyncio.run(store.set_run_state("cursor", "123", source_kind="tg", source_name="chan"))
    assert asyncio.run(store.get_run_state("cursor", source_kind="tg", source_name="chan")) == "123"
    assert asyncio.run(store.get_run_state("cursor")) is None


def test_minimal_store_supports_dedup_records() -> None:
    store = MinimalStore()
    record = RememberedDedupKey(
        key="content:test",
        kind=DedupKeyKind.CONTENT,
        item_id="item-1",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
    )
    duplicate = DuplicateRecord(
        item_id="item-2",
        source_kind=SourceKind.DEBUG,
        source_name="debug",
        reason=DuplicateRejectionReason.DUPLICATE_CONTENT,
        duplicate_key="content:test",
        matched_key="content:test",
        matched_item_id="item-1",
        matched_source_kind=SourceKind.DEBUG,
        matched_source_name="debug",
        details="duplicate",
    )

    import asyncio

    asyncio.run(store.remember_dedup_key(record))
    asyncio.run(store.record_duplicate(duplicate))

    assert asyncio.run(store.has_dedup_key("content:test")) is True
    assert asyncio.run(store.list_dedup_keys(DedupKeyKind.CONTENT.value)) == (record,)
    assert asyncio.run(store.list_duplicate_records()) == (duplicate,)
