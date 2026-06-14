from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PluginKind(StrEnum):
    SOURCE         = "source"
    SINK           = "sink"
    NODE           = "node"
    STORE          = "store"
    JOB_GROUP_STORE = "job_group_store"
    BYPASS         = "bypass"
    LLM            = "llm"
    EMBEDDING      = "embedding"
    VECTOR         = "vector"
    JOB_BACKEND    = "job_backend"
    SEARCH_BACKEND = "search_backend"
    AUTH           = "auth"
    MONITOR        = "monitor"
    SCRAPER        = "scraper"
    PARSER         = "parser"
    RERANKER       = "reranker"

class PluginState(StrEnum):
    PENDING  = "pending"   # registered, factory not yet called
    ACTIVE   = "active"    # factory called successfully at least once
    DISABLED = "disabled"  # ImportError (missing optional extras) — not an error
    ERROR    = "error"     # unexpected exception during factory call

@dataclass(frozen=True)
class PluginDescriptor:
    name: str
    kind: PluginKind
    factory: Callable[..., Any]
    version: str = "0.0.0"
    description: str = ""
    requires_extras: tuple[str, ...] = field(default_factory=tuple)
    entry_point_group: str = ""

@dataclass
class PluginEntry:
    descriptor: PluginDescriptor
    state: PluginState = PluginState.PENDING
    error: Exception | None = None

class PluginNotFound(KeyError):
    """No plugin with the given kind+name is registered."""

class DuplicatePlugin(ValueError):
    """A plugin with the same kind+name was already registered."""
