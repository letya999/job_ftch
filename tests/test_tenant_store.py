from __future__ import annotations

import pytest

from job_ftch.application.tenant_store import TenantStore
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


@pytest.mark.asyncio
async def test_tenant_store_get_set_delegate_with_prefix() -> None:
    backing = InMemoryStore()
    store = TenantStore("tenant_a", backing)

    await store.set("state:key", "value")

    assert await store.get("state:key") == "value"
    assert await backing.get("tenant_a:state:key") == "value"


@pytest.mark.asyncio
async def test_tenant_store_cursor_uses_tenant_prefix() -> None:
    backing = InMemoryStore()
    store = TenantStore("tenant_a", backing)
    cursor = store.incremental_cursor()

    await cursor.set("source-1", "cursor-123")

    assert await backing.get("tenant_a:source-1:cursor") == "cursor-123"
    assert await cursor.get("source-1") == "cursor-123"


@pytest.mark.asyncio
async def test_two_tenants_different_cursors() -> None:
    backing = InMemoryStore()
    tenant_a = TenantStore("tenant_a", backing)
    tenant_b = TenantStore("tenant_b", backing)

    await tenant_a.incremental_cursor().set("shared-source", "cursor-a")
    await tenant_b.incremental_cursor().set("shared-source", "cursor-b")

    assert await tenant_a.incremental_cursor().get("shared-source") == "cursor-a"
    assert await tenant_b.incremental_cursor().get("shared-source") == "cursor-b"
    assert await backing.get("tenant_a:shared-source:cursor") == "cursor-a"
    assert await backing.get("tenant_b:shared-source:cursor") == "cursor-b"
