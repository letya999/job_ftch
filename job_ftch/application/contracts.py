"""Ports for the hexagonal application core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from job_ftch.application.pipeline import RunSummary
    from job_ftch.domain import (
        DuplicateRecord,
        JobGroup,
        JobRecord,
        QuarantinedRawItem,
        RememberedDedupKey,
        SkillTag,
    )
    from job_ftch.domain.site_models import MonitorResult, ScrapedPostingPayload
    from job_ftch.domain.source_spec import CareerSiteSpec

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
class TypeChangingNode(Stage[StageInput, StageOutput], Protocol[StageInput, StageOutput]):
    """A pipeline node that transitions between two distinct payload types."""


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

    async def get_source_strategy(self, domain: str) -> dict[str, str] | None:
        """Fetch cached scraping strategy for a domain."""

    async def save_source_strategy(self, domain: str, monitor: str, bypass: str) -> None:
        """Cache a successful scraping strategy for a domain."""


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

    async def clear_set(self, key: str) -> None:
        """Remove all members of the named set."""

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


@dataclass(frozen=True)
class ClassificationResult:
    label: str  # PostType string value
    confidence: float  # 0.0-1.0
    model_id: str


@dataclass(frozen=True)
class PluginMetadata:
    """Metadata manifest for any job_ftch plugin."""

    name: str  # unique plugin identifier
    version: str  # semver string
    plugin_type: str  # "source" | "sink" | "extractor" | "classifier" | "normalizer" | "scorer" | "notification_target"
    description: str
    author: str = ""
    requires_extras: tuple[str, ...] = ()  # extras groups needed: ("openai",)
    entry_point_group: str = ""  # e.g. "job_ftch.sources"


@runtime_checkable
class ClassifierProvider(Protocol):
    async def classify(self, text: str) -> ClassificationResult:
        """Classify a single text item. Returns PostType label + confidence."""

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        """Classify multiple texts. Default impl can call classify() in a loop."""

    @property
    def model_id(self) -> str:
        """Identifier for logging and metrics."""


@runtime_checkable
class JobGroupStore(Protocol):
    async def get_group(self, group_id: str) -> JobGroup | None: ...
    async def create(self, job: JobRecord) -> JobGroup: ...
    async def merge(
        self, group_id: str, job: JobRecord, merge_confidence: float = 1.0
    ) -> JobGroup: ...
    async def find_by_url(self, canonical_url: str) -> JobGroup | None: ...
    async def find_by_fingerprint(self, fingerprint: str) -> JobGroup | None: ...
    async def find_by_blocking_key(self, key: str, limit: int = 50) -> list[JobGroup]: ...
    async def list_groups(self, limit: int = 100) -> list[JobGroup]: ...
    async def count(self) -> int: ...


@runtime_checkable
class JobPersistenceBackend(Protocol):
    async def save(self, job: JobRecord) -> None: ...
    async def get_job(self, job_id: str) -> JobRecord | None: ...
    async def list_jobs(self, limit: int, offset: int) -> list[JobRecord]: ...
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
        payload: dict[str, Any],
    ) -> None: ...

    async def search(
        self,
        vector: list[float],
        limit: int,
        filter: dict[str, Any] | None = None,
    ) -> list[str]: ...


@runtime_checkable
class IngestMode(Protocol):
    async def run(
        self,
        source: Source[Any],
        on_item: Callable[[Any], Awaitable[None]],
    ) -> None:
        """Drive source fetch and call on_item for each yielded item."""


@runtime_checkable
class ProxyManager(Protocol):
    def get_proxy(self) -> str | None:
        """Return the next proxy URL from the pool."""


@runtime_checkable
class BypassStrategy(Protocol):
    async def apply_http(self, client: Any) -> Any:
        """Apply bypass to an HTTP client (e.g. swap httpx for curl_cffi)."""

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Modify Playwright launch kwargs (add proxy, stealth flags, executable_path)."""

    async def apply_page(self, page: Any) -> None:
        """Apply bypass to a created Playwright page (stealth scripts, behavior simulation)."""


@runtime_checkable
class BoardMonitor(Protocol):
    """Discovers what jobs exist on a board."""

    async def discover(self, spec: CareerSiteSpec, http: Any) -> MonitorResult: ...


@runtime_checkable
class JobScraper(Protocol):
    """Extracts structured content from a single job URL."""

    async def scrape(
        self, url: str, config: dict[str, Any], http: Any
    ) -> ScrapedPostingPayload | None: ...


class Normalizer(Protocol):
    """Port for ontology-based normalization services."""

    def infer_role_family(self, title: str, language: str = "unknown") -> str | None: ...
    def infer_seniority(self, title: str) -> str | None: ...
    def normalize_skills(self, skills: tuple[SkillTag, ...]) -> tuple[SkillTag, ...]: ...


class MetricsExporter(Protocol):
    """Port for exporting tenant-scoped pipeline metrics."""

    async def observe_run(
        self,
        summary: RunSummary,
        *,
        tenant_id: str,
        job_group_total: int | None = None,
    ) -> None: ...


@runtime_checkable
class LanguageDetectorPort(Protocol):
    """Port for language detection of job text."""

    def detect(self, text: str) -> str:
        """Detect language of text. Returns ISO 639-1 code: 'ru', 'en', 'kz', or 'unknown'."""


@runtime_checkable
class TranslatorPort(Protocol):
    """Port for machine translation between language pairs."""

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text from source_lang to target_lang. Returns original text on failure."""

    def supports(self, source_lang: str, target_lang: str) -> bool:
        """Return True if this translator can handle the given language pair."""


@runtime_checkable
class CrossEncoderPort(Protocol):
    """Port for cross-encoder based result reranking."""

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score each document against query. Returns list of float scores (same order as docs)."""
