"""Ports for the hexagonal application core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from domain import DuplicateRecord, Job, JobGroup, QuarantinedRawItem, RememberedDedupKey

SourceItem = TypeVar("SourceItem", covariant=True)
StageInput = TypeVar("StageInput", contravariant=True)
StageOutput = TypeVar("StageOutput", covariant=True)
PipelineItem = TypeVar("PipelineItem")
SinkItem = TypeVar("SinkItem", contravariant=True)
ExtractedItem = TypeVar("ExtractedItem")


@runtime_checkable
class Source(Protocol[SourceItem]):
    def fetch(self) -> AsyncIterator[SourceItem | QuarantinedRawItem]:
        """Yield validated input items or quarantined source payloads."""


@runtime_checkable
class Stage(Protocol[StageInput, StageOutput]):
    async def process(self, item: StageInput) -> StageOutput | None:
        """Return the item, a transformed item, or None to drop it."""


@runtime_checkable
class PipelineNode(Stage[PipelineItem, PipelineItem], Protocol[PipelineItem]):
    """Backward-compatible same-type pipeline stage."""


@runtime_checkable
class SanitizingNode(Stage[PipelineItem, PipelineItem], Protocol[PipelineItem]):
    """Mandatory first pipeline step."""


@runtime_checkable
class ProcessingNode(Stage[PipelineItem, PipelineItem], Protocol[PipelineItem]):
    """Subsequent pipeline steps after sanitation."""


@runtime_checkable
class Sink(Protocol[SinkItem]):
    async def emit(self, item: SinkItem) -> None:
        """Persist or forward an emitted pipeline item."""


@runtime_checkable
class FlushableSink(Sink[SinkItem], Protocol[SinkItem]):
    async def flush(self) -> None:
        """Finalize buffered writes."""


@runtime_checkable
class Store(Protocol):
    async def has_processed(self, item_id: str) -> bool:
        """Check whether the raw item already reached a terminal outcome."""

    async def mark_processed(self, item_id: str) -> None:
        """Persist the raw-item identity after a terminal outcome."""

    async def has_dedup_key(self, key: str) -> bool:
        """Check whether a deduplication key is already known."""

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        """Persist a deduplication key and the item it points to."""

    async def list_dedup_keys(self, kind: str | None = None) -> tuple[RememberedDedupKey, ...]:
        """List remembered deduplication keys, optionally filtered by kind."""

    async def record_duplicate(self, record: DuplicateRecord) -> None:
        """Persist why an item was marked as duplicate."""

    async def list_duplicate_records(self) -> tuple[DuplicateRecord, ...]:
        """List duplicate decisions recorded by the pipeline."""

    async def get_run_state(
        self,
        key: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> str | None:
        """Read arbitrary run state."""

    async def set_run_state(
        self,
        key: str,
        value: str,
        *,
        source_kind: str | None = None,
        source_name: str | None = None,
    ) -> None:
        """Persist arbitrary run state."""


@runtime_checkable
class StoreConnector(Protocol):
    """Universal KV + set connector - any backend (SQL, Redis, filesystem) implements it."""

    async def get(self, key: str) -> str | None:
        """Fetch a string value by key, or None if absent."""

    async def set(self, key: str, value: str) -> None:
        """Upsert a string value by key."""

    async def delete(self, key: str) -> None:
        """Remove a key-value pair. No-op if absent."""

    async def set_add(self, key: str, member: str) -> None:
        """Add a member to the named set. Idempotent."""

    async def set_contains(self, key: str, member: str) -> bool:
        """Return True if member is in the named set."""

    async def set_members(self, key: str) -> frozenset[str]:
        """Return all members of the named set."""

    async def ping(self) -> bool:
        """Return True if the backend is reachable and ready."""


@runtime_checkable
class AuthProvider(Protocol):
    def resolve(self, source_id: str) -> dict[str, str]:
        """Resolve credentials for a source by its auth_source_id."""


@runtime_checkable
class LLMProvider(Protocol):
    async def extract(self, text: str, schema: type[ExtractedItem]) -> ExtractedItem:
        """Extract a structured object from text."""


@runtime_checkable
class JobGroupStore(Protocol):
    async def get_group(self, group_id: str) -> JobGroup | None: ...
    async def create(self, job: Job) -> JobGroup: ...
    async def merge(self, group_id: str, job: Job) -> JobGroup: ...
    async def find_by_url(self, canonical_url: str) -> JobGroup | None: ...
    async def find_by_fingerprint(self, fingerprint: str) -> JobGroup | None: ...
    async def list_groups(self, limit: int = 100) -> list[JobGroup]: ...
    async def count(self) -> int: ...


@runtime_checkable
class JobPersistenceBackend(Protocol):
    async def save(self, job: Job) -> None: ...
    async def get_job(self, job_id: str) -> Job | None: ...
    async def list_jobs(self, limit: int, offset: int) -> list[Job]: ...
    async def delete(self, job_id: str) -> None: ...
    async def ping(self) -> bool: ...


@runtime_checkable
class SearchBackend(Protocol):
    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[JobGroup]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class VectorBackend(Protocol):
    async def upsert(
        self,
        job_id: str,
        vector: list[float],
        payload: dict[str, object],
    ) -> None: ...

    async def search(
        self,
        vector: list[float],
        limit: int,
        filter: dict[str, object] | None = None,
    ) -> list[str]: ...
