"""Ports for the hexagonal application core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from application.context import ProcessingContext
    from application.outcomes import NodeOutcome, PipelineStage
    from domain import QuarantinedRawItem

SourceItem = TypeVar("SourceItem", covariant=True)
InT = TypeVar("InT", contravariant=True)
OutT = TypeVar("OutT")
SinkItem = TypeVar("SinkItem", contravariant=True)
ExtractedItem = TypeVar("ExtractedItem")


@runtime_checkable
class Source(Protocol[SourceItem]):
    def fetch(self) -> AsyncIterator[SourceItem | QuarantinedRawItem]:
        """Yield validated input items or quarantined source payloads."""


@runtime_checkable
class Node(Protocol[InT, OutT]):
    name: str
    stage: PipelineStage
    is_sanitize: bool

    async def process(self, item: InT, context: ProcessingContext) -> NodeOutcome[OutT]:
        """Return a structured processing outcome."""


@runtime_checkable
class Sink(Protocol[SinkItem]):
    async def emit(self, item: SinkItem) -> None:
        """Persist or forward an emitted pipeline item."""

    async def finalize(self) -> None:
        """Flush and finalize any pending output."""


@runtime_checkable
class Store(Protocol):
    async def has_processed(self, item_id: str) -> bool:
        """Check whether the item was already emitted."""

    async def mark_processed(self, item_id: str) -> None:
        """Persist the processed item identifier."""

    async def try_mark_processed(self, item_id: str) -> bool:
        """Atomically persist a processed item ID; return False if it already exists."""

    async def has_dedup_key(self, key: str) -> bool:
        """Check whether a deduplication key is already known."""

    async def remember_dedup_key(self, key: str) -> None:
        """Persist a deduplication key."""

    async def try_remember_dedup_key(
        self,
        key: str,
        *,
        kind: str = "generic",
        item_id: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """Atomically persist a dedup key; return False if it already exists."""

    async def get_run_state(self, key: str) -> str | None:
        """Read arbitrary run state."""

    async def set_run_state(self, key: str, value: str) -> None:
        """Persist arbitrary run state."""

    async def get_source_cursor(self, source_key: str) -> str | None:
        """Read a persisted source cursor."""

    async def set_source_cursor(self, source_key: str, cursor_value: str) -> None:
        """Persist a source cursor."""

    async def save_run_summary(self, run_id: str, payload: Mapping[str, object]) -> None:
        """Persist a run summary payload."""

    async def save_rejection(
        self,
        rejection_id: str,
        *,
        run_id: str | None,
        stage: str,
        reason: str,
        payload: Mapping[str, object],
    ) -> None:
        """Persist a rejection payload."""


@runtime_checkable
class LLMProvider(Protocol):
    async def extract(self, text: str, schema: type[ExtractedItem]) -> ExtractedItem:
        """Extract a structured object from text."""
