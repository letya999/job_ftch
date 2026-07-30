"""Store adapter implementations.

Convention:
  - `InMemoryStore` is the convenience import: the most common store in
    tests, smoke runs, and `--help` paths.
  - `SQLiteStore` and `PostgresStore` (and `JobGroupStore`) are reachable
    through the **registry**, not through direct import. Use
    `get_store_class("sqlite")` or `create_store(settings)` so the
    plugin-discovery path (per ADR-007) is the single source of truth.
  - `list_stores()` returns the tuple of registered backend names for
    introspection (e.g. `job_ftch stores list`).
"""

from job_ftch.infrastructure.stores.in_memory import InMemoryStore

__all__ = ["InMemoryStore", "get_store_class", "list_stores"]


def get_store_class(kind: str):  # type: ignore
    """Return the registered store class for the given backend `kind`.

    Equivalent to `from job_ftch.application.registry import get_store_class`,
    but exposed here so callers do not have to reach into the application
    layer for a plugin lookup.
    """
    from job_ftch.application.registry import get_store_class as _get

    return _get(kind)


def list_stores() -> tuple[str, ...]:
    """Return the tuple of registered store backend names."""
    from job_ftch.application.registry import list_stores as _list

    return _list()
