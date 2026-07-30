"""Per-source wall-clock budget propagated through career-site I/O."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

_deadline_at: ContextVar[float | None] = ContextVar("career_site_deadline_at", default=None)


def set_source_deadline(deadline_at: float) -> Token[float | None]:
    """Set the absolute monotonic deadline for the current source task."""
    return _deadline_at.set(deadline_at)


def reset_source_deadline(token: Token[float | None]) -> None:
    _deadline_at.reset(token)


def remaining_source_seconds() -> float | None:
    deadline_at = _deadline_at.get()
    if deadline_at is None:
        return None
    return max(0.0, deadline_at - asyncio.get_running_loop().time())


async def await_with_source_deadline[T](awaitable: Awaitable[T]) -> T:
    """Await an operation without allowing it to outlive the source budget."""
    remaining = remaining_source_seconds()
    if remaining is None:
        return await awaitable
    if remaining <= 0:
        # Callers commonly create a coroutine immediately before delegating
        # its deadline check here.  Closing it prevents an unawaited-coroutine
        # warning when the source budget was exhausted while waiting for a
        # shared resource (for example the browser pool).
        close = getattr(awaitable, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
        raise TimeoutError("source deadline exhausted")
    # Do not use ``asyncio.timeout`` directly around a Patchright operation.
    # It cancels this task while the driver still owns a response future; when
    # that future subsequently fails during page/context shutdown, Windows
    # reports "Future exception was never retrieved".  Owning a child task
    # lets us cancel *and drain* the operation before returning the deadline
    # error to the caller.
    task = asyncio.ensure_future(awaitable)

    def _consume_late_exception(completed: asyncio.Future[T]) -> None:
        if completed.cancelled():
            return
        with suppress(Exception):
            completed.exception()

    task.add_done_callback(_consume_late_exception)
    try:
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if task in done:
            return task.result()
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        raise TimeoutError("source deadline exhausted")
    except asyncio.CancelledError:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        raise


async def sleep_with_source_deadline(seconds: float) -> None:
    remaining = remaining_source_seconds()
    if remaining is not None:
        if remaining <= 0:
            raise TimeoutError("source deadline exhausted")
        seconds = min(seconds, remaining)
    await asyncio.sleep(seconds)


@asynccontextmanager
async def source_deadline_scope(deadline_at: float) -> AsyncIterator[None]:
    token = set_source_deadline(deadline_at)
    try:
        yield
    finally:
        reset_source_deadline(token)
