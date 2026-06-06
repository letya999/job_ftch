"""Ports for the hexagonal application core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from domain import QuarantinedRawItem

SourceItem = TypeVar("SourceItem", covariant=True)
PipelineItem = TypeVar("PipelineItem")
SinkItem = TypeVar("SinkItem", contravariant=True)
ExtractedItem = TypeVar("ExtractedItem")


@runtime_checkable
class Source(Protocol[SourceItem]):
    def fetch(self) -> AsyncIterator[SourceItem | QuarantinedRawItem]:
        """Yield validated input items or quarantined source payloads."""


@runtime_checkable
class PipelineNode(Protocol[PipelineItem]):
    async def process(self, item: PipelineItem) -> PipelineItem | None:
        """Return the item, a transformed item, or None to drop it."""


@runtime_checkable
class SanitizingNode(PipelineNode[PipelineItem], Protocol[PipelineItem]):
    """Mandatory first pipeline step."""


@runtime_checkable
class ProcessingNode(PipelineNode[PipelineItem], Protocol[PipelineItem]):
    """Subsequent pipeline steps after sanitation."""


@runtime_checkable
class Sink(Protocol[SinkItem]):
    async def emit(self, item: SinkItem) -> None:
        """Persist or forward an emitted pipeline item."""


@runtime_checkable
class Store(Protocol):
    async def has_processed(self, item_id: str) -> bool:
        """Check whether the item was already emitted."""

    async def mark_processed(self, item_id: str) -> None:
        """Persist the processed item identifier."""

    async def has_dedup_key(self, key: str) -> bool:
        """Check whether a deduplication key is already known."""

    async def remember_dedup_key(self, key: str) -> None:
        """Persist a deduplication key."""

    async def get_run_state(self, key: str) -> str | None:
        """Read arbitrary run state."""

    async def set_run_state(self, key: str, value: str) -> None:
        """Persist arbitrary run state."""


@runtime_checkable
class LLMProvider(Protocol):
    async def extract(self, text: str, schema: type[ExtractedItem]) -> ExtractedItem:
        """Extract a structured object from text."""
