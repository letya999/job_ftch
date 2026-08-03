"""Nested source/operation budgets and secret-safe route attempt observability."""

from __future__ import annotations

import hashlib
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass

import structlog

from job_ftch.infrastructure.sources.source_deadline import remaining_source_seconds

logger = structlog.get_logger("job_ftch.bypass.attempt_budget")


@dataclass(slots=True)
class OperationState:
    operation_id: str
    kind: str
    max_browser_launches: int
    route_attempts: int = 0
    browser_launches: int = 0
    proxy_rotations: int = 0
    attempts: int = 0
    same_route_retry_count: int = 0
    last_route: tuple[str, str | None, str] | None = None
    started_at: float = 0.0


class AttemptBudget:
    """Enforce source totals plus task-local operation limits."""

    def __init__(
        self,
        *,
        max_route_attempts: int,
        max_source_browser_launches: int,
        max_source_proxy_rotations: int,
        max_weighted_work: int,
        max_proxy_rotations_per_operation: int,
        max_same_route_retries_per_operation: int = 8,
    ) -> None:
        self.max_route_attempts = max_route_attempts
        self.max_source_browser_launches = max_source_browser_launches
        self.max_source_proxy_rotations = max_source_proxy_rotations
        self.max_weighted_work = max_weighted_work
        self.max_proxy_rotations_per_operation = max_proxy_rotations_per_operation
        self.max_same_route_retries_per_operation = max(0, max_same_route_retries_per_operation)
        self.source_browser_launches = 0
        self.source_proxy_rotations = 0
        self.weighted_work = 0
        self._operation: ContextVar[OperationState | None] = ContextVar(
            f"bypass_operation_{id(self)}",
            default=None,
        )

    def start_operation(
        self,
        operation_id: str,
        *,
        kind: str,
        max_browser_launches: int,
    ) -> Token[OperationState | None]:
        return self._operation.set(
            OperationState(
                operation_id=operation_id,
                kind=kind,
                max_browser_launches=max_browser_launches,
                started_at=time.monotonic(),
            )
        )

    def end_operation(self, token: Token[OperationState | None]) -> None:
        try:
            self._operation.reset(token)
        except ValueError:
            # Async generators may be finalized by their consumer in a child
            # context. The operation is already terminal; resetting a token
            # created in another Context would otherwise mask source cleanup.
            logger.debug("bypass_operation_context_already_closed")

    def allow_route_transition(self, *, weight: int) -> bool:
        operation = self._operation.get()
        if operation is not None and operation.route_attempts >= self.max_route_attempts:
            return False
        return self.weighted_work + max(1, weight) <= self.max_weighted_work

    def note_route_transition(self, *, weight: int) -> None:
        operation = self._operation.get()
        if operation is not None:
            operation.route_attempts += 1
        self.weighted_work += max(1, weight)

    def allow_browser_launch(self) -> bool:
        operation = self._operation.get()
        if self.source_browser_launches >= self.max_source_browser_launches:
            return False
        return operation is None or operation.browser_launches < operation.max_browser_launches

    def note_browser_launch(self) -> None:
        operation = self._operation.get()
        if operation is not None:
            operation.browser_launches += 1
        self.source_browser_launches += 1
        self.weighted_work += 10

    def allow_proxy_rotation(self) -> bool:
        operation = self._operation.get()
        if self.source_proxy_rotations >= self.max_source_proxy_rotations:
            return False
        return (
            operation is None or operation.proxy_rotations < self.max_proxy_rotations_per_operation
        )

    def note_proxy_rotation(self) -> None:
        operation = self._operation.get()
        if operation is not None:
            operation.proxy_rotations += 1
        self.source_proxy_rotations += 1
        self.weighted_work += 2

    def same_route_retry_exhausted(self) -> bool:
        operation = self._operation.get()
        if operation is None:
            return False
        return operation.same_route_retry_count >= self.max_same_route_retries_per_operation

    def log_attempt(
        self,
        *,
        source_id: str,
        transport: str,
        browser: str | None,
        network: str,
        failure_kind: str,
        status_code: int | None,
        session_generation: int = 0,
        challenge_action: str = "none",
    ) -> None:
        operation = self._operation.get()
        remaining = remaining_source_seconds()
        route = (transport, browser, network)
        if operation is not None:
            operation.attempts += 1
            if operation.last_route == route:
                operation.same_route_retry_count += 1
            else:
                operation.same_route_retry_count = 0
                operation.last_route = route
        source_hash = hashlib.sha256(source_id.encode()).hexdigest()[:16]
        logger.info(
            "bypass_attempt",
            source_hash=source_hash,
            url_hash=source_hash,
            operation_id=operation.operation_id if operation else "unscoped",
            operation_kind=operation.kind if operation else "unscoped",
            attempt_number=operation.attempts if operation else 1,
            transport=transport,
            browser=browser,
            network=network,
            session_generation=session_generation,
            challenge_action=challenge_action,
            failure_kind=failure_kind,
            status_code=status_code,
            elapsed_ms=(
                round((time.monotonic() - operation.started_at) * 1000) if operation else None
            ),
            remaining_deadline_ms=(round(remaining * 1000) if remaining is not None else None),
            same_route_retry_count=(operation.same_route_retry_count if operation else 0),
            route_transition_count=operation.route_attempts if operation else 0,
            proxy_rotation_count=operation.proxy_rotations if operation else 0,
            browser_launch_count=operation.browser_launches if operation else 0,
        )
