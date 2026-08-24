from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from job_ftch.application import builder as builder_module
from job_ftch.application.pipeline import RunSummary
from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import Settings
from job_ftch.infrastructure.stores.in_memory import InMemoryStore


@pytest.mark.asyncio
async def test_single_tenant_cli_uses_namespaced_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backing = InMemoryStore()

    async def fake_build_store(settings: Settings) -> InMemoryStore:
        del settings
        return backing

    async def fake_job_group_store(settings: Settings) -> object:
        del settings
        return object()

    def fake_build_llm(settings: Settings) -> object:
        del settings
        return object()

    def fake_load_profile_catalog(settings: Settings) -> SimpleNamespace:
        del settings
        return SimpleNamespace(catalog_name="test", profiles=())

    def fake_build_nodes(*args: object, **kwargs: object) -> tuple[object, None, list[object]]:
        store = args[1]
        assert isinstance(store, TenantStore)
        return object(), None, []

    def fake_build_source(settings: Settings, *, store: object) -> object:
        assert isinstance(store, TenantStore)
        assert store._tenant_id == settings.tenant_id  # type: ignore[attr-defined]
        return object()

    def fake_output_sinks(
        settings: Settings, store: object | None = None
    ) -> tuple[object, None, None, None]:
        del settings, store
        return object(), None, None, None

    def fake_rejected_sink(settings: Settings, store: object | None = None) -> tuple[None, object]:
        del settings, store
        return None, object()

    def fake_quarantine_sink(settings: Settings) -> object:
        del settings
        return object()

    async def fake_run_async(
        self: builder_module.PipelineBuilder,
        max_items: int | None = None,
    ) -> RunSummary:
        del max_items
        assert isinstance(self._store, TenantStore)
        await self._store.mark_processed("shared-item")
        return RunSummary(
            source_run_id=f"run-{self._tenant_id}",
            finished_at=datetime.now(UTC),
        )

    monkeypatch.setattr(builder_module, "build_store", fake_build_store)
    monkeypatch.setattr(
        builder_module,
        "create_job_group_store_with_fallback",
        fake_job_group_store,
    )
    monkeypatch.setattr(builder_module, "build_llm", fake_build_llm)
    monkeypatch.setattr(builder_module, "load_profile_catalog", fake_load_profile_catalog)
    monkeypatch.setattr(builder_module, "build_nodes", fake_build_nodes)
    monkeypatch.setattr(builder_module, "build_source", fake_build_source)
    monkeypatch.setattr(builder_module, "build_output_sinks", fake_output_sinks)
    monkeypatch.setattr(builder_module, "build_rejected_sink", fake_rejected_sink)
    monkeypatch.setattr(builder_module, "build_quarantine_sink", fake_quarantine_sink)
    monkeypatch.setattr(builder_module.PipelineBuilder, "run_async", fake_run_async)

    await builder_module.run_pipeline_from_settings(Settings(tenant_id="tenant_a"))
    await builder_module.run_pipeline_from_settings(Settings(tenant_id="tenant_b"))

    assert await backing.set_contains("tenant_a:processed", "shared-item")
    assert await backing.set_contains("tenant_b:processed", "shared-item")
