"""Open registries for sources, sinks, stores, and career-site parsers."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from importlib.metadata import entry_points
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from config import Settings

if TYPE_CHECKING:
    from application.contracts import AuthProvider
    from domain.source_spec import SourceSpec


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
SourceSpecFactory = Callable[[Any, "AuthProvider"], object]
ParserMatcher = Callable[[str, str], bool]
ParserFactory = Callable[[], object]

FSource = TypeVar("FSource", bound=SourceFactory)
FSourceV2 = TypeVar("FSourceV2", bound=SourceSpecFactory)
FSink = TypeVar("FSink", bound=SinkFactory)
FStore = TypeVar("FStore", bound=StoreFactory)
FLLM = TypeVar("FLLM", bound=LLMFactory)
FParser = TypeVar("FParser", bound=ParserFactory)
FAny = TypeVar("FAny", bound=Callable[..., Any])


_source_factories: dict[str, SourceFactory] = {}
_source_spec_factories: dict[str, SourceSpecFactory] = {}
_sink_factories: dict[str, SinkFactory] = {}
_store_factories: dict[str, StoreFactory] = {}
_llm_factories: dict[str, LLMFactory] = {}
_parser_factories: list[tuple[str, ParserMatcher, ParserFactory]] = []

_bypass_factories: dict[str, Callable[..., Any]] = {}
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


def register_source_v2(kind: str) -> Callable[[FSourceV2], FSourceV2]:
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


def load_extensions() -> None:
    global _builtins_loaded, _entry_points_loaded
    with _lock:
        if not _builtins_loaded:
            for module_name in (
                "infrastructure.sources.local_fixture",
                "infrastructure.sources.telegram",
                "infrastructure.sources.career_site",
                "infrastructure.sources.declarative",
                "infrastructure.stores.in_memory",
                "infrastructure.llm.heuristic",
                "infrastructure.llm.openai_provider",
                "sinks.json_file",
                "sinks.telegram_posting",
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


def create_source_from_spec(spec: SourceSpec, auth: AuthProvider | None = None) -> object:
    load_extensions()
    effective_auth: AuthProvider = auth if auth is not None else _NullAuthProvider()
    factory = _source_spec_factories.get(spec.type)
    if factory is None:
        msg = f"Unsupported source type: {spec.type}"
        raise ValueError(msg)
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
