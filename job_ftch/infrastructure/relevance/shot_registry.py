"""Process-wide BGE-M3 shot store registry.

The Telegram bot adapter writes user shots here whenever a
``/positive`` / ``/negative`` / ``/positive_job`` / ``/negative_job``
example is added or removed. The pipeline builder reads from the
same store at every ``run()`` call. Both halves share the process
because the bot and the pipeline are launched together; this is
intentional and the same model as the live ``tenant_runner``
already uses for the in-memory store.

The registry is a plain module-level singleton with a per-key
lock. The pipeline and the bot are coroutines on the same event
loop, so a regular ``threading.Lock`` is overkill, but the lock
is kept anyway so multi-threaded test harnesses (e.g.
``asyncio.to_thread`` style code) cannot corrupt the in-memory
list.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.infrastructure.relevance.shot_anchor import (
        BgeMThreeShotProvider,
        InMemoryBgeMThreeShotStore,
    )


_lock = threading.Lock()
_store: InMemoryBgeMThreeShotStore | None = None
_provider: BgeMThreeShotProvider | None = None


def configure(
    *,
    store: InMemoryBgeMThreeShotStore,
    provider: BgeMThreeShotProvider,
) -> None:
    """Bind the singleton. Called once at process startup."""
    global _store, _provider
    with _lock:
        _store = store
        _provider = provider


def get_store() -> InMemoryBgeMThreeShotStore | None:
    with _lock:
        return _store


def get_provider() -> BgeMThreeShotProvider | None:
    with _lock:
        return _provider


def reset() -> None:
    """Drop the singleton. Used by tests that want a clean slate."""
    global _store, _provider
    with _lock:
        _store = None
        _provider = None
