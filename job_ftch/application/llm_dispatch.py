"""Provider-neutral continuation signal and per-item session context."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from typing import Protocol

    from job_ftch.config import Settings

    class LLMRunContext(Protocol):
        async def __aenter__(self) -> LLMRunContext: ...
        async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...
        async def finish(self, summary: dict[str, Any]) -> None: ...


llm_session: ContextVar[str] = ContextVar("llm_session", default="")
llm_run: ContextVar[str] = ContextVar("llm_run", default="")


def uses_cliproxy_runs(settings: Settings) -> bool:
    profiles = getattr(settings, "llm_provider_profiles", {})
    return any(
        binding.capability != "vision"
        and getattr(profiles.get(binding.provider), "cliproxy_dispatch", False)
        for binding in getattr(settings, "llm_bindings", {}).values()
    )


def run_session_identifier(tenant: str, run_id: str) -> str:
    return sha256(f"{tenant}:run:{run_id}".encode()).hexdigest()


@asynccontextmanager
async def llm_deadline(seconds: float, provider: object) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError as exc:
        if not getattr(provider, "durable_dispatch", False):
            raise
        raise LLMRequestDeferredError(
            llm_run.get() or "deadline",
            datetime.now(UTC) + timedelta(seconds=30),
            reason="queue_wait_timeout",
        ) from exc


def session_identifier(tenant: str, body: bytes) -> str:
    identity = llm_session.get() or sha256(body).hexdigest()
    return sha256(f"{tenant}:{identity}".encode()).hexdigest()


@contextmanager
def llm_item_session(value: str) -> Iterator[None]:
    token = llm_session.set(value)
    try:
        yield
    finally:
        llm_session.reset(token)


class LLMRequestDeferredError(RuntimeError):
    """A durable request is pending; callers must preserve the observation."""

    def __init__(
        self,
        request_id: str,
        retry_at: datetime,
        *,
        reason: str = "llm_pending",
        requires_operator: bool = False,
    ) -> None:
        super().__init__(f"LLM request pending: {request_id}")
        self.request_id = request_id
        self.retry_at = retry_at
        self.reason = reason
        self.requires_operator = requires_operator
        self.settlement_item_ids: tuple[str, ...] = ()


def deferred_from_error(error: BaseException) -> LLMRequestDeferredError | None:
    """Unwrap SDK/Instructor exceptions without importing either dependency."""
    seen: set[int] = set()
    current: Any = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LLMRequestDeferredError):
            return current
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            body = body.get("error", body)
            if isinstance(body, dict) and body.get("code") in {"llm_pending", "llm_deferred"}:
                return LLMRequestDeferredError(
                    str(body["request_id"]),
                    datetime.fromisoformat(str(body["retry_at"])),
                    reason=str(body.get("reason") or "llm_pending"),
                    requires_operator=bool(body.get("requires_operator")),
                )
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None
