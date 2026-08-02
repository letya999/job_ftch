"""Narrow capability protocols for optional Store features.

These protocols sit atop the existing Store protocol (which is NOT split in
this change - that is TD-036). They replace getattr-based capability
negotiation: the pipeline verifies capabilities once at build time via
isinstance, not per-item via getattr.

@runtime_checkable checks method presence only, not signatures (confirmed
via typing docs). isinstance is called at pipeline build time, not per-item.
Signature correctness is enforced by mypy, not at runtime.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class OutboxCapable(Protocol):
    async def enqueue_outbox(self, record: object) -> object: ...
    async def list_pending_outbox(
        self, limit: int = 100, *, tenant_id: str | None = None
    ) -> tuple[object, ...]: ...
    async def mark_outbox_delivered(self, idempotency_key: str) -> object | None: ...


@runtime_checkable
class ObservationLedgerCapable(Protocol):
    async def record_observation(self, entry: object) -> object: ...
    async def get_observation(
        self, stable_id: str, content_hash: str, *, tenant_id: str = "default"
    ) -> object | None: ...


@runtime_checkable
class TenantScoped(Protocol):
    @property
    def tenant_id(self) -> str: ...


class CapabilityError(TypeError):
    """Raised when a store or stage is missing a required capability."""


def verify_pipeline_capabilities(
    store: object,
    *,
    require_outbox: bool = False,
    require_ledger: bool = False,
    require_tenant: bool = False,
) -> list[str]:
    """Check store capabilities at build time. Returns list of warnings."""
    warnings: list[str] = []
    store_name = type(store).__name__
    if require_outbox and not isinstance(store, OutboxCapable):
        raise CapabilityError(
            f"{store_name} does not implement OutboxCapable "
            f"(missing enqueue_outbox / list_pending_outbox / mark_outbox_delivered)"
        )
    if require_ledger and not isinstance(store, ObservationLedgerCapable):
        raise CapabilityError(
            f"{store_name} does not implement ObservationLedgerCapable "
            f"(missing record_observation / get_observation)"
        )
    if require_tenant and not isinstance(store, TenantScoped):
        raise CapabilityError(
            f"{store_name} does not implement TenantScoped (missing tenant_id property)"
        )
    return warnings
