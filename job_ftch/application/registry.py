"""Open registries for sources, sinks, stores, and career-site parsers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

import structlog

from job_ftch.config import Settings

if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider
    from job_ftch.domain.source_spec import SourceSpec


class CareerSiteParser(Protocol):
    async def parse(
        self,
        *,
        client: Any,
        url: str,
        html: str,
        limit: int,
    ) -> list[object]:
        """Parse a fetched career-site board into emitted items."""


SourceFactory = Callable[[Settings], object]
SinkFactory = Callable[[Settings], object]
StoreFactory = Callable[[Settings], object]
LLMFactory = Callable[[Settings], object]
SourceSpecFactory = Callable[..., object]
ParserMatcher = Callable[[str, str], bool]
ParserFactory = Callable[[], object]

FSource = TypeVar("FSource", bound=SourceFactory)
FSourceV2 = TypeVar("FSourceV2", bound=SourceSpecFactory)
FSink = TypeVar("FSink", bound=SinkFactory)
FStore = TypeVar("FStore", bound=StoreFactory)
FLLM = TypeVar("FLLM", bound=LLMFactory)
FParser = TypeVar("FParser", bound=ParserFactory)
FAny = TypeVar("FAny", bound=Callable[..., Any])


@dataclass(frozen=True)
class MonitorEntry:
    name: str
    cost: int  # lower = cheaper = tried first in auto-detect
    rich: bool  # True = returns full payload, no scraper needed
    factory: Callable[..., Any]  # (spec, http, auth) -> monitor instance OR discover coroutine
    can_handle: Callable[..., Any] | None = None  # async (url, client) -> dict | None


@dataclass(frozen=True)
class ScraperEntry:
    name: str
    factory: Callable[..., Any]  # (config, http) -> scraper instance OR scrape coroutine
    can_handle: Callable[..., Any] | None = None  # (list[str]) -> dict | None  (static HTML probe)
    needs_browser: bool = False


_source_factories: dict[str, SourceFactory] = {}
_source_spec_factories: dict[str, SourceSpecFactory] = {}
_sink_factories: dict[str, SinkFactory] = {}
_store_factories: dict[str, StoreFactory] = {}
_job_group_store_factories: dict[str, StoreFactory] = {}
_llm_factories: dict[str, LLMFactory] = {}
_parser_factories: list[tuple[str, ParserMatcher, ParserFactory]] = []

_MONITOR_REGISTRY: list[MonitorEntry] = []
_SCRAPER_REGISTRY: dict[str, ScraperEntry] = {}

_bypass_factories: dict[str, Callable[..., Any]] = {}
_AUTH_PROVIDERS: dict[str, Callable[..., object]] = {}
_job_backend_factories: dict[str, Callable[..., Any]] = {}
_search_backend_factories: dict[str, Callable[..., Any]] = {}
_embedding_provider_factories: dict[str, Callable[..., Any]] = {}
_vector_backend_factories: dict[str, Callable[..., Any]] = {}
_lock = Lock()
_builtins_loaded = False
_entry_points_loaded = False


def register_source(kind: str) -> Callable[[FSource], FSource]:
    normalized = kind.strip()

    def decorator(factory: FSource) -> FSource:
        _source_factories[normalized] = factory
        return factory

    return decorator


def register_source_spec(kind: str) -> Callable[[FSourceV2], FSourceV2]:
    normalized = kind.strip()

    def decorator(factory: FSourceV2) -> FSourceV2:
        _source_spec_factories[normalized] = factory
        return factory

    return decorator


def register_sink(kind: str) -> Callable[[FSink], FSink]:
    normalized = kind.strip()

    def decorator(factory: FSink) -> FSink:
        _sink_factories[normalized] = factory
        return factory

    return decorator


def register_store(kind: str) -> Callable[[FStore], FStore]:
    normalized = kind.strip()

    def decorator(factory: FStore) -> FStore:
        _store_factories[normalized] = factory
        return factory

    return decorator


def register_job_group_store(kind: str) -> Callable[[FStore], FStore]:
    normalized = kind.strip()

    def decorator(factory: FStore) -> FStore:
        _job_group_store_factories[normalized] = factory
        return factory

    return decorator


def register_llm(kind: str) -> Callable[[FLLM], FLLM]:
    normalized = kind.strip()

    def decorator(factory: FLLM) -> FLLM:
        _llm_factories[normalized] = factory
        return factory

    return decorator


def register_parser(
    kind: str,
    *,
    matcher: ParserMatcher,
) -> Callable[[FParser], FParser]:
    normalized = kind.strip()

    def decorator(factory: FParser) -> FParser:
        _parser_factories.append((normalized, matcher, factory))
        return factory

    return decorator


def register_bypass(name: str) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _bypass_factories[name] = factory
        return factory

    return decorator


def register_auth_provider(name: str) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _AUTH_PROVIDERS[name] = factory
        return factory

    return decorator


def register_job_backend(name: str) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _job_backend_factories[name] = factory
        return factory

    return decorator


def register_search_backend(name: str) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _search_backend_factories[name] = factory
        return factory

    return decorator


def register_embedding_provider(name: str) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _embedding_provider_factories[name] = factory
        return factory

    return decorator


def register_vector_backend(name: str) -> Callable[[FAny], FAny]:
    def decorator(factory: FAny) -> FAny:
        _vector_backend_factories[name] = factory
        return factory

    return decorator


def register_monitor(
    name: str,
    factory: Callable[..., Any],
    cost: int,
    rich: bool,
    can_handle: Callable[..., Any] | None = None,
) -> None:
    """Register a board monitor and keep registry sorted by cost."""
    entry = MonitorEntry(
        name=name,
        factory=factory,
        cost=cost,
        rich=rich,
        can_handle=can_handle,
    )
    _MONITOR_REGISTRY.append(entry)
    _MONITOR_REGISTRY.sort(key=lambda x: x.cost)


def register_scraper(
    name: str,
    factory: Callable[..., Any],
    can_handle: Callable[..., Any] | None = None,
    needs_browser: bool = False,
) -> None:
    """Register a job scraper."""
    _SCRAPER_REGISTRY[name] = ScraperEntry(
        name=name,
        factory=factory,
        can_handle=can_handle,
        needs_browser=needs_browser,
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


def resolve_bypass(name: str | None, bypass_config: dict[str, str] | None = None) -> Any:
    """Resolve a BypassStrategy by name."""
    load_extensions()
    name = name or "noop"
    factory = _bypass_factories.get(name)
    if factory is None:
        msg = f"Unsupported bypass strategy: {name}"
        raise ValueError(msg)
    try:
        return factory(bypass_config=bypass_config)
    except TypeError:
        return factory()


def get_all_monitor_entries() -> list[MonitorEntry]:
    """Return all registered monitors sorted by cost."""
    load_extensions()
    return list(_MONITOR_REGISTRY)


def all_monitor_names() -> frozenset[str]:
    load_extensions()
    return frozenset(e.name for e in _MONITOR_REGISTRY)


def rich_monitor_names() -> frozenset[str]:
    load_extensions()
    return frozenset(e.name for e in _MONITOR_REGISTRY if e.rich)


def load_extensions() -> None:
    global _builtins_loaded, _entry_points_loaded
    with _lock:
        if not _builtins_loaded:
            for module_name in (
                "job_ftch.infrastructure.sources.local_fixture",
                "job_ftch.infrastructure.sources.telegram",
                "job_ftch.infrastructure.sources.career_site",
                "job_ftch.infrastructure.sources.career_site_source",
                "job_ftch.infrastructure.sources.declarative",
                "job_ftch.infrastructure.stores.in_memory",
                "job_ftch.infrastructure.stores.sqlite",
                "job_ftch.infrastructure.stores.postgres",
                "job_ftch.infrastructure.stores.job_group_store",
                "job_ftch.infrastructure.llm.heuristic",
                "job_ftch.infrastructure.llm.openai_provider",
                "job_ftch.infrastructure.auth.env_auth",
                "job_ftch.infrastructure.auth.file_auth",
                "job_ftch.infrastructure.auth.vault_auth",
                "job_ftch.sinks.json_file",
                "job_ftch.sinks.telegram_posting",
                "job_ftch.infrastructure.backends.jobs.sqlite",
                "job_ftch.infrastructure.backends.jobs.postgres",
                "job_ftch.infrastructure.embeddings.openai_provider",
                "job_ftch.infrastructure.embeddings.sentence_transformers_provider",
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
                "job_ftch.infrastructure.bypass",
            ):
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
        ):
            for candidate in entry_points(group=group):
                loaded = candidate.load()
                if callable(loaded):
                    loaded()
        _entry_points_loaded = True


def create_source(settings: Settings) -> object:
    load_extensions()
    if settings.source_backend == "career_site":
        from job_ftch.domain.source_spec import CareerSiteSpec

        if settings.career_site_url is None:
            msg = "Career site source requires JOB_FTCH_CAREER_SITE_URL."
            raise ValueError(msg)
        return create_source_from_spec(
            CareerSiteSpec(
                type="career_site",
                url=settings.career_site_url,
                limit=settings.pipeline_max_items_per_run,
            )
        )
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
    try:
        return factory(spec, effective_auth, store)
    except TypeError:
        return factory(spec, effective_auth)


def create_sink(settings: Settings, *, quarantine: bool = False) -> object:
    load_extensions()
    factory = _sink_factories.get(settings.sink_backend)
    if factory is None:
        msg = f"Unsupported sink backend: {settings.sink_backend}"
        raise ValueError(msg)
    return factory(settings if not quarantine else settings.quarantine_settings())


def create_store(settings: Settings) -> object:
    load_extensions()
    factory = _store_factories.get(settings.store_backend)
    if factory is None:
        msg = f"Unsupported store backend: {settings.store_backend}"
        raise ValueError(msg)
    return factory(settings)


_FALLBACK_STORE_BACKEND = "memory"


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

    try:
        primary_store = create_store(settings)
    except Exception as exc:
        if settings.store_fallback_on_error:
            structlog.get_logger("job_ftch.registry").warning(
                "store_creation_failed_falling_back_to_in_memory",
                backend=settings.store_backend,
                error=str(exc),
            )
            return _create_fallback_store(settings)
        raise

    # If it supports StoreConnector (ping), check health
    if isinstance(primary_store, StoreConnector):
        if await primary_store.ping():
            return primary_store

        # Health check failed
        if settings.store_fallback_on_error:
            structlog.get_logger("job_ftch.registry").warning(
                "store_health_check_failed_falling_back_to_in_memory",
                backend=settings.store_backend,
            )
            return _create_fallback_store(settings)

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


def resolve_career_site_parser(*, url: str, html: str) -> CareerSiteParser:
    load_extensions()
    for _, matcher, factory in _parser_factories:
        if matcher(url, html):
            return cast("CareerSiteParser", factory())
    msg = f"Unsupported career site layout for URL: {url}"
    raise ValueError(msg)


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
