from __future__ import annotations

import pytest

from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.config import Settings
from job_ftch.domain import TenantConfig
from job_ftch.domain.source_spec import CareerSiteSpec, TelegramChannelSpec


@pytest.mark.asyncio
async def test_tenant_runner_clear_sources_disables_base_and_removes_runtime() -> None:
    settings = Settings(
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
        embedding_enabled=False,
    )
    base_spec = CareerSiteSpec(url="https://example.com/jobs")
    tenant = TenantConfig(
        tenant_id="test_clear_unique_123",
        display_name="Test Clear",
        sources=[base_spec],
        store_backend="memory",
        job_group_store_backend="memory",
        search_backend="sqlite",
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)

    # Add a runtime source
    runtime_spec = TelegramChannelSpec(entity="test_channel")
    await runner.add_source_spec("test_clear_unique_123", runtime_spec, added_via="test")

    # Verify both are available initially
    payloads_before = await runner.list_sources("test_clear_unique_123")
    assert len(payloads_before) == 2
    assert payloads_before[0]["enabled"] is True
    assert payloads_before[1]["enabled"] is True

    # Clear all sources
    await runner.clear_sources("test_clear_unique_123")

    # Verify results
    payloads_after = await runner.list_sources("test_clear_unique_123")

    # Base sources should still exist but be disabled
    # Runtime sources should be removed completely, so only 1 remains in the list
    assert len(payloads_after) == 1
    assert payloads_after[0]["enabled"] is False
    assert "example_com_jobs" in payloads_after[0]["source_id"]

    # If we add another runtime source, it should work fine
    new_runtime_spec = TelegramChannelSpec(entity="another_channel")
    await runner.add_source_spec("test_clear_unique_123", new_runtime_spec, added_via="test")

    payloads_final = await runner.list_sources("test_clear_unique_123")
    assert len(payloads_final) == 2

    enabled_count = sum(1 for p in payloads_final if p["enabled"])
    disabled_count = sum(1 for p in payloads_final if not p["enabled"])
    assert enabled_count == 1
    assert disabled_count == 1
