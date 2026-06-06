from __future__ import annotations

from typing import TYPE_CHECKING, Any

from application import (
    LLMProvider,
    Node,
    NodeOutcome,
    PipelineStage,
    ProcessingContext,
    Sink,
    Source,
    Store,
)
from domain import RawItem, SourceKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping


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
    name = "minimal_sanitize"
    stage = PipelineStage.SANITIZE
    is_sanitize = True

    async def process(self, item: RawItem, context: ProcessingContext) -> NodeOutcome[RawItem]:
        return NodeOutcome.pass_(item)


class MinimalSink:
    def __init__(self) -> None:
        self.items: list[RawItem] = []

    async def emit(self, item: RawItem) -> None:
        self.items.append(item)

    async def finalize(self) -> None:
        return None


class MinimalStore:
    def __init__(self) -> None:
        self._processed: set[str] = set()
        self._dedup: set[str] = set()
        self._state: dict[str, str] = {}

    async def has_processed(self, item_id: str) -> bool:
        return item_id in self._processed

    async def mark_processed(self, item_id: str) -> None:
        await self.try_mark_processed(item_id)

    async def try_mark_processed(self, item_id: str) -> bool:
        if item_id in self._processed:
            return False
        self._processed.add(item_id)
        return True

    async def has_dedup_key(self, key: str) -> bool:
        return key in self._dedup

    async def remember_dedup_key(self, key: str) -> None:
        await self.try_remember_dedup_key(key)

    async def try_remember_dedup_key(
        self,
        key: str,
        *,
        kind: str = "generic",
        item_id: str | None = None,
        reason: str | None = None,
    ) -> bool:
        _ = (kind, item_id, reason)
        if key in self._dedup:
            return False
        self._dedup.add(key)
        return True

    async def get_run_state(self, key: str) -> str | None:
        return self._state.get(key)

    async def set_run_state(self, key: str, value: str) -> None:
        self._state[key] = value

    async def get_source_cursor(self, source_key: str) -> str | None:
        return await self.get_run_state(source_key)

    async def set_source_cursor(self, source_key: str, cursor_value: str) -> None:
        await self.set_run_state(source_key, cursor_value)

    async def save_run_summary(self, run_id: str, payload: Mapping[str, object]) -> None:
        _ = (run_id, payload)

    async def save_rejection(
        self,
        rejection_id: str,
        *,
        run_id: str | None,
        stage: str,
        reason: str,
        payload: Mapping[str, object],
    ) -> None:
        _ = (rejection_id, run_id, stage, reason, payload)


class MinimalLLMProvider:
    async def extract(self, text: str, schema: type[Any]) -> Any:
        return schema(text=text)


def test_protocol_contracts_runtime_checkable() -> None:
    assert isinstance(MinimalSource(), Source)
    assert isinstance(MinimalSanitizeNode(), Node)
    assert isinstance(MinimalSink(), Sink)
    assert isinstance(MinimalStore(), Store)
    assert isinstance(MinimalLLMProvider(), LLMProvider)
