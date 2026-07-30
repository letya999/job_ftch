from __future__ import annotations

import json

from job_ftch.application.builder import (
    resolve_settings_pipeline_item_concurrency,
    resolve_settings_source_count,
    resolve_settings_source_fetch_concurrency,
    tenant_to_settings,
)
from job_ftch.application.concurrency import (
    estimate_source_work_units,
    resolve_pipeline_item_concurrency,
    resolve_source_preparation_concurrency,
)
from job_ftch.config import Settings
from job_ftch.domain import TenantConfig
from job_ftch.domain.source_spec import CareerSiteSpec, LocalFixtureSpec


def test_resolve_pipeline_item_concurrency_fixed_mode_preserves_requested() -> None:
    plan = resolve_pipeline_item_concurrency(
        requested=12,
        source_count=1,
        source_work_units=1,
        store_pool_max=2,
        adaptive=False,
        cpu_count=1,
    )

    assert plan.effective == 12


def test_resolve_pipeline_item_concurrency_adaptive_caps_small_runs() -> None:
    plan = resolve_pipeline_item_concurrency(
        requested=16,
        source_count=1,
        source_work_units=1,
        store_pool_max=32,
        adaptive=True,
        cpu_count=8,
    )

    assert plan.effective == 4


def test_resolve_pipeline_item_concurrency_adaptive_scales_with_sources() -> None:
    plan = resolve_pipeline_item_concurrency(
        requested=16,
        source_count=3,
        source_work_units=6,
        store_pool_max=32,
        adaptive=True,
        cpu_count=8,
    )

    assert plan.effective == 12


def test_estimate_source_work_units_weights_heavy_sources_higher() -> None:
    specs = [
        LocalFixtureSpec(type="local_fixture", path="fixtures/debug/raw_items.json"),
        CareerSiteSpec(type="career_site", url="https://example.com/jobs"),
    ]

    assert estimate_source_work_units(specs) == 4


def test_resolve_source_preparation_concurrency_uses_weighted_units() -> None:
    plan = resolve_source_preparation_concurrency(
        requested=10,
        source_count=2,
        source_work_units=4,
        adaptive=True,
        cpu_count=8,
    )

    assert plan.effective == 4


def test_resolve_settings_source_count_uses_sources_file(tmp_path) -> None:
    source_path = tmp_path / "sources.json"
    source_path.write_text(
        json.dumps(
            {
                "sources": [
                    {"type": "local_fixture", "path": "fixtures/debug/raw_items.json"},
                    {"type": "local_fixture", "path": "fixtures/debug/raw_items.json"},
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "embedding_provider": "none",
            "sources_file_path": source_path,
        }
    )

    assert resolve_settings_source_count(settings) == 2


def test_tenant_to_settings_propagates_adaptive_concurrency_fields() -> None:
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": "fixtures/debug/raw_items.json"}],
            "llm_backend": "heuristic",
            "store_backend": "memory",
            "job_group_store_backend": "memory",
            "job_backend": "memory",
            "search_backend": "memory",
            "source_fetch_concurrency": 8,
            "source_fetch_concurrency_adaptive": False,
            "source_preparation_concurrency": 6,
            "source_preparation_concurrency_adaptive": False,
            "pipeline_item_concurrency": 12,
            "pipeline_item_concurrency_adaptive": False,
        }
    )
    base_settings = Settings.model_validate(
        {"llm_backend": "heuristic", "embedding_provider": "none"}
    )

    settings = tenant_to_settings(tenant, base_settings)

    assert settings.source_fetch_concurrency == 8
    assert settings.source_fetch_concurrency_adaptive is False
    assert settings.source_preparation_concurrency == 6
    assert settings.source_preparation_concurrency_adaptive is False
    assert settings.pipeline_item_concurrency == 12
    assert settings.pipeline_item_concurrency_adaptive is False


def test_resolve_settings_pipeline_item_concurrency_uses_settings_fields() -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "embedding_provider": "none",
            "pipeline_item_concurrency": 16,
            "pipeline_item_concurrency_adaptive": True,
            "store_pool_max": 32,
        }
    )

    effective = resolve_settings_pipeline_item_concurrency(
        settings,
        source_specs=[
            CareerSiteSpec(type="career_site", url="https://example.com/jobs"),
            CareerSiteSpec(type="career_site", url="https://example.com/jobs-2"),
        ],
        source_count=2,
        cpu_count=2,
    )

    assert effective == 8


def test_resolve_settings_source_fetch_concurrency_uses_weighted_sources() -> None:
    settings = Settings.model_validate(
        {
            "llm_backend": "heuristic",
            "embedding_provider": "none",
            "source_fetch_concurrency": 10,
            "source_fetch_concurrency_adaptive": True,
        }
    )

    effective = resolve_settings_source_fetch_concurrency(
        settings,
        source_specs=[
            LocalFixtureSpec(type="local_fixture", path="fixtures/debug/raw_items.json"),
            CareerSiteSpec(type="career_site", url="https://example.com/jobs"),
        ],
        cpu_count=8,
    )

    assert effective == 4
