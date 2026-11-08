"""Provider-neutral continuation signal and per-item session context."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

llm_session: ContextVar[str] = ContextVar("llm_session", default="")


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

    def __init__(self, request_id: str, retry_at: datetime) -> None:
        super().__init__(f"LLM request pending: {request_id}")
        self.request_id = request_id
        self.retry_at = retry_at


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
            if isinstance(body, dict) and body.get("code") == "llm_pending":
                return LLMRequestDeferredError(
                    str(body["request_id"]), datetime.fromisoformat(str(body["retry_at"]))
                )
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None
