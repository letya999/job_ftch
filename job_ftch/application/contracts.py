"""Ports for the hexagonal application core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from datetime import datetime as _DateTime

    from job_ftch.domain import (
        CompiledOntology,
        DuplicateRecord,
        JobGroup,
        JobRecord,
        ManagedCandidateProfile,
        ObservationLedgerEntry,
        OntologyTermStat,
        OutboxRecord,
        QuarantinedRawItem,
        RememberedDedupKey,
        ShotOntologyGraph,
        SkillTag,
    )
    from job_ftch.domain.site_models import MonitorResult, ScrapedPostingPayload
    from job_ftch.domain.source_assessment import SourceAssessmentResult, SourceIngestState
    from job_ftch.domain.source_spec import CareerSiteSpec

else:
    _DateTime = object

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


FanOutItem = TypeVar("FanOutItem", covariant=True)


@runtime_checkable
class FanOutStage(Protocol[StageInput, FanOutItem]):
    """An explicit one-to-many pipeline boundary.

    Ordinary ``Stage`` instances return one item or ``None``. A graph executor
    may expand a sequence only from this contract, never from an arbitrary
    stage's accidental list return.
    """

    is_fan_out_stage: bool

    async def process(self, item: StageInput) -> tuple[FanOutItem, ...]: ...


@runtime_checkable
class Sink(Protocol[SinkItem]):
    async def emit(self, item: SinkItem) -> None:
        """Persist or forward an emitted pipeline item."""


@runtime_checkable
class DeliveryTarget(Protocol[SinkItem]):
    """An externally delivered, replayable pipeline destination."""

    @property
    def target_id(self) -> str: ...

    async def deliver(self, item: SinkItem) -> None: ...


@runtime_checkable
class FlushableSink(Sink[SinkItem], Protocol[SinkItem]):
    async def flush(self) -> None:
        """Finalize buffered writes."""


@dataclass(frozen=True, slots=True)
class DedupReservation:
    """Result of an atomic compare-and-reserve operation."""

    acquired: bool
    reserved_keys: tuple[str, ...] = ()
    conflicting_key: str | None = None


@runtime_checkable
class Store(Protocol):
    async def enqueue_outbox(self, record: OutboxRecord) -> OutboxRecord: ...
    async def list_pending_outbox(
        self, limit: int = 100, *, tenant_id: str | None = None
    ) -> tuple[OutboxRecord, ...]: ...
    async def mark_outbox_delivered(self, idempotency_key: str) -> OutboxRecord | None: ...

    async def acquire_dedup_claim(self, key: str, owner_id: str, *, ttl_seconds: int) -> bool:
        """Atomically acquire a temporary dedup claim, reclaiming only expired leases."""

    async def release_dedup_claim(self, key: str, owner_id: str) -> None:
        """Release a temporary claim when it is still owned by ``owner_id``."""

    async def compare_and_reserve(
        self, keys: tuple[str, ...], owner_id: str, *, ttl_seconds: int
    ) -> DedupReservation:
        """Atomically reserve all keys or none. Returns which keys were reserved."""

    async def record_observation(self, entry: ObservationLedgerEntry) -> ObservationLedgerEntry:
        """Append a raw observation, returning its stable content version."""

    async def get_observation(
        self, stable_id: str, content_hash: str, *, tenant_id: str = "default"
    ) -> ObservationLedgerEntry | None:
        """Return an immutable raw observation by content identity."""

    async def has_processed(self, item_id: str) -> bool:
        """Check whether the raw item already reached a terminal outcome."""

    async def mark_processed(self, item_id: str) -> None:
        """Persist the raw-item identity after a terminal outcome."""

    async def has_dedup_key(self, key: str) -> bool:
        """Check whether a deduplication key is already known."""

    async def remember_dedup_key(self, record: RememberedDedupKey) -> None:
        """Persist a deduplication key and the item it points to."""

    async def get_dedup_key(self, key: str) -> RememberedDedupKey | None:
        """Fetch a single remembered deduplication key by its exact key."""

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

    async def get_last_run_snapshot(
        self,
        tenant_id: str,
        source_id: str,
    ) -> frozenset[str]:
        """Return stable_ids from the most recent run of this source.

        Per ADR-031: a stable_id in the last run means the source already
        yielded this item and it should be dropped from the current run
        (unless the snapshot has expired via purge_old_snapshots).
        """

    async def get_last_run_snapshot_hashes(self, tenant_id: str, source_id: str) -> dict[str, str]:
        """Return stable_id -> content hash from the most recent completed source run."""

    async def save_snapshot_rows(
        self,
        tenant_id: str,
        source_id: str,
        run_id: str,
        rows: tuple[tuple[str, str, str], ...],
    ) -> None:
        """Bulk-insert (stable_id, item_hash, item_json) rows for a finished run.

        Per ADR-031: rows are scoped to (tenant_id, source_id, run_id) inside
        the backend; callers pass raw tuples only.
        """

    async def purge_old_snapshots(
        self,
        tenant_id: str,
        source_id: str,
        *,
        older_than_days: int,
    ) -> int:
        """Delete snapshot rows older than the retention window.

        Returns the number of rows deleted. Per ADR-031: called at the end of
        every save_snapshot_rows; default ttl_days is 7.
        """

    async def get_source_assessment(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceAssessmentResult | None:
        """Return the persisted source assessment for this tenant/source."""

    async def save_source_assessment(
        self,
        tenant_id: str,
        result: SourceAssessmentResult,
    ) -> None:
        """Persist the source assessment in a typed backend-specific store."""

    async def get_source_ingest_state(
        self,
        tenant_id: str,
        source_id: str,
    ) -> SourceIngestState | None:
        """Return persisted bootstrap/incremental ingest state for this source."""

    async def save_source_ingest_state(
        self,
        tenant_id: str,
        state: SourceIngestState,
    ) -> None:
        """Persist bootstrap/incremental ingest state for this source."""


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


class BgeMThreeProviderPort(Protocol):
    """Minimal dense+sparse encoder contract shared by runtime composition roots."""

    @property
    def dim(self) -> int: ...

    def encode(
        self, text: str, *, max_length: int = 512, return_sparse: bool = False
    ) -> dict[str, Any]: ...


@runtime_checkable
class LLMProvider(Protocol):
    async def extract(self, text: str, schema: type[ExtractedItem]) -> ExtractedItem:
        """Extract a structured object from text."""

    async def classify(self, prompt: str, schema: type[Any]) -> Any:
        """Classify text against a Pydantic schema (point ② — relevance)."""

    async def present(self, job_payload: str, schema: type[Any]) -> Any:
        """Format structured job as presentable text (point ③ — Telegram)."""

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
    ) -> str:
        """Free-form text generation for dynamic prompt building (BR-1)."""


@runtime_checkable
class CrossEncoderProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one raw cross-encoder score for every supplied document."""


class ShotStoreClearError(RuntimeError):
    """Raised when a shot-store deletion fails, leaving stale vectors behind.

    A failed add just means a missing example; a failed remove is worse —
    it leaves a vector actively influencing relevance scoring after the
    candidate profile has already dropped the text, with no way for the
    user to notice. Implementations of :class:`ManagedShotBackend` should
    raise this (instead of swallowing the underlying error) so callers can
    tell the user the deletion was only partial.
    """


@runtime_checkable
class ManagedShotBackend(Protocol):
    def add_shot(
        self,
        *,
        text: str,
        label: str,
        role: str,
        tenant_id: str,
        user_id: str,
    ) -> None:
        """Persist a single managed shot and mirror it into scorer-visible state."""

    def remove_shot(self, *, text: str, role: str) -> None:
        """Remove one managed shot from scorer-visible and persistent backends."""

    def remove_user_shots(self, *, tenant_id: str, user_id: str) -> int:
        """Remove all managed shots for a user. Returns removed in-memory count."""

    async def sync_profile_to_shot_store(
        self,
        *,
        profile: ManagedCandidateProfile,
        tenant_id: str,
        user_id: str,
    ) -> tuple[int, int]:
        """Rebuild scorer-visible shots from a managed profile."""


@runtime_checkable
class OntologyStore(Protocol):
    """Persistent storage for live ontology and relevance keyword buckets.

    Per ADR-020: backed by DB when store_backend is sqlite/postgres, by JSON files otherwise.
    """

    async def upsert_skill(
        self,
        canonical: str,
        *,
        alias: str | None = None,
        lang: str = "en",
        source_shot_id: str | None = None,
        source_type: str | None = None,
        polarity: str = "positive",
        model: str | None = None,
        prompt_hash: str | None = None,
    ) -> None: ...

    async def list_skills(self, lang: str | None = None) -> tuple[str, ...]:
        """Return all canonical skill names, optionally filtered by language."""

    async def list_negative_skills(self, lang: str | None = None) -> tuple[str, ...]: ...

    async def upsert_role(
        self,
        canonical: str,
        *,
        alias: str | None = None,
        lang: str = "en",
        source_shot_id: str | None = None,
        source_type: str | None = None,
        polarity: str = "positive",
        model: str | None = None,
        prompt_hash: str | None = None,
    ) -> None: ...

    async def list_roles(self, lang: str | None = None) -> tuple[str, ...]: ...

    async def list_negative_roles(self, lang: str | None = None) -> tuple[str, ...]: ...

    async def upsert_seniority(self, level: str) -> None: ...

    async def list_seniority(self) -> tuple[str, ...]: ...

    async def upsert_anti_pattern(self, pattern: str) -> None: ...

    async def list_anti_patterns(self) -> tuple[str, ...]: ...

    async def upsert_positive_keyword(self, term: str, *, weight: int = 1) -> None: ...

    async def list_positive_keywords(self) -> tuple[dict[str, object], ...]: ...

    async def upsert_negative_keyword(self, term: str, *, weight: int = 1) -> None: ...

    async def list_negative_keywords(self) -> tuple[dict[str, object], ...]: ...

    async def upsert_skill_alias(self, alias: str, canonical: str, lang: str = "en") -> None:
        """Add an alias mapping for an existing canonical skill."""

    async def lookup_skill(self, alias: str) -> str | None:
        """Return canonical name for an alias (case-insensitive), or None."""

    async def upsert_shot_graph(self, graph: ShotOntologyGraph) -> None:
        """Persist a shot-derived ontology graph when the backend supports it."""

    async def upsert_term_stats(self, stats: tuple[OntologyTermStat, ...]) -> None:
        """Persist corpus-level ontology term statistics for auditability."""

    async def upsert_compiled_ontology(self, ontology: CompiledOntology) -> None:
        """Persist the profile-level compiled ontology source of truth."""


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
    async def replace_member(self, group_id: str, job: JobRecord) -> JobGroup: ...
    async def find_by_url(self, canonical_url: str) -> JobGroup | None: ...
    async def find_by_fingerprint(self, fingerprint: str) -> JobGroup | None: ...
    async def find_by_blocking_key(self, key: str, limit: int = 50) -> list[JobGroup]: ...
    async def list_groups(
        self, limit: int = 100, since: _DateTime | None = None
    ) -> list[JobGroup]: ...
    async def count(self, since: _DateTime | None = None) -> int: ...
    async def clear(self) -> int: ...


@runtime_checkable
class JobPersistenceBackend(Protocol):
    async def save(self, job: JobRecord) -> None: ...
    async def get_job(self, job_id: str) -> JobRecord | None: ...
    async def list_jobs(self, limit: int, offset: int) -> list[JobRecord]: ...
    async def count_jobs(self) -> int: ...
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

    async def upsert_many(
        self,
        records: list[tuple[str, list[float], dict[str, Any]]],
    ) -> None: ...

    async def search(
        self,
        vector: list[float],
        limit: int,
        filter: dict[str, Any] | None = None,
    ) -> list[str]: ...

    async def clear(self) -> int: ...


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
class ProxyRouter(Protocol):
    def get_proxy_for(
        self,
        *,
        domain: str,
        country: str = "",
        purpose: str = "ingest",
    ) -> str | None:
        """Return a policy-allowed proxy URL for a specific domain."""

    def rotate_proxy_for(self, domain: str) -> None:
        """Invalidate the current sticky proxy/session for a domain."""

    def proxy_stats(self) -> dict[str, Any]:
        """Return secret-safe proxy pool and budget diagnostics."""


@runtime_checkable
class BypassStrategy(Protocol):
    async def apply_http(self, client: Any) -> Any:
        """Apply bypass to an HTTP client (e.g. swap httpx for curl_cffi)."""

    def apply_browser_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Modify Playwright launch kwargs (add proxy, stealth flags, executable_path)."""

    async def apply_page(self, page: Any) -> None:
        """Apply bypass to a created Playwright page (stealth scripts, behavior simulation)."""


@runtime_checkable
class BrowserSessionBypass(BypassStrategy, Protocol):
    def open_page(
        self,
        config: dict[str, Any],
        *,
        use_proxy: bool = False,
    ) -> Any:
        """Open a browser page/session for bypasses that own the browser runtime."""


@runtime_checkable
class BrowserSessionProbe(Protocol):
    async def probe_listing(
        self,
        *,
        url: str,
        engine: str,
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Open one ephemeral listing page and return bounded public previews."""

    async def probe_detail(
        self,
        *,
        url: str,
        engine: str,
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Open one ephemeral detail page and return a bounded public preview."""

    async def probe_challenge(
        self,
        *,
        url: str,
        engine: str,
        headed: bool = False,
        max_items: int = 5,
        bypass_config: dict[str, Any] | None = None,
        solve: str = "none",
    ) -> dict[str, Any]:
        """Detect a challenge; optional browser_wait/provider solve under gates."""


@runtime_checkable
class OperatorBrowserSessionPort(Protocol):
    async def open(
        self,
        *,
        tenant_id: str,
        url: str,
        engine: str,
        headed: bool = False,
        bypass_config: dict[str, Any] | None = None,
        manual_challenge: bool = False,
    ) -> dict[str, Any]:
        """Open one ephemeral operator session and return a public snapshot."""

    async def get(self, session_id: str) -> dict[str, Any]:
        """Return a public snapshot without driving the page."""

    async def continue_session(
        self,
        session_id: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        """Run one bounded command on an open session."""

    async def capture(self, session_id: str, artifact_type: str) -> dict[str, Any]:
        """Capture a public-safe artifact from an open session."""

    async def close(self, session_id: str) -> dict[str, Any]:
        """Close one session and release the browser."""


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
