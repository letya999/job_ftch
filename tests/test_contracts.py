from __future__ import annotations

from typing import TYPE_CHECKING, Any

from application import LLMProvider, ProcessingNode, SanitizingNode, Sink, Source, Store
from domain import RawItem, SourceKind

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
        self._dedup: set[str] = set()
        self._state: dict[str, str] = {}

    async def has_processed(self, item_id: str) -> bool:
        return item_id in self._processed

    async def mark_processed(self, item_id: str) -> None:
        self._processed.add(item_id)

    async def has_dedup_key(self, key: str) -> bool:
        return key in self._dedup

    async def remember_dedup_key(self, key: str) -> None:
        self._dedup.add(key)

    async def get_run_state(self, key: str) -> str | None:
        return self._state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self._state[key] = value


class MinimalLLMProvider:
    async def extract(self, text: str, schema: type[Any]) -> Any:
        return schema(text=text)


def test_protocol_contracts_runtime_checkable() -> None:
    assert isinstance(MinimalSource(), Source)
    assert isinstance(MinimalSanitizeNode(), SanitizingNode)
    assert isinstance(MinimalProcessingNode(), ProcessingNode)
    assert isinstance(MinimalSink(), Sink)
    assert isinstance(MinimalStore(), Store)
    assert isinstance(MinimalLLMProvider(), LLMProvider)
