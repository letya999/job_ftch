"""Open registries for sources, sinks, stores, and career-site parsers."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from threading import Lock
from types import MethodType
from typing import TYPE_CHECKING, Any, TypeVar, cast

import structlog

from job_ftch.application.site_parser_manifest import (
    build_runtime_defaults_from_manifest,
    get_site_parser_manifest_entry,
)
from job_ftch.config import Settings

from .plugin import PluginDescriptor, PluginKind
from .plugin_registry import PluginRegistry, _default_registry

if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider
    from job_ftch.application.source_assessment import SourceAssessmentAdapter
    from job_ftch.domain.source_assessment import (
        AssessmentConfidence,
        FreshnessAssessment,
    )
    from job_ftch.domain.source_spec import SourceSpec


SourceFactory = Callable[[Settings], object]
SinkFactory = Callable[[Settings], object]
StoreFactory = Callable[[Settings], object]
LLMFactory = Callable[[Settings], object]
OntologyFactory = Callable[[Settings], object]
ManagedShotBackendFactory = Callable[[Settings], object]
SourceSpecFactory = Callable[..., object]


def _call_with_supported_kwargs(factory: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call `factory(*args, **kwargs)` keeping only kwargs the signature accepts.

    Replaces the `try/except TypeError` two-attempt dispatch used previously.
    Factories with varying signatures (`(spec, auth, store)` vs `(spec, auth)`,
    `(bypass_config=...)` vs `()`) all work without the factory raising and
    being retried with a smaller call.
    """
    import inspect

    try:
        sig = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(*args, **kwargs)

    supported: dict[str, Any] = {}
    for name, value in kwargs.items():
        if name in sig.parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            supported[name] = value

    return factory(*args, **supported)


FSource = TypeVar("FSource", bound=SourceFactory)
FSourceV2 = TypeVar("FSourceV2", bound=SourceSpecFactory)
FSink = TypeVar("FSink", bound=SinkFactory)
FStore = TypeVar("FStore", bound=StoreFactory)
FLLM = TypeVar("FLLM", bound=LLMFactory)
FOntology = TypeVar("FOntology", bound=OntologyFactory)
FManagedShotBackend = TypeVar("FManagedShotBackend", bound=ManagedShotBackendFactory)
FAny = TypeVar("FAny", bound=Callable[..., Any])


@dataclass(frozen=True)
class MonitorEntry:
    name: str
    cost: int  # lower = cheaper = tried first in auto-detect
    rich: bool  # True = returns full payload, no scraper needed
    factory: Callable[..., Any]  # (spec, http, auth) -> monitor instance OR discover coroutine
    can_handle: Callable[..., Any] | None = None  # async (url, client) -> dict | None
    assessment_probe: Callable[..., Any] | None = None  # bounded source-assessment probe
    assessment_hint: SourceRegistryAssessmentHint | None = None
    scraper_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRegistryAssessmentHint:
    source_family: str
    confidence: AssessmentConfidence
    rationale: str
    evidence_kind: str
    evidence_value: str
    url_patterns: tuple[str, ...] = ()
    has_publication_time: bool = False
    has_update_time: bool = False
    has_stable_id: bool = False
    has_stable_url: bool = True
    supports_ordered_head: bool = False
    has_cursor_or_since_filter: bool = False
    has_change_validators: bool = False
    has_page_change_signal: bool = False
    has_rss_or_sitemap_dates: bool = False
    has_embedded_state: bool = False
    known_integration: bool = True
    freshness: FreshnessAssessment | None = None


@dataclass(frozen=True)
class SiteParserEntry:
    name: str
    domain_pattern: str
    factory: Callable[..., Any]
    assessment_hint: SourceRegistryAssessmentHint | None = None


@dataclass(frozen=True)
class ScraperEntry:
    name: str
    factory: Callable[..., Any]  # (config, http) -> scraper instance OR scrape coroutine
    can_handle: Callable[..., Any] | None = None  # (list[str]) -> dict | None  (static HTML probe)
    assessment_probe: Callable[..., Any] | None = None  # bounded source-assessment probe
    needs_browser: bool = False


@dataclass(frozen=True, slots=True)
class BypassCapability:
    """Registry metadata used by route policy without backend-name dispatch."""

    cost: int = 0
    transport: str = "httpx"
    browser_family: str | None = None
    owns_session: bool = False
    supports_proxy: bool = True
    challenge_actions: frozenset[str] = frozenset()
    legal_gate: str | None = None
    min_remaining_seconds: float = 0.0

    @property
    def requires_browser(self) -> bool:
        return self.browser_family is not None


_source_factories: dict[str, SourceFactory] = {}
_source_spec_factories: dict[str, SourceSpecFactory] = {}
_sink_factories: dict[str, SinkFactory] = {}
_store_factories: dict[str, StoreFactory] = {}
_job_group_store_factories: dict[str, StoreFactory] = {}
_ontology_factories: dict[str, OntologyFactory] = {}
_llm_factories: dict[str, LLMFactory] = {}
_managed_shot_backend_factories: dict[str, ManagedShotBackendFactory] = {}
_site_parser_factories: list[SiteParserEntry] = []

_MONITOR_REGISTRY: list[MonitorEntry] = []
_SCRAPER_REGISTRY: dict[str, ScraperEntry] = {}

_bypass_factories: dict[str, Callable[..., Any]] = {}
_bypass_capabilities: dict[str, BypassCapability] = {}
_AUTH_PROVIDERS: dict[str, Callable[..., object]] = {}
_job_backend_factories: dict[str, Callable[..., Any]] = {}
_search_backend_factories: dict[str, Callable[..., Any]] = {}
_embedding_provider_factories: dict[str, Callable[..., Any]] = {}
_vector_backend_factories: dict[str, Callable[..., Any]] = {}
_reranker_factories: dict[str, Callable[[Settings], Any]] = {}
_source_assessment_adapters: dict[str, SourceAssessmentAdapter] = {}
_lock = Lock()
_builtins_loaded = False
_entry_points_loaded = False


def get_registry() -> PluginRegistry:
    """Return the global plugin registry."""
    return _default_registry


def known_board_assessment_hint(
    evidence_kind: str,
    evidence_value: str,
    *,
    source_family: str = "known_board",
    url_patterns: tuple[str, ...] = (),
    has_publication_time: bool = False,
    has_update_time: bool = False,
    has_stable_id: bool = False,
    has_stable_url: bool = True,
    supports_ordered_head: bool = False,
    has_cursor_or_since_filter: bool = False,
    has_change_validators: bool = False,
    has_page_change_signal: bool = False,
    has_rss_or_sitemap_dates: bool = False,
    has_embedded_state: bool = False,
    can_detect_freshness_without_snapshot: bool = False,
    can_filter_since_yesterday: bool = False,
    item_level_dates: bool = False,
    ordered_by_newest: bool = False,
    page_level_change_only: bool = False,
    requires_full_snapshot: bool = True,
    rationale: str = "Known board monitor supplies a stable source-specific discovery path.",
) -> SourceRegistryAssessmentHint:
    from job_ftch.domain.source_assessment import AssessmentConfidence, FreshnessAssessment

    return SourceRegistryAssessmentHint(
        source_family=source_family,
        confidence=AssessmentConfidence.HIGH,
        rationale=rationale,
        evidence_kind=evidence_kind,
        evidence_value=evidence_value,
        url_patterns=url_patterns,
        has_publication_time=has_publication_time,
        has_update_time=has_update_time,
        has_stable_id=has_stable_id,
        has_stable_url=has_stable_url,
        supports_ordered_head=supports_ordered_head,
        has_cursor_or_since_filter=has_cursor_or_since_filter,
        has_change_validators=has_change_validators,
        has_page_change_signal=has_page_change_signal,
        has_rss_or_sitemap_dates=has_rss_or_sitemap_dates,
        has_embedded_state=has_embedded_state,
        known_integration=True,
        freshness=FreshnessAssessment(
            confidence=AssessmentConfidence.HIGH,
            can_detect_freshness_without_snapshot=can_detect_freshness_without_snapshot,
            can_filter_since_yesterday=can_filter_since_yesterday,
            item_level_dates=item_level_dates,
            ordered_by_newest=ordered_by_newest,
            page_level_change_only=page_level_change_only,
            requires_full_snapshot=requires_full_snapshot,
            rationale=rationale,
        ),
    )


def register_source(
    kind: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FSource], FSource]:
    normalized = kind.strip()

    def decorator(factory: FSource) -> FSource:
        _source_factories[normalized] = factory
        _default_registry.register(
            PluginDescriptor(
                name=normalized,
                kind=PluginKind.SOURCE,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_managed_shot_backend(
    kind: str,
) -> Callable[[FManagedShotBackend], FManagedShotBackend]:
    normalized = kind.strip()

    def decorator(factory: FManagedShotBackend) -> FManagedShotBackend:
        _managed_shot_backend_factories[normalized] = factory
        return factory

    return decorator


def register_source_spec(
    kind: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FSourceV2], FSourceV2]:
    normalized = kind.strip()

    def decorator(factory: FSourceV2) -> FSourceV2:
        _source_spec_factories[normalized] = factory
        _default_registry.register(
            PluginDescriptor(
                name=normalized,
                kind=PluginKind.SOURCE,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_sink(
    kind: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FSink], FSink]:
    normalized = kind.strip()

    def decorator(factory: FSink) -> FSink:
        _sink_factories[normalized] = factory
        _default_registry.register(
            PluginDescriptor(
                name=normalized,
                kind=PluginKind.SINK,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_store(
    kind: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FStore], FStore]:
    normalized = kind.strip()

    def decorator(factory: FStore) -> FStore:
        _store_factories[normalized] = factory
        _default_registry.register(
            PluginDescriptor(
                name=normalized,
                kind=PluginKind.STORE,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_job_group_store(
    kind: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FStore], FStore]:
    normalized = kind.strip()

    def decorator(factory: FStore) -> FStore:
        _job_group_store_factories[normalized] = factory
        _default_registry.register(
            PluginDescriptor(
                name=normalized,
                kind=PluginKind.JOB_GROUP_STORE,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_llm(
    kind: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FLLM], FLLM]:
    normalized = kind.strip()

    def decorator(factory: FLLM) -> FLLM:
        _llm_factories[normalized] = factory
        _default_registry.register(
            PluginDescriptor(
                name=normalized,
                kind=PluginKind.LLM,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def get_store_class(kind: str) -> Callable[[Settings], object]:
    """Return the registered store factory for the given backend `kind`."""
    load_extensions()
    factory = _store_factories.get(kind)
    if factory is None:
        msg = f"Unknown store backend: {kind}"
        raise ValueError(msg)
    return factory


def list_stores() -> tuple[str, ...]:
    """Return the tuple of registered store backend names."""
    load_extensions()
    return tuple(_store_factories.keys())


def register_ontology_store(
    kind: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FOntology], FOntology]:
    normalized = kind.strip()

    def decorator(factory: FOntology) -> FOntology:
        _ontology_factories[normalized] = factory
        return factory

    return decorator


def register_bypass(
    name: str,
    *,
    version: str = "0.0.0",
    requires_extras: tuple[str, ...] = (),
    capability: BypassCapability | None = None,
) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _bypass_factories[name] = factory
        _bypass_capabilities[name] = capability or BypassCapability()
        _default_registry.register(
            PluginDescriptor(
                name=name,
                kind=PluginKind.BYPASS,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def get_bypass_capability(name: str) -> BypassCapability:
    """Return declared route metadata for a registered bypass backend."""
    load_extensions()
    try:
        return _bypass_capabilities[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported bypass capability: {name}") from exc


def list_bypass_capabilities() -> dict[str, BypassCapability]:
    """Return a snapshot of the positive capability inventory."""
    load_extensions()
    return dict(_bypass_capabilities)


def register_auth_provider(
    name: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _AUTH_PROVIDERS[name] = factory
        _default_registry.register(
            PluginDescriptor(
                name=name,
                kind=PluginKind.AUTH,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_job_backend(
    name: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _job_backend_factories[name] = factory
        _default_registry.register(
            PluginDescriptor(
                name=name,
                kind=PluginKind.JOB_BACKEND,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_search_backend(
    name: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _search_backend_factories[name] = factory
        _default_registry.register(
            PluginDescriptor(
                name=name,
                kind=PluginKind.SEARCH_BACKEND,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_embedding_provider(
    name: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _embedding_provider_factories[name] = factory
        _default_registry.register(
            PluginDescriptor(
                name=name,
                kind=PluginKind.EMBEDDING,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_vector_backend(
    name: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _vector_backend_factories[name] = factory
        _default_registry.register(
            PluginDescriptor(
                name=name,
                kind=PluginKind.VECTOR,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def register_reranker(
    name: str, *, version: str = "0.0.0", requires_extras: tuple[str, ...] = ()
) -> Callable[[Callable[[Settings], Any]], Callable[[Settings], Any]]:
    def decorator(fn: Callable[[Settings], Any]) -> Callable[[Settings], Any]:
        _reranker_factories[name] = fn
        _default_registry.register(
            PluginDescriptor(
                name=name,
                kind=PluginKind.RERANKER,
                factory=fn,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return fn

    return decorator


def register_source_assessment_adapter(
    name: str,
    adapter: SourceAssessmentAdapter,
    *,
    version: str = "0.0.0",
    requires_extras: tuple[str, ...] = (),
) -> None:
    normalized = name.strip()
    _source_assessment_adapters[normalized] = adapter
    _default_registry.register(
        PluginDescriptor(
            name=normalized,
            kind=PluginKind.SOURCE_ASSESSMENT,
            factory=lambda: adapter,
            version=version,
            requires_extras=requires_extras,
        ),
        overwrite=True,
    )


def register_monitor(
    name: str,
    factory: Callable[..., Any],
    cost: int,
    rich: bool,
    can_handle: Callable[..., Any] | None = None,
    assessment_probe: Callable[..., Any] | None = None,
    assessment_hint: SourceRegistryAssessmentHint | None = None,
    scraper_chain: tuple[str, ...] = (),
    version: str = "0.0.0",
    requires_extras: tuple[str, ...] = (),
) -> None:
    """Register a board monitor and keep registry sorted by cost."""
    entry = MonitorEntry(
        name=name,
        factory=factory,
        cost=cost,
        rich=rich,
        can_handle=can_handle,
        assessment_probe=assessment_probe,
        assessment_hint=assessment_hint,
        scraper_chain=scraper_chain,
    )
    _MONITOR_REGISTRY.append(entry)
    _MONITOR_REGISTRY.sort(key=lambda x: x.cost)
    _default_registry.register(
        PluginDescriptor(
            name=name,
            kind=PluginKind.MONITOR,
            factory=factory,
            version=version,
            requires_extras=requires_extras,
        ),
        overwrite=True,
    )


def register_scraper(
    name: str,
    factory: Callable[..., Any],
    can_handle: Callable[..., Any] | None = None,
    assessment_probe: Callable[..., Any] | None = None,
    needs_browser: bool = False,
    version: str = "0.0.0",
    requires_extras: tuple[str, ...] = (),
) -> None:
    """Register a job scraper."""
    _SCRAPER_REGISTRY[name] = ScraperEntry(
        name=name,
        factory=factory,
        can_handle=can_handle,
        assessment_probe=assessment_probe,
        needs_browser=needs_browser,
    )
    _default_registry.register(
        PluginDescriptor(
            name=name,
            kind=PluginKind.SCRAPER,
            factory=factory,
            version=version,
            requires_extras=requires_extras,
        ),
        overwrite=True,
    )


def resolve_monitor(name: str) -> MonitorEntry:
    """Find a monitor by name."""
    load_extensions()
    for entry in _MONITOR_REGISTRY:
        if entry.name == name:
            return entry
    msg = f"Unsupported monitor: {name}"
    raise ValueError(msg)


def resolve_scraper(name: str) -> ScraperEntry:
    """Find a scraper by name."""
    load_extensions()
    entry = _SCRAPER_REGISTRY.get(name)
    if entry is None:
        msg = f"Unsupported scraper: {name}"
        raise ValueError(msg)
    return entry


def get_monitor_assessment_hint(name: str) -> SourceRegistryAssessmentHint | None:
    try:
        return resolve_monitor(name).assessment_hint
    except ValueError:
        return None


def resolve_monitor_assessment_hint(url: str) -> SourceRegistryAssessmentHint | None:
    import re

    load_extensions()
    for entry in _MONITOR_REGISTRY:
        hint = entry.assessment_hint
        if hint is None:
            continue
        for pattern in hint.url_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return hint
    return None


def resolve_bypass(name: str | None, bypass_config: dict[str, Any] | None = None) -> Any:
    """Resolve a BypassStrategy by name."""
    load_extensions()
    name = name or "noop"
    factory = _bypass_factories.get(name)
    if factory is None:
        msg = f"Unsupported bypass strategy: {name}"
        raise ValueError(msg)
    return _call_with_supported_kwargs(factory, bypass_config=bypass_config)


def get_all_monitor_entries() -> list[MonitorEntry]:
    """Return all registered monitors sorted by cost."""
    load_extensions()
    return list(_MONITOR_REGISTRY)


def get_all_scraper_entries() -> list[ScraperEntry]:
    """Return all registered scrapers."""
    load_extensions()
    return list(_SCRAPER_REGISTRY.values())


def all_monitor_names() -> frozenset[str]:
    load_extensions()
    return frozenset(e.name for e in _MONITOR_REGISTRY)


def all_scraper_names() -> frozenset[str]:
    load_extensions()
    return frozenset(_SCRAPER_REGISTRY)


def all_site_parser_names() -> frozenset[str]:
    load_extensions()
    return frozenset(entry.name for entry in _site_parser_factories)


def rich_monitor_names() -> frozenset[str]:
    load_extensions()
    return frozenset(e.name for e in _MONITOR_REGISTRY if e.rich)


def get_source_assessment_adapters() -> list[SourceAssessmentAdapter]:
    load_extensions()
    return sorted(_source_assessment_adapters.values(), key=lambda item: item.priority)


_OPTIONAL_BUILTIN_MODULES = frozenset(
    {
        "job_ftch.infrastructure.llm.openai_provider",
        "job_ftch.infrastructure.embeddings.openai_provider",
        "job_ftch.infrastructure.sources.telegram",
        "job_ftch.infrastructure.sources.telegram_realtime",
        "job_ftch.infrastructure.stores.sqlite",
        "job_ftch.infrastructure.backends.jobs.sqlite",
        "job_ftch.infrastructure.sources.realtime.rss",
        "job_ftch.infrastructure.sources.realtime.webhook",
        "job_ftch.infrastructure.sources.realtime.websocket",
        "job_ftch.infrastructure.sources.browser.base",
        "job_ftch.infrastructure.bypass.curl_bypass",
        "job_ftch.infrastructure.embeddings.sentence_transformers_provider",
        "job_ftch.infrastructure.embeddings.ollama_provider",
        "job_ftch.infrastructure.backends.vector.pgvector",
        "job_ftch.infrastructure.backends.vector.qdrant",
        "job_ftch.infrastructure.backends.search.hybrid",
        "job_ftch.infrastructure.stores.postgres",
        "job_ftch.infrastructure.backends.jobs.postgres",
    }
)


def load_extensions() -> None:
    global _builtins_loaded, _entry_points_loaded
    with _lock:
        if not _builtins_loaded:
            for module_name in (
                "job_ftch.infrastructure.sources.local_fixture",
                "job_ftch.infrastructure.sources.telegram",
                "job_ftch.infrastructure.sources.career_site",
                "job_ftch.infrastructure.sources.career_site_source",
                "job_ftch.infrastructure.sources.site_parsers",
                "job_ftch.infrastructure.sources.declarative",
                "job_ftch.infrastructure.stores.in_memory",
                "job_ftch.infrastructure.stores.sqlite",
                "job_ftch.infrastructure.stores.postgres",
                "job_ftch.infrastructure.stores.job_group_store",
                "job_ftch.infrastructure.llm.heuristic",
                "job_ftch.infrastructure.llm.openai_provider",
                "job_ftch.infrastructure.llm.fastembed_provider",
                "job_ftch.infrastructure.llm.reranker_provider",
                "job_ftch.infrastructure.auth.env_auth",
                "job_ftch.infrastructure.auth.file_auth",
                "job_ftch.sinks.json_file",
                "job_ftch.sinks.null_sink",
                "job_ftch.adapters.telegram_posting_sink",
                "job_ftch.infrastructure.backends.jobs.sqlite",
                "job_ftch.infrastructure.backends.jobs.postgres",
                "job_ftch.infrastructure.embeddings.openai_provider",
                "job_ftch.infrastructure.embeddings.ollama_provider",
                "job_ftch.infrastructure.backends.vector.pgvector",
                "job_ftch.infrastructure.backends.vector.qdrant",
                "job_ftch.infrastructure.backends.search.hybrid",
                "job_ftch.infrastructure.sources.api.greenhouse",
                "job_ftch.infrastructure.sources.api.hh",
                "job_ftch.infrastructure.sources.browser.base",
                "job_ftch.infrastructure.sources.realtime.rss",
                "job_ftch.infrastructure.sources.realtime.webhook",
                "job_ftch.infrastructure.sources.realtime.websocket",
                "job_ftch.infrastructure.sources.telegram_realtime",
                "job_ftch.infrastructure.sources.monitors",
                "job_ftch.infrastructure.sources.scrapers",
                "job_ftch.infrastructure.source_assessment",
                "job_ftch.infrastructure.bypass",
                "job_ftch.infrastructure.ontology.db_store",
                "job_ftch.infrastructure.ontology.postgres_store",
                "job_ftch.infrastructure.ontology.file_store",
                "job_ftch.infrastructure.relevance.managed_shots",
            ):
                if module_name in _OPTIONAL_BUILTIN_MODULES:
                    with contextlib.suppress(ImportError):
                        import_module(module_name)
                else:
                    import_module(module_name)
            _builtins_loaded = True
        if _entry_points_loaded:
            return
        for group in (
            "job_ftch.sources",
            "job_ftch.parsers",
            "job_ftch.sinks",
            "job_ftch.stores",
            "job_ftch.bypass",
            "job_ftch.job_backends",
            "job_ftch.search_backends",
            "job_ftch.embedding_providers",
            "job_ftch.vector_backends",
            "job_ftch.rerankers",
            "job_ftch.source_assessment_adapters",
        ):
            for candidate in entry_points(group=group):
                loaded = candidate.load()
                if callable(loaded):
                    loaded()
        _entry_points_loaded = True


def create_source(settings: Settings) -> object:
    load_extensions()
    factory = _source_factories.get(settings.source_backend)
    if factory is None:
        msg = f"Unsupported source backend: {settings.source_backend}"
        raise ValueError(msg)
    return factory(settings)


class _NullAuthProvider:
    """Fallback auth provider used when no auth is configured."""

    def resolve(self, source_id: str) -> dict[str, str]:
        del source_id
        return {}


def create_source_from_spec(
    spec: SourceSpec, auth: AuthProvider | None = None, store: Any = None
) -> object:
    load_extensions()
    effective_auth: AuthProvider = auth if auth is not None else _NullAuthProvider()
    factory = _source_spec_factories.get(spec.type)
    if factory is None:
        msg = f"Unsupported source type: {spec.type}"
        raise ValueError(msg)
    source = _call_with_supported_kwargs(factory, spec, effective_auth, store=store)
    # CompositeSource uses this contract for canonical source-health keys.
    # Several legacy adapters (notably Telegram) predate SourceSpec and would
    # otherwise identify themselves as `TelegramChannelSource:<repr>`.
    if getattr(source, "spec", None) is None:
        source.spec = spec
    return source


def create_sink(settings: Settings, *, quarantine: bool = False) -> object:
    load_extensions()
    factory = _sink_factories.get(settings.sink_backend)
    if factory is None:
        msg = f"Unsupported sink backend: {settings.sink_backend}"
        raise ValueError(msg)
    return factory(settings if not quarantine else settings.quarantine_settings())


def resolve_store_backend(settings: Settings) -> str:
    """Resolve `store_backend='auto'` to a concrete registered backend (per ADR-034).

    Order of preference:
      1. `postgres` if `store_dsn` is set and the value parses as a URL
         (`scheme://...`).
      2. `sqlite` if `JOB_FTCH_SQLITE_PATH` is set or the default path
         (`./data/jobs.db`) is in a writable directory.
      3. `memory` otherwise.

    The result is logged at INFO level so operators can see which backend
    was picked. Production deployments that need a specific backend should
    set `JOB_FTCH_STORE_BACKEND` explicitly; this resolver is the dev /
    CI / `--help` default.
    """
    backend = (settings.store_backend or "auto").strip().lower()
    if backend != "auto":
        return backend

    dsn = settings.store_dsn.get_secret_value().strip() if settings.store_dsn else ""
    if dsn and "://" in dsn:
        return "postgres"

    sqlite_path = os.environ.get("JOB_FTCH_SQLITE_PATH", "").strip()
    if sqlite_path:
        return "sqlite"

    default_path = Path("./data/jobs.db")
    try:
        default_path.parent.mkdir(parents=True, exist_ok=True)
        return "sqlite"
    except OSError:
        return "memory"


def create_store(settings: Settings) -> object:
    load_extensions()
    backend = resolve_store_backend(settings)
    factory = _store_factories.get(backend)
    if factory is None:
        msg = f"Unsupported store backend: {backend}"
        raise ValueError(msg)
    if backend != settings.store_backend:
        structlog.get_logger("job_ftch.registry").info(
            "store_backend_resolved",
            requested=settings.store_backend,
            resolved=backend,
        )
    return factory(settings)


_FALLBACK_STORE_BACKEND = "memory"


class StorageError(RuntimeError):
    """Store creation/health error that must not be silently masked."""


def _create_fallback_store(settings: Settings) -> object:
    factory = _store_factories.get(_FALLBACK_STORE_BACKEND)
    if factory is None:
        msg = f"Fallback store '{_FALLBACK_STORE_BACKEND}' not registered. Ensure load_extensions() ran."
        raise RuntimeError(msg)
    return factory(settings)


async def create_store_with_fallback(settings: Settings) -> object:
    """Create a store with async health check and fallback to the in_memory backend."""
    load_extensions()

    from job_ftch.application.contracts import StoreConnector

    effective_backend = resolve_store_backend(settings)
    allow_fallback = settings.store_fallback_on_error and (
        settings.store_allow_fallback or effective_backend != "postgres"
    )
    log = structlog.get_logger("job_ftch.registry")

    try:
        primary_store = create_store(settings)
    except Exception as exc:
        if effective_backend == "postgres":
            log.error(
                "postgres_store_creation_failed",
                backend=effective_backend,
                fallback_allowed=settings.store_allow_fallback,
                error=str(exc),
            )
            if not allow_fallback:
                raise StorageError(f"Postgres configured but unreachable: {exc}") from exc
        if allow_fallback:
            log.warning(
                "store_creation_failed_falling_back_to_in_memory",
                backend=effective_backend,
                error=str(exc),
            )
            return _create_fallback_store(settings)
        raise

    # If it supports StoreConnector (ping), check health
    if isinstance(primary_store, StoreConnector):
        if await primary_store.ping():
            return primary_store

        # Health check failed
        if allow_fallback:
            log_method = log.error if effective_backend == "postgres" else log.warning
            log_method(
                "store_health_check_failed_falling_back_to_in_memory",
                backend=effective_backend,
            )
            return _create_fallback_store(settings)
        if effective_backend == "postgres":
            log.error(
                "postgres_store_unreachable",
                backend=effective_backend,
                fallback_allowed=settings.store_allow_fallback,
            )
            raise StorageError("Postgres configured but unreachable during startup ping.")

    return primary_store


def create_job_group_store(settings: Settings) -> object:
    load_extensions()
    backend = settings.job_group_store_backend
    factory = _job_group_store_factories.get(backend)
    if factory is None:
        msg = f"Unsupported job group store backend: {backend}"
        raise ValueError(msg)
    return factory(settings)


async def create_job_group_store_with_fallback(settings: Settings) -> object:
    """Create job group store with async ping health check and fallback to sqlite/memory."""
    load_extensions()
    backend = settings.job_group_store_backend
    log = structlog.get_logger("job_ftch.registry")

    factory = _job_group_store_factories.get(backend)
    if factory is None:
        msg = f"Unsupported job group store backend: {backend}"
        raise ValueError(msg)

    primary_exc: Exception | None = None
    store: object | None = None
    try:
        store = factory(settings)
    except Exception as exc:
        primary_exc = exc

    if store is not None:
        if hasattr(store, "ping"):
            try:
                ok = await store.ping()  # type: ignore[union-attr]
                if ok:
                    return store
                primary_exc = RuntimeError(
                    f"job_group_store ping returned False for backend={backend}"
                )
            except Exception as exc:
                primary_exc = exc
        else:
            return store

    log.warning(
        "job_group_store_primary_unavailable_falling_back",
        backend=backend,
        error=str(primary_exc),
    )

    for fallback in ("sqlite", "memory"):
        if fallback == backend:
            continue
        fallback_factory = _job_group_store_factories.get(fallback)
        if fallback_factory is None:
            continue
        try:
            fallback_store = fallback_factory(settings)
            if hasattr(fallback_store, "ping"):
                ok = await fallback_store.ping()  # type: ignore[union-attr]
                if not ok:
                    continue
            log.warning(
                "job_group_store_falling_back",
                primary=backend,
                fallback=fallback,
                error=str(primary_exc),
            )
            return fallback_store
        except Exception:
            continue

    if primary_exc is not None:
        raise primary_exc
    msg = f"All job_group_store backends failed for primary={backend}"
    raise RuntimeError(msg)


def create_llm(settings: Settings) -> object:
    load_extensions()
    factory = _llm_factories.get(settings.llm_backend)
    if factory is None:
        msg = f"Unsupported llm backend: {settings.llm_backend}"
        raise ValueError(msg)
    return factory(settings)


def create_managed_shot_backend(settings: Settings) -> object:
    load_extensions()
    factory = _managed_shot_backend_factories.get("default")
    if factory is None:
        msg = "Managed shot backend not registered for kind: default"
        raise ValueError(msg)
    return factory(settings)


def create_ontology_store(settings: Settings) -> object:
    """Build the live ontology store based on settings.

    Per ADR-020: DB-backed when store_backend is sqlite/postgres, file-backed otherwise.

    The SQLite and PostgreSQL backends are registered as separate
    factories because they have different driver APIs (aiosqlite vs
    asyncpg) and connection models (file path vs DSN). Mapping
    both to a single ``"db"`` factory would silently send the wrong
    driver the wrong arguments — the original bug that put this
    dispatch in place.
    """
    load_extensions()
    # ``store_backend`` is either a literal ``"postgres"`` (set
    # explicitly by the operator or by the auto-resolver) or
    # ``"sqlite"`` / ``"memory"`` / etc. The legacy code path
    # checked for ``"postgresql"`` (with the trailing ``ql``)
    # which never matched and silently fell through to the
    # ``file`` backend, dropping every ontology update.
    if settings.store_backend == "postgres":
        kind = "postgres"
    elif settings.store_backend == "sqlite":
        kind = "db"
    else:
        kind = "file"
    factory = _ontology_factories.get(kind)
    if factory is None:
        msg = f"Ontology store not registered for kind: {kind}"
        raise ValueError(msg)
    return factory(settings)


def register_site_parser(
    name: str,
    *,
    domain_pattern: str,
    assessment_hint: SourceRegistryAssessmentHint | None = None,
    version: str = "0.0.0",
    requires_extras: tuple[str, ...] = (),
) -> Callable[[FAny], FAny]:
    normalized = name.strip()

    def decorator(factory: FAny) -> FAny:
        _site_parser_factories.append(
            SiteParserEntry(
                name=normalized,
                domain_pattern=domain_pattern,
                factory=factory,
                assessment_hint=assessment_hint,
            )
        )
        _default_registry.register(
            PluginDescriptor(
                name=normalized,
                kind=PluginKind.PARSER,
                factory=factory,
                version=version,
                requires_extras=requires_extras,
            ),
            overwrite=True,
        )
        return factory

    return decorator


def _effective_site_parser_pattern(entry: SiteParserEntry) -> str:
    manifest_entry = get_site_parser_manifest_entry(entry.name)
    if manifest_entry is not None:
        return manifest_entry.domain_pattern
    return entry.domain_pattern


def _sorted_site_parser_entries() -> list[SiteParserEntry]:
    return sorted(
        _site_parser_factories,
        key=lambda entry: len(_effective_site_parser_pattern(entry)),
        reverse=True,
    )


def _instantiate_site_parser(entry: SiteParserEntry) -> Any:
    parser = entry.factory()
    manifest_entry = get_site_parser_manifest_entry(entry.name)
    if manifest_entry is None:
        return parser

    parser._manifest_entry = manifest_entry
    parser.domain_pattern = manifest_entry.domain_pattern
    parser.has_custom_parse = manifest_entry.has_custom_parse
    parser.supports_discover = manifest_entry.supports_discover

    manifest_defaults = build_runtime_defaults_from_manifest(manifest_entry)
    original_runtime_defaults = getattr(parser, "runtime_defaults", None)
    if manifest_defaults is not None and callable(original_runtime_defaults):

        def _runtime_defaults(self: Any, url: str) -> object:
            del url
            return manifest_defaults

        parser.runtime_defaults = MethodType(_runtime_defaults, parser)
    return parser


def resolve_site_parser(url: str) -> Any | None:
    import re

    load_extensions()
    for entry in _sorted_site_parser_entries():
        if re.search(_effective_site_parser_pattern(entry), url, re.IGNORECASE):
            return _instantiate_site_parser(entry)
    return None


def resolve_site_parser_by_name(name: str) -> Any | None:
    load_extensions()
    needle = name.strip()
    if not needle:
        return None
    for entry in _sorted_site_parser_entries():
        if entry.name == needle:
            return _instantiate_site_parser(entry)
    return None


def site_parser_domain_pattern(name: str) -> str | None:
    load_extensions()
    needle = name.strip()
    if not needle:
        return None
    for entry in _sorted_site_parser_entries():
        if entry.name == needle:
            return _effective_site_parser_pattern(entry)
    return None


def resolve_site_parser_for_spec(spec: Any) -> Any | None:
    pinned = getattr(spec, "site_parser", None)
    if isinstance(pinned, str) and pinned.strip():
        parser = resolve_site_parser_by_name(pinned)
        if parser is not None:
            return parser
    url = getattr(spec, "url", None)
    if isinstance(url, str) and url.strip():
        return resolve_site_parser(url)
    return None


def resolve_site_parser_assessment_hint(url: str) -> SourceRegistryAssessmentHint | None:
    import re

    load_extensions()
    for entry in _sorted_site_parser_entries():
        if re.search(_effective_site_parser_pattern(entry), url, re.IGNORECASE):
            return entry.assessment_hint
    return None


def create_auth_provider(name: str | None, settings: Settings) -> AuthProvider:
    load_extensions()
    normalized = (name or "env").strip().lower()
    factory = _AUTH_PROVIDERS.get(normalized)
    if factory is None:
        msg = f"Unsupported auth provider: {name}"
        raise ValueError(msg)
    return factory(settings)


def create_job_backend(settings: Settings) -> object:
    load_extensions()
    factory = _job_backend_factories.get(settings.job_backend)
    if factory is None:
        msg = f"Unsupported job backend: {settings.job_backend}"
        raise ValueError(msg)
    return factory(settings)


def create_search_backend(settings: Settings) -> object:
    load_extensions()
    factory = _search_backend_factories.get(settings.search_backend)
    if factory is None:
        msg = f"Unsupported search backend: {settings.search_backend}"
        raise ValueError(msg)
    return factory(settings)


def create_embedding_provider(settings: Settings) -> object:
    load_extensions()
    factory = _embedding_provider_factories.get(settings.embedding_provider)
    if factory is None:
        lazy_modules = {
            "sentence_transformers": "job_ftch.infrastructure.embeddings.sentence_transformers_provider",
        }
        module_name = lazy_modules.get(settings.embedding_provider)
        if module_name is not None:
            import_module(module_name)
            factory = _embedding_provider_factories.get(settings.embedding_provider)
    if factory is None:
        msg = f"Unsupported embedding provider: {settings.embedding_provider}"
        raise ValueError(msg)
    return factory(settings)


def create_vector_backend(settings: Settings) -> object | None:
    load_extensions()
    if not settings.vector_backend:
        return None
    factory = _vector_backend_factories.get(settings.vector_backend)
    if factory is None:
        msg = f"Unsupported vector backend: {settings.vector_backend}"
        raise ValueError(msg)
    return cast("object", factory(settings))


def create_reranker(settings: Settings) -> Any | None:
    load_extensions()
    name = getattr(settings, "reranker_model", "jina-v2-multilingual")
    factory = _reranker_factories.get(name)
    if factory is None:
        return None
    return factory(settings)
