"""Open registries for sources, sinks, stores, and career-site parsers."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from importlib.metadata import entry_points
from threading import Lock
from typing import Any, Protocol, cast

from config import Settings


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
ParserMatcher = Callable[[str, str], bool]
ParserFactory = Callable[[], object]

_source_factories: dict[str, SourceFactory] = {}
_sink_factories: dict[str, SinkFactory] = {}
_store_factories: dict[str, StoreFactory] = {}
_llm_factories: dict[str, LLMFactory] = {}
_parser_factories: list[tuple[str, ParserMatcher, ParserFactory]] = []
_lock = Lock()
_builtins_loaded = False
_entry_points_loaded = False


def register_source(kind: str) -> Callable[[SourceFactory], SourceFactory]:
    normalized = kind.strip()

    def decorator(factory: SourceFactory) -> SourceFactory:
        _source_factories[normalized] = factory
        return factory

    return decorator


def register_sink(kind: str) -> Callable[[SinkFactory], SinkFactory]:
    normalized = kind.strip()

    def decorator(factory: SinkFactory) -> SinkFactory:
        _sink_factories[normalized] = factory
        return factory

    return decorator


def register_store(kind: str) -> Callable[[StoreFactory], StoreFactory]:
    normalized = kind.strip()

    def decorator(factory: StoreFactory) -> StoreFactory:
        _store_factories[normalized] = factory
        return factory

    return decorator


def register_llm(kind: str) -> Callable[[LLMFactory], LLMFactory]:
    normalized = kind.strip()

    def decorator(factory: LLMFactory) -> LLMFactory:
        _llm_factories[normalized] = factory
        return factory

    return decorator


def register_parser(
    kind: str,
    *,
    matcher: ParserMatcher,
) -> Callable[[ParserFactory], ParserFactory]:
    normalized = kind.strip()

    def decorator(factory: ParserFactory) -> ParserFactory:
        _parser_factories.append((normalized, matcher, factory))
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
