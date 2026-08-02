"""Capability preflight checks at pipeline construction time."""

from __future__ import annotations

import pytest

from job_ftch.application.capabilities import (
    CapabilityError,
    ObservationLedgerCapable,
    OutboxCapable,
    TenantScoped,
    verify_pipeline_capabilities,
)


class _MinimalStore:
    pass


class _OutboxStore:
    async def enqueue_outbox(self, record: object) -> object:
        return record

    async def list_pending_outbox(
        self, limit: int = 100, *, tenant_id: str | None = None
    ) -> tuple[object, ...]:
        return ()

    async def mark_outbox_delivered(self, idempotency_key: str) -> object | None:
        return None


class _LedgerStore:
    async def record_observation(self, entry: object) -> object:
        return entry

    async def get_observation(
        self, stable_id: str, content_hash: str, *, tenant_id: str = "default"
    ) -> object | None:
        return None


class _TenantStore:
    @property
    def tenant_id(self) -> str:
        return "test-tenant"


class _FullStore(_OutboxStore, _LedgerStore, _TenantStore):
    pass


def test_outbox_capable_passes_for_compliant_store() -> None:
    store = _OutboxStore()
    assert isinstance(store, OutboxCapable)
    verify_pipeline_capabilities(store, require_outbox=True)


def test_outbox_capable_rejects_missing_store() -> None:
    with pytest.raises(CapabilityError, match="OutboxCapable"):
        verify_pipeline_capabilities(_MinimalStore(), require_outbox=True)


def test_ledger_capable_passes_for_compliant_store() -> None:
    store = _LedgerStore()
    assert isinstance(store, ObservationLedgerCapable)
    verify_pipeline_capabilities(store, require_ledger=True)


def test_ledger_capable_rejects_missing_store() -> None:
    with pytest.raises(CapabilityError, match="ObservationLedgerCapable"):
        verify_pipeline_capabilities(_MinimalStore(), require_ledger=True)


def test_tenant_scoped_passes_for_compliant_store() -> None:
    store = _TenantStore()
    assert isinstance(store, TenantScoped)
    verify_pipeline_capabilities(store, require_tenant=True)


def test_tenant_scoped_rejects_missing_store() -> None:
    with pytest.raises(CapabilityError, match="TenantScoped"):
        verify_pipeline_capabilities(_MinimalStore(), require_tenant=True)


def test_full_store_passes_all_checks() -> None:
    store = _FullStore()
    verify_pipeline_capabilities(
        store, require_outbox=True, require_ledger=True, require_tenant=True
    )


def test_no_requirements_passes_for_any_store() -> None:
    verify_pipeline_capabilities(_MinimalStore())
