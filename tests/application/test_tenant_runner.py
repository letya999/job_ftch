from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from job_ftch.application import tenant_runner as tenant_runner_module
from job_ftch.application.auth import resolve_auth_provider
from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.profile_inputs import build_candidate_profile_from_payload
from job_ftch.application.registry import create_auth_provider
from job_ftch.application.source_assessment import store_source_assessment
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import (
    TenantRunner,
    _update_source_health_payload,
)
from job_ftch.cli import _handle_tenants, _merge_run_summaries
from job_ftch.config import Settings
from job_ftch.domain import (
    JobLineage,
    ManagedCandidateProfile,
    RawItem,
    SourceHealth,
    SourceKind,
    TenantConfig,
    source_spec_identifier,
)
from job_ftch.domain.source_assessment import (
    AssessmentConfidence,
    FreshnessAssessment,
    SourceAssessmentResult,
    SourceCapabilities,
)
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider

if TYPE_CHECKING:
    from pathlib import Path


def _isolated_base_settings() -> Settings:
    return Settings.model_validate(
        {
            "bgem3_enabled": False,
            "embedding_enabled": False,
            "embedding_prefilter_enabled": False,
            "embedding_provider": "none",
            "relevance_backend": "keywords",
            "relevance_shot_backend": "memory",
        }
    )


@pytest.fixture(autouse=True)
def _isolate_default_tenant_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Never let tenant tests mutate the workspace's runtime artifacts."""
    original = tenant_runner_module.tenant_to_settings

    def isolated(tenant: TenantConfig, base_settings: Settings | None = None) -> Settings:
        settings = original(tenant, base_settings)
        root = tmp_path / "tenant_outputs" / tenant.tenant_id
        updates: dict[str, Path] = {}
        if "output" not in tenant.model_fields_set:
            updates["output_path"] = root / "jobs.json"
        if "review_output" not in tenant.model_fields_set:
            updates["review_output_path"] = root / "review.jsonl"
        if "rejected_output" not in tenant.model_fields_set:
            updates["rejected_output_path"] = root / "rejected.jsonl"
        if "quarantine_output" not in tenant.model_fields_set:
            updates["quarantine_output_path"] = root / "quarantine.jsonl"
        return settings.model_copy(update=updates)

    monkeypatch.setattr(tenant_runner_module, "tenant_to_settings", isolated)


def _write_fixture(path: Path) -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="1",
        text=(
            "Senior machine learning engineer\n"
            "Remote\n"
            "Company: OpenAI\n"
            "Python, llm, pytorch\n"
            "Salary: USD 100000 - 150000"
        ),
        metadata={"company": "OpenAI", "title": "Senior machine learning engineer"},
    )
    path.write_text(json.dumps([item.model_dump(mode="json")]), encoding="utf-8")


class _AcceptingHeuristicLLMProvider(HeuristicLLMProvider):
    async def classify(self, _prompt: str, _schema: type[Any]) -> object:
        return SimpleNamespace(
            decision="accept",
            confidence=0.95,
            reasoning="deterministic test fixture",
            matched_positive_aspects=(),
            mismatched_aspects=(),
        )


def test_auth_provider_resolution_via_registry() -> None:
    settings = Settings()

    provider = resolve_auth_provider("env", settings=settings)
    default_provider = create_auth_provider(None, settings)

    assert isinstance(provider, EnvAuthProvider)
    assert isinstance(default_provider, EnvAuthProvider)

    with pytest.raises(ValueError, match="Unsupported auth provider"):
        create_auth_provider("unknown", settings)

    with pytest.raises(ValueError, match="auth_file_path is required"):
        create_auth_provider("file", settings)


def test_load_tenants_supports_directory_and_aggregate_file(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()

    single_file = configs_dir / "single.yaml"
    single_file.write_text(
        "\n".join(
            [
                "tenant_id: ai_jobs",
                "display_name: AI Jobs",
                "sources:",
                "  - type: local_fixture",
                f"    path: {fixture_path.as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    aggregate_file = configs_dir / "many.json"
    aggregate_file.write_text(
        json.dumps(
            {
                "tenants": [
                    {
                        "tenant_id": "ml_jobs",
                        "display_name": "ML Jobs",
                        "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    tenants = load_tenants(configs_dir)

    assert [tenant.tenant_id for tenant in tenants] == ["ml_jobs", "ai_jobs"]


@pytest.mark.asyncio
async def test_run_all_propagates_operator_limits_to_each_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenants = [
        TenantConfig(tenant_id="t1", display_name="Tenant 1", sources=[]),
        TenantConfig(tenant_id="t2", display_name="Tenant 2", sources=[]),
    ]
    runner = TenantRunner.from_tenants(tenants, base_settings=_isolated_base_settings())
    calls: list[dict[str, object]] = []

    async def fake_run_tenant(
        tenant_id: str,
        *,
        max_items: int | None = None,
        user_id: str | None = None,
        source_ids: list[str] | None = None,
    ) -> RunSummary:
        calls.append(
            {
                "tenant_id": tenant_id,
                "max_items": max_items,
                "user_id": user_id,
                "source_ids": source_ids,
            }
        )
        return RunSummary(tenant_id=tenant_id, source_run_id=f"run-{tenant_id}")

    monkeypatch.setattr(runner, "run_tenant", fake_run_tenant)

    summaries = await runner.run_all(concurrency=1, max_items=3, user_id="operator-1")

    assert [summary.tenant_id for summary in summaries] == ["t1", "t2"]
    assert calls == [
        {
            "tenant_id": "t1",
            "max_items": 3,
            "user_id": "operator-1",
            "source_ids": None,
        },
        {
            "tenant_id": "t2",
            "max_items": 3,
            "user_id": "operator-1",
            "source_ids": None,
        },
    ]


@pytest.mark.asyncio
async def test_strategy_roundtrip_memory_backend() -> None:
    from job_ftch.application.tenant_runner import TenantStore
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    store = TenantStore("tenant1", InMemoryStore())

    await store.save_source_strategy("example.com", "playwright", "noop")
    result = await store.get_source_strategy("example.com")
    assert result == {"monitor": "playwright", "bypass": "noop"}

    missing = await store.get_source_strategy("missing.com")
    assert missing is None


@pytest.mark.asyncio
async def test_tenant_store_run_history_rolls_back_on_index_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.tenant_runner import TenantStore
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    backing = InMemoryStore()
    store = TenantStore("tenant1", backing)
    summary = RunSummary(source_run_id="run-1")
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "tenant1:run_history_ids":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.save_run_summary(summary)

    assert await store.get_run_summary("run-1") is None
    assert await store.list_run_summaries(limit=10) == []


@pytest.mark.asyncio
async def test_tenant_store_runtime_source_rolls_back_on_index_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.tenant_runner import TenantStore
    from job_ftch.domain import RuntimeSourceRecord
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    backing = InMemoryStore()
    store = TenantStore("tenant1", backing)
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    record = RuntimeSourceRecord(
        source_id=source_spec_identifier(spec),
        spec=spec,
        enabled=True,
        added_via="test",
    )
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "tenant1:runtime_source_ids":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.save_runtime_source(record)

    assert await store.get_runtime_source(record.source_id) is None
    assert await store.list_runtime_sources() == []


@pytest.mark.asyncio
async def test_tenant_store_source_disabled_rolls_back_on_index_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.tenant_runner import TenantStore
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    backing = InMemoryStore()
    store = TenantStore("tenant1", backing)
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "tenant1:source_disabled_ids":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.set_source_disabled("career_site:example", True)

    assert await store.list_disabled_source_ids() == set()


@pytest.mark.asyncio
async def test_tenant_store_source_enabled_ignores_index_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.tenant_runner import TenantStore
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    backing = InMemoryStore()
    store = TenantStore("tenant1", backing)
    await store.set_source_disabled("career_site:example", True)
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "tenant1:source_disabled_ids":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    await store.set_source_disabled("career_site:example", False)

    assert await store.list_disabled_source_ids() == set()


@pytest.mark.asyncio
async def test_tenant_store_source_health_rolls_back_on_index_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.tenant_runner import TenantStore
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    backing = InMemoryStore()
    store = TenantStore("tenant1", backing)
    health = SourceHealth(
        source_id="career_site:example",
        source_kind="career_site",
        source_name="example",
        last_run_at="2026-06-30T00:00:00+00:00",
        last_success_at=None,
        failure_streak=1,
        success_count=0,
        last_fetched=0,
        last_emitted=0,
        last_failed=1,
        last_quarantined=0,
        baseline_emitted=0.0,
        drift_ratio=None,
        degraded=True,
        status="error",
    )
    original_set_add = backing.set_add

    async def _flaky_set_add(key: str, member: str) -> None:
        if key == "tenant1:source_health_ids":
            raise OSError("index down")
        await original_set_add(key, member)

    monkeypatch.setattr(backing, "set_add", _flaky_set_add)

    with pytest.raises(OSError, match="index down"):
        await store.save_source_health("career_site:example", health)

    assert await store.get_source_health("career_site:example") is None
    assert await store.list_source_health() == []


@pytest.mark.asyncio
async def test_tenant_store_clear_runtime_sources_rolls_back_on_partial_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_ftch.application.tenant_runner import TenantStore
    from job_ftch.domain import RuntimeSourceRecord
    from job_ftch.infrastructure.stores.in_memory import InMemoryStore

    backing = InMemoryStore()
    store = TenantStore("tenant1", backing)
    spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    record = RuntimeSourceRecord(
        source_id=source_spec_identifier(spec),
        spec=spec,
        enabled=True,
        added_via="test",
    )
    await store.save_runtime_source(record)
    original_delete = backing.delete

    async def _flaky_delete(key: str) -> None:
        if key == f"tenant1:runtime_source:{record.source_id}":
            raise OSError("delete down")
        await original_delete(key)

    monkeypatch.setattr(backing, "delete", _flaky_delete)

    with pytest.raises(OSError, match="delete down"):
        await store.clear_runtime_sources()

    assert await store.get_runtime_source(record.source_id) == record
    assert await store.list_runtime_sources() == [record]


@pytest.mark.asyncio
async def test_tenant_runner_namespaces_status_and_reset(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())

    summary = await runner.run_tenant("ai_jobs")
    status = await runner.get_status("ai_jobs")
    store = runner.get_runtime("ai_jobs").store

    assert summary.tenant_id == "ai_jobs"
    assert status is not None
    assert status.tenant_id == "ai_jobs"
    assert await store.get_run_state("pipeline.status") == "completed"

    await runner.reset_tenant("ai_jobs")

    assert await store.get_run_state("pipeline.status") is None

    await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_can_scope_a_clean_canary_to_explicit_source_ids(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    _write_fixture(first_path)
    _write_fixture(second_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [
                {"type": "local_fixture", "path": first_path.as_posix()},
                {"type": "local_fixture", "path": second_path.as_posix()},
            ],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    selected_source_id = source_spec_identifier(tenant.sources[1])

    summary = await runner.run_tenant("ai_jobs", source_ids=(selected_source_id,))

    assert summary.fetched == 1
    assert {str(outcome["source_id"]) for outcome in summary.source_outcomes} == {
        selected_source_id
    }
    await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_builds_job_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    profile_path = tmp_path / "profiles.yaml"
    profile_path.write_text(
        """catalog_name: lineage_test
profiles:
  - profile_id: ml
    name: ML Engineer
    target_roles: [\"ML Engineer\"]
    preferred_skills: [\"ML\"]
""",
        encoding="utf-8",
    )
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "filter_profile_path": str(profile_path),
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )
    runner.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()

    await runner.run_tenant("ai_jobs")
    jobs = await runner.latest_jobs("ai_jobs", limit=1)
    lineage = await runner.get_job_lineage(jobs[0].job_id, tenant_id="ai_jobs")

    assert lineage is not None
    assert lineage.tenant_id == "ai_jobs"
    assert lineage.job_id == jobs[0].job_id
    assert lineage.raw_item_id == jobs[0].raw_item_id
    assert lineage.source_record_id == "1#0"
    assert lineage.source_run_id is not None
    assert "extraction" in lineage.pipeline_stages

    await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_persists_run_history(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())

    first = await runner.run_tenant("ai_jobs")
    second = await runner.run_tenant("ai_jobs")
    history = await runner.list_runs(tenant_id="ai_jobs", limit=10)
    loaded = await runner.get_run(first.source_run_id or "", tenant_id="ai_jobs")

    assert first.source_run_id is not None
    assert second.source_run_id is not None
    assert len(history) == 2
    assert history[0].source_run_id == second.source_run_id
    assert history[1].source_run_id == first.source_run_id
    assert loaded is not None
    assert loaded.source_run_id == first.source_run_id
    assert loaded.tenant_id == "ai_jobs"
    health = await runner.get_runtime("ai_jobs").store.get_source_health("debug:fixture")
    assert health is not None
    assert health.status == "healthy"

    assert health.success_count == 2
    assert health.failure_streak == 0

    await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_prepares_sources_with_bounded_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [
                {"type": "local_fixture", "path": fixture_path.as_posix(), "source_name": "src-1"},
                {"type": "local_fixture", "path": fixture_path.as_posix(), "source_name": "src-2"},
                {"type": "local_fixture", "path": fixture_path.as_posix(), "source_name": "src-3"},
            ],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    settings = _isolated_base_settings()
    settings.source_preparation_concurrency = 2
    runner = TenantRunner.from_tenants([tenant], base_settings=settings)

    release = asyncio.Event()
    active = 0
    max_active = 0
    started = {
        "local_fixture:src-1": asyncio.Event(),
        "local_fixture:src-2": asyncio.Event(),
        "local_fixture:src-3": asyncio.Event(),
    }

    class _FakeAssessmentService:
        async def assess_and_store(self, spec, store, *, ttl_days: int | None = None):
            del ttl_days
            nonlocal active, max_active
            sid = source_spec_identifier(spec)
            active += 1
            max_active = max(max_active, active)
            started[sid].set()
            try:
                await release.wait()
                result = SourceAssessmentResult(
                    source_id=sid,
                    source_type=spec.type,
                    capabilities=SourceCapabilities(has_publication_time=False),
                    freshness=FreshnessAssessment(
                        confidence=AssessmentConfidence.HIGH,
                        can_detect_freshness_without_snapshot=False,
                        can_filter_since_yesterday=False,
                        item_level_dates=False,
                        requires_full_snapshot=True,
                        rationale="test",
                    ),
                )
                await store_source_assessment(store, result)
                return result
            finally:
                active -= 1

    async def _fake_build_runtime_catalog(self, runtime, *, user_id=None):
        del self, runtime, user_id
        return SimpleNamespace(catalog_name="test"), None

    async def _fake_build_runtime_builder(
        self,
        runtime,
        *,
        effective_sources,
        catalog,
        run_id,
        user_id=None,
        relevance_prompts=None,
    ):
        del self, runtime, effective_sources, catalog, run_id, user_id, relevance_prompts

        class _FakeBuilder:
            async def run_async(self, *, max_items=None):
                del max_items
                summary = RunSummary()
                summary.source_run_id = "run-1"
                return summary

        class _FakeSnapshot:
            def set_run_id(self, run_id):
                del run_id

            async def save_and_purge(self):
                return None

        return _FakeBuilder(), _FakeSnapshot()

    async def _fake_update_source_health(self, runtime, summary):
        del runtime, summary
        return None

    monkeypatch.setattr(
        "job_ftch.application.tenant_runner.create_source_assessment_service",
        lambda: _FakeAssessmentService(),
    )
    monkeypatch.setattr(TenantRunner, "_build_runtime_catalog", _fake_build_runtime_catalog)
    monkeypatch.setattr(TenantRunner, "_build_runtime_builder", _fake_build_runtime_builder)
    monkeypatch.setattr(TenantRunner, "_update_source_health", _fake_update_source_health)

    task = asyncio.create_task(runner.run_tenant("ai_jobs"))
    await asyncio.gather(
        started["local_fixture:src-1"].wait(),
        started["local_fixture:src-2"].wait(),
    )
    await asyncio.sleep(0)

    assert max_active == 2
    assert not started["local_fixture:src-3"].is_set()

    release.set()
    await task
    await runner.close()


def test_merge_run_summaries_accumulates_counts_and_source_stats() -> None:
    first = RunSummary(
        fetched=2,
        emitted=1,
        failed=0,
        drop_reasons={"already_processed": 1},
    )
    first.source_stats("telegram_channel").fetched = 2
    first.source_stats("telegram_channel").emitted = 1
    first_identity = first.source_identity_stats("telegram_channel", "tg_ai_jobs")
    assert first_identity is not None
    first_identity.emitted = 1

    second = RunSummary(
        fetched=3,
        emitted=2,
        failed=1,
        quarantine_reasons={"policy": 2},
    )
    second.source_stats("career_site").fetched = 3
    second.source_stats("career_site").failed = 1
    second_identity = second.source_identity_stats("career_site", "bcc_ml")
    assert second_identity is not None
    second_identity.failed = 1

    merged = _merge_run_summaries([first, second])

    assert merged.fetched == 5
    assert merged.emitted == 3
    assert merged.failed == 1
    assert merged.drop_reasons == {"already_processed": 1}
    assert merged.quarantine_reasons == {"policy": 2}
    assert merged.by_source_kind["telegram_channel"].emitted == 1
    assert merged.by_source_kind["career_site"].failed == 1
    assert merged.by_source_id["telegram_channel:tg_ai_jobs"].emitted == 1
    assert merged.by_source_id["career_site:bcc_ml"].failed == 1


@pytest.mark.asyncio
async def test_tenant_runner_lists_source_health(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())

    try:
        await runner.run_tenant("ai_jobs")
        payloads = await runner.list_source_health("ai_jobs")
        assert len(payloads) == 1
        assert payloads[0]["source_id"] == "debug:fixture"
        assert payloads[0]["status"] == "healthy"
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_persists_runtime_sources_and_disables_them(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runtime_spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    source_id = source_spec_identifier(runtime_spec)

    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    try:
        added = await runner.add_source_spec("ai_jobs", runtime_spec, added_via="test")
        listed = await runner.list_sources("ai_jobs")
        tenants = await runner.list_tenants()

        assert added["source_id"] == source_id
        assert any(
            item["source_id"] == source_id and item["origin"] == "runtime" for item in listed
        )
        assert tenants[0].source_count == 2
    finally:
        await runner.close()

    reloaded = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    try:
        listed = await reloaded.list_sources("ai_jobs")
        assert any(item["source_id"] == source_id and item["enabled"] is True for item in listed)

        disabled = await reloaded.disable_source("ai_jobs", source_id)
        listed_after_disable = await reloaded.list_sources("ai_jobs")
        tenants = await reloaded.list_tenants()

        assert disabled["status"] == "disabled"
        assert any(
            item["source_id"] == source_id and item["status"] == "disabled"
            for item in listed_after_disable
        )
        assert tenants[0].source_count == 1
    finally:
        await reloaded.close()


@pytest.mark.asyncio
async def test_tenant_runner_remove_runtime_source_keeps_config_sources(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runtime_spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    source_id = source_spec_identifier(runtime_spec)
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    try:
        await runner.add_source_spec("ai_jobs", runtime_spec, added_via="test")
        removed = await runner.remove_source("ai_jobs", source_id)
        listed = await runner.list_sources("ai_jobs")
        config_id = next(item["source_id"] for item in listed if item.get("origin") == "config")
        config_block = await runner.remove_source("ai_jobs", config_id)
        assert removed["status"] == "removed"
        assert all(item["source_id"] != source_id for item in listed)
        assert config_block["status"] == "unsupported"
        assert config_block["error"] == "config_source_not_deletable"
        assert any(item["source_id"] == config_id for item in listed)
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_update_source_runtime_and_config_rules(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runtime_spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
        limit=20,
    )
    source_id = source_spec_identifier(runtime_spec)
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    try:
        await runner.add_source_spec("ai_jobs", runtime_spec, added_via="test")
        patched = await runner.update_source(
            "ai_jobs",
            source_id,
            {"enabled": False, "limit": 7},
        )
        assert patched.get("enabled") is False
        assert patched["spec"]["limit"] == 7
        unknown = await runner.update_source("ai_jobs", source_id, {"parser": "generic"})
        assert unknown["error"] == "invalid_arguments"
        listed = await runner.list_sources("ai_jobs")
        config_id = next(item["source_id"] for item in listed if item.get("origin") == "config")
        config_limit = await runner.update_source("ai_jobs", config_id, {"limit": 2})
        assert config_limit["status"] == "unsupported"
        assert config_limit["error"] == "config_limit_not_updatable"
        config_disabled = await runner.update_source("ai_jobs", config_id, {"enabled": False})
        assert config_disabled.get("enabled") is False
        with pytest.raises(KeyError):
            await runner.update_source("ai_jobs", "missing-source", {"enabled": True})
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_list_sources_deduplicates_runtime_copy_of_config_source(
    tmp_path: Path,
) -> None:
    from job_ftch.domain import RuntimeSourceRecord

    base_spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [base_spec.model_dump(mode="json")],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    source_id = source_spec_identifier(base_spec)

    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    try:
        runtime = runner.get_runtime("ai_jobs")
        await runtime.store.save_runtime_source(
            RuntimeSourceRecord(source_id=source_id, spec=base_spec, added_via="legacy")
        )
        await runner._reload_runtime_sources(runtime)
        listed = await runner.list_sources("ai_jobs")

        matching = [item for item in listed if item["source_id"] == source_id]
        assert len(matching) == 1
        assert matching[0]["origin"] == "config"
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_add_source_spec_rolls_back_runtime_state_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runtime_spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    source_id = source_spec_identifier(runtime_spec)
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    runtime = runner.get_runtime("ai_jobs")
    original_set_source_disabled = runtime.store.set_source_disabled

    async def _flaky_set_source_disabled(current_source_id: str, disabled: bool) -> None:
        if current_source_id == source_id and disabled is False:
            raise OSError("disable index down")
        await original_set_source_disabled(current_source_id, disabled)

    monkeypatch.setattr(runtime.store, "set_source_disabled", _flaky_set_source_disabled)
    try:
        with pytest.raises(OSError, match="disable index down"):
            await runner.add_source_spec("ai_jobs", runtime_spec, added_via="test")

        listed = await runner.list_sources("ai_jobs")
        assert all(item["source_id"] != source_id for item in listed)
        assert await runtime.store.get_runtime_source(source_id) is None
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_disable_source_rolls_back_runtime_record_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runtime_spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    source_id = source_spec_identifier(runtime_spec)
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    try:
        await runner.add_source_spec("ai_jobs", runtime_spec, added_via="test")
        runtime = runner.get_runtime("ai_jobs")
        original_set_source_disabled = runtime.store.set_source_disabled

        async def _flaky_set_source_disabled(current_source_id: str, disabled: bool) -> None:
            if current_source_id == source_id and disabled is True:
                raise OSError("disable persist down")
            await original_set_source_disabled(current_source_id, disabled)

        monkeypatch.setattr(runtime.store, "set_source_disabled", _flaky_set_source_disabled)

        with pytest.raises(OSError, match="disable persist down"):
            await runner.disable_source("ai_jobs", source_id)

        listed = await runner.list_sources("ai_jobs")
        restored = next(item for item in listed if item["source_id"] == source_id)
        assert restored["enabled"] is True
        assert restored["status"] != "disabled"
        assert (await runtime.store.get_runtime_source(source_id)).enabled is True
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_clear_sources_restores_state_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runtime_spec = CareerSiteSpec(
        type="career_site",
        url="https://example.com/jobs",
        source_name="example_com_jobs",
    )
    source_id = source_spec_identifier(runtime_spec)
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    try:
        await runner.add_source_spec("ai_jobs", runtime_spec, added_via="test")
        runtime = runner.get_runtime("ai_jobs")
        base_source_id = source_spec_identifier(runtime.base_sources[0])
        original_set_source_disabled = runtime.store.set_source_disabled

        async def _flaky_set_source_disabled(current_source_id: str, disabled: bool) -> None:
            if current_source_id == base_source_id and disabled is True:
                raise OSError("base disable down")
            await original_set_source_disabled(current_source_id, disabled)

        monkeypatch.setattr(runtime.store, "set_source_disabled", _flaky_set_source_disabled)

        with pytest.raises(OSError, match="base disable down"):
            await runner.clear_sources("ai_jobs")

        listed = await runner.list_sources("ai_jobs")
        assert any(item["source_id"] == source_id and item["enabled"] is True for item in listed)
        assert await runtime.store.get_runtime_source(source_id) is not None
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_ignores_post_run_housekeeping_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [
                {
                    "type": "local_fixture",
                    "path": fixture_path.as_posix(),
                    "source_name": "src-1",
                }
            ],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())

    class _FakeAssessmentService:
        async def assess(self, spec, *, store=None, auth_provider=None):
            del spec, store, auth_provider
            return None

        async def assess_and_store(self, spec, store, *, ttl_days=None):
            del spec, store, ttl_days
            return None

    async def _fake_build_runtime_catalog(self, runtime, *, user_id=None):
        del self, runtime, user_id
        return SimpleNamespace(catalog_name="default"), {}

    async def _fake_build_runtime_builder(
        self,
        runtime,
        *,
        effective_sources,
        catalog,
        run_id,
        user_id=None,
        relevance_prompts=None,
    ):
        del self, runtime, effective_sources, catalog, run_id, user_id, relevance_prompts

        class _FakeBuilder:
            async def run_async(self, *, max_items=None):
                del max_items
                summary = RunSummary()
                summary.source_run_id = "run-1"
                summary.finished_at = datetime.now(UTC)
                summary.by_source_id["local_fixture:src-1"] = SourceRunStats(fetched=1, emitted=1)
                return summary

        class _FakeSnapshot:
            def set_run_id(self, run_id):
                del run_id

            async def save_and_purge(self):
                return None

        return _FakeBuilder(), _FakeSnapshot()

    async def _failing_update_source_health(self, runtime, summary):
        del self, runtime, summary
        raise OSError("health down")

    async def _failing_mark_source_bootstrap_completed(self, runtime, source_id, completed_at):
        del self, runtime, source_id, completed_at
        raise OSError("bootstrap down")

    monkeypatch.setattr(
        "job_ftch.application.tenant_runner.create_source_assessment_service",
        lambda: _FakeAssessmentService(),
    )
    monkeypatch.setattr(TenantRunner, "_build_runtime_catalog", _fake_build_runtime_catalog)
    monkeypatch.setattr(TenantRunner, "_build_runtime_builder", _fake_build_runtime_builder)
    monkeypatch.setattr(TenantRunner, "_update_source_health", _failing_update_source_health)
    monkeypatch.setattr(
        TenantRunner,
        "_mark_source_bootstrap_completed",
        _failing_mark_source_bootstrap_completed,
    )

    runtime = runner.get_runtime("ai_jobs")
    original_set_run_state = runtime.store.set_run_state

    async def _flaky_set_run_state(key: str, value: str, **kwargs: object):
        if key == "pipeline.run_summary":
            raise OSError("state down")
        return await original_set_run_state(key, value, **kwargs)

    async def _flaky_save_run_summary(summary: RunSummary):
        del summary
        raise OSError("history down")

    runtime.store.set_run_state = _flaky_set_run_state  # type: ignore[method-assign]
    runtime.store.save_run_summary = _flaky_save_run_summary  # type: ignore[method-assign]

    try:
        summary = await runner.run_tenant("ai_jobs")
        assert summary.tenant_id == "ai_jobs"
        assert summary.source_run_id == "run-1"
        assert summary.by_source_id["local_fixture:src-1"].emitted == 1
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_clears_stale_status_when_final_summary_persist_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [
                {
                    "type": "local_fixture",
                    "path": fixture_path.as_posix(),
                    "source_name": "src-1",
                }
            ],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())

    class _FakeAssessmentService:
        async def assess_and_store(self, spec, store, *, ttl_days=None):
            del spec, store, ttl_days
            return None

    async def _fake_build_runtime_catalog(self, runtime, *, user_id=None):
        del self, runtime, user_id
        return SimpleNamespace(catalog_name="default"), {}

    async def _fake_build_runtime_builder(
        self,
        runtime,
        *,
        effective_sources,
        catalog,
        run_id,
        user_id=None,
        relevance_prompts=None,
    ):
        del self, runtime, effective_sources, catalog, run_id, user_id, relevance_prompts

        class _FakeBuilder:
            async def run_async(self, *, max_items=None):
                del max_items
                summary = RunSummary()
                summary.source_run_id = "run-new"
                summary.finished_at = datetime.now(UTC)
                summary.by_source_id["local_fixture:src-1"] = SourceRunStats(fetched=1, emitted=1)
                return summary

        class _FakeSnapshot:
            def set_run_id(self, run_id):
                del run_id

            async def save_and_purge(self):
                return None

        return _FakeBuilder(), _FakeSnapshot()

    monkeypatch.setattr(
        "job_ftch.application.tenant_runner.create_source_assessment_service",
        lambda: _FakeAssessmentService(),
    )
    monkeypatch.setattr(TenantRunner, "_build_runtime_catalog", _fake_build_runtime_catalog)
    monkeypatch.setattr(TenantRunner, "_build_runtime_builder", _fake_build_runtime_builder)

    runtime = runner.get_runtime("ai_jobs")
    stale_summary = RunSummary(source_run_id="run-new", tenant_id="ai_jobs")
    stale_summary.finished_at = datetime.now(UTC)
    await runtime.store.set_run_state(
        "pipeline.run_summary",
        json.dumps(stale_summary.as_dict(), default=str, ensure_ascii=False, sort_keys=True),
    )
    original_set_run_state = runtime.store.set_run_state

    async def _flaky_set_run_state(key: str, value: str, **kwargs: object):
        if key == "pipeline.run_summary" and value:
            raise OSError("state down")
        return await original_set_run_state(key, value, **kwargs)

    runtime.store.set_run_state = _flaky_set_run_state  # type: ignore[method-assign]

    try:
        summary = await runner.run_tenant("ai_jobs")
        status = await runner.get_status("ai_jobs")
        assert summary.source_run_id == "run-new"
        assert status is None
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_tenant_runner_persists_candidate_profiles_and_reranks_latest_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )
    runner.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()
    try:
        # The semantic shot scorer only matches against actual example
        # shots, not against ``target_roles`` keywords. The test still
        # seeds the in-memory BGE-M3 store from the same profile it
        # saves so the later semantic stages see the same profile state
        # the bot would produce after the user adds a positive example.
        from job_ftch.infrastructure.relevance import shot_registry
        from job_ftch.infrastructure.relevance.shot_anchor import (
            InMemoryBgeMThreeShotStore,
        )

        class _DeterministicBgeM3:
            """Test double that always matches positives strongly."""

            dim = 16

            def encode(
                self,
                text: str,
                *,
                max_length: int = 512,
                return_sparse: bool = False,
            ) -> dict[str, Any]:
                import numpy as np

                vec = np.zeros(self.dim, dtype=np.float32)
                # Mark which "slot" of the vector each text occupies
                # so the cosine similarity between the fixture text
                # and the positive example is near 1.0.
                if "Senior ML" in text or "machine learning" in text:
                    vec[0] = 1.0
                else:
                    vec[1] = 1.0
                out: dict[str, Any] = {"dense": vec}
                if return_sparse:
                    out["sparse"] = {str(i): 1.0 for i in range(3)}
                return out

        provider = _DeterministicBgeM3()
        store = InMemoryBgeMThreeShotStore(provider=provider)
        shot_registry.reset()
        shot_registry.configure(store=store, provider=provider)
        try:
            profile = build_candidate_profile_from_payload(
                user_id="1",
                profile_id="ml",
                payload={
                    "summary": "machine learning engineer",
                    "target_roles": ["ml engineer", "machine learning engineer"],
                    "relevance_threshold": 0.0,
                },
            )
            # ``build_candidate_profile_from_payload`` ignores example
            # texts (the bot's text-handling path uses
            # ``add_example_to_profile`` instead), so set them
            # directly on the SearchProfile. This matches what the
            # bot would produce after the user runs
            # ``/positive + /done`` once.
            sp_first = profile.search_profiles[0]
            sp_first = sp_first.model_copy(
                update={
                    "positive_example_texts": (
                        "Senior ML Engineer with python and pytorch experience",
                    ),
                }
            )
            profile = profile.model_copy(
                update={"search_profiles": (sp_first,) + profile.search_profiles[1:]}
            )
            mp = ManagedCandidateProfile(user_id="1", profile_id="ml", profile=profile)
            saved = await runner.save_and_activate_candidate_profile("ai_jobs", mp)
            # Populate the in-memory shot store with the positive
            # example the test just saved. The bot does the same
            # via sync_profile_to_shot_store; tests do it directly.
            from job_ftch.application.shot_sync import sync_profile_to_shot_store

            await sync_profile_to_shot_store(
                profile=mp,
                tenant_id="ai_jobs",
                user_id="1",
            )
            await runner.run_tenant("ai_jobs")
            jobs = await runner.latest_jobs("ai_jobs", user_id="1", limit=5)
            profiles = await runner.list_candidate_profiles("ai_jobs", "1")

            assert saved["profile_id"] == "ml"
            assert profiles[0]["active"] is True
            assert jobs[0].best_profile_id == "ml"
            assert jobs[0].best_score is not None
        finally:
            shot_registry.reset()
    finally:
        await runner.close()


def test_update_source_health_payload_marks_drift_and_failure_streak() -> None:
    stats = SourceRunStats(emitted=0, failed=0, fetched=2)
    payload = _update_source_health_payload(
        SourceHealth(
            source_id="career_site:bcc_ml",
            source_kind="career_site",
            source_name="bcc_ml",
            last_run_at="2026-06-12T00:00:00+00:00",
            last_success_at="2026-06-12T00:00:00+00:00",
            failure_streak=0,
            success_count=3,
            last_fetched=10,
            last_emitted=10,
            last_failed=0,
            last_quarantined=0,
            baseline_emitted=10.0,
            drift_ratio=1.0,
            degraded=False,
            status="healthy",
        ),
        source_id="career_site:bcc_ml",
        source_kind="career_site",
        source_name="bcc_ml",
        stats=stats,
        finished_at=datetime(2026, 6, 13, tzinfo=UTC),
    )

    assert payload.degraded is True
    assert payload.status == "degraded"
    assert payload.drift_ratio == 0.0

    failed = SourceRunStats(emitted=0, failed=1)
    failed_payload = _update_source_health_payload(
        payload,
        source_id="career_site:bcc_ml",
        source_kind="career_site",
        source_name="bcc_ml",
        stats=failed,
        finished_at=datetime(2026, 6, 13, 1, tzinfo=UTC),
    )

    assert failed_payload.failure_streak == 1


def _health_from(stats: SourceRunStats, previous: SourceHealth | None = None) -> SourceHealth:
    return _update_source_health_payload(
        previous,
        source_id="career_site:acme",
        source_kind="career_site",
        source_name="acme",
        stats=stats,
        finished_at=datetime(2026, 6, 13, tzinfo=UTC),
    )


def test_majority_item_failure_marks_run_failing() -> None:
    """fetched=10, failed=8 is a majority failure (0.8 > 0.5), not a success."""
    health = _health_from(SourceRunStats(fetched=10, failed=8))

    assert health.failure_streak == 1
    assert health.status == "failing"


def test_minority_item_failure_stays_healthy() -> None:
    """fetched=10, failed=2 is below the majority threshold and remains healthy."""
    health = _health_from(SourceRunStats(fetched=10, failed=2, emitted=8))

    assert health.failure_streak == 0
    assert health.status == "healthy"


def test_source_level_failure_when_nothing_fetched() -> None:
    """fetched=0, failed=1 is a source-level crash, counted as a failure."""
    health = _health_from(SourceRunStats(fetched=0, failed=1))

    assert health.failure_streak == 1
    assert health.status == "failing"


def test_failure_streak_pauses_then_healthy_probe_resets_once() -> None:
    """Three consecutive majority failures pause; one healthy probe fully resets."""
    failing = SourceRunStats(fetched=10, failed=8)
    health = _health_from(failing)
    assert health.failure_streak == 1 and not health.paused

    health = _health_from(failing, previous=health)
    assert health.failure_streak == 2 and not health.paused

    health = _health_from(failing, previous=health)
    assert health.failure_streak == 3 and health.paused and health.status == "paused"

    healthy = _health_from(SourceRunStats(fetched=10, failed=1, emitted=9), previous=health)
    assert healthy.failure_streak == 0
    assert not healthy.paused
    assert healthy.status == "healthy"


def test_inconsistent_counters_do_not_silently_pass() -> None:
    """failed > fetched violates the invariant: it is flagged, and still fails the run."""
    import structlog

    with structlog.testing.capture_logs() as logs:
        health = _health_from(SourceRunStats(fetched=2, failed=5))

    assert health.failure_streak == 1
    assert health.status == "failing"
    assert any(entry["event"] == "source_health_counter_invariant_violated" for entry in logs)


@pytest.mark.asyncio
async def test_tenants_cli_lineage_outputs_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lineage = JobLineage.model_validate(
        {
            "tenant_id": "ai_jobs",
            "job_id": "job-1",
            "group_id": "group-1",
            "raw_item_id": "raw-1",
            "source_record_id": "1",
            "source_kind": "debug",
            "source_name": "fixture",
            "source_url": "https://example.com/jobs/1",
            "canonical_url": "https://example.com/jobs/1",
            "fetched_at": datetime(2026, 6, 12, tzinfo=UTC),
            "pipeline_stages": ("sanitize", "extraction", "aggregation"),
            "source_run_id": "run-123",
        }
    )

    class _RunnerStub:
        def __init__(self) -> None:
            self.closed = False
            self.calls: list[tuple[str, str | None]] = []

        async def get_job_lineage(
            self, job_id: str, *, tenant_id: str | None = None
        ) -> JobLineage | None:
            self.calls.append((job_id, tenant_id))
            return lineage

        async def close(self) -> None:
            self.closed = True

    runner = _RunnerStub()
    monkeypatch.setattr("job_ftch.cli._load_tenant_runner", lambda settings: runner)

    await _handle_tenants(
        Settings(),
        Namespace(tenant_command="lineage", tenant_id="ai_jobs", job_id="job-1"),
    )

    payload = json.loads(capsys.readouterr().out)
    assert runner.calls == [("job-1", "ai_jobs")]
    assert runner.closed is True
    assert payload["tenant_id"] == "ai_jobs"
    assert payload["job_id"] == "job-1"
    assert payload["source_run_id"] == "run-123"
    assert payload["pipeline_stages"] == ["sanitize", "extraction", "aggregation"]


@pytest.mark.asyncio
async def test_runs_cli_list_outputs_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = RunSummary(
        tenant_id="ai_jobs",
        fetched=1,
        emitted=1,
        source_run_id="run-123",
        started_at=datetime(2026, 6, 12, tzinfo=UTC),
        finished_at=datetime(2026, 6, 12, tzinfo=UTC),
    )

    class _RunnerStub:
        def __init__(self) -> None:
            self.closed = False
            self.calls: list[tuple[str | None, int]] = []

        async def list_runs(
            self, *, tenant_id: str | None = None, limit: int = 20
        ) -> list[RunSummary]:
            self.calls.append((tenant_id, limit))
            return [summary]

        async def close(self) -> None:
            self.closed = True

    runner = _RunnerStub()
    monkeypatch.setattr("job_ftch.cli._load_tenant_runner", lambda settings: runner)

    from job_ftch.cli import _handle_runs

    await _handle_runs(
        Settings(),
        Namespace(runs_command="list", tenant_id="ai_jobs", limit=5),
    )

    payload = json.loads(capsys.readouterr().out)
    assert runner.calls == [("ai_jobs", 5)]
    assert runner.closed is True
    assert payload[0]["tenant_id"] == "ai_jobs"
    assert payload[0]["source_run_id"] == "run-123"


@pytest.mark.asyncio
async def test_two_tenant_runners_do_not_duplicate_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    profile_path = tmp_path / "profiles.yaml"
    profile_path.write_text(
        """catalog_name: concurrency_test
profiles:
  - profile_id: ml
    name: ML Engineer
    target_roles: ["ML Engineer"]
    preferred_skills: ["ML"]
""",
        encoding="utf-8",
    )
    tenant_payload = {
        "tenant_id": "ai_jobs",
        "display_name": "AI Jobs",
        "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
        "store_backend": "sqlite",
        "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
        "job_group_store_backend": "sqlite",
        "job_backend": "sqlite",
        "search_backend": "sqlite",
        "filter_profile_path": str(profile_path),
        "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
    }
    tenant = TenantConfig.model_validate(tenant_payload)
    base_settings = _isolated_base_settings().model_copy(update={"relevance_shot_threshold": -10.0})
    runner_one = TenantRunner.from_tenants([tenant], base_settings=base_settings)
    runner_two = TenantRunner.from_tenants([tenant], base_settings=base_settings)

    monkeypatch.setattr(
        tenant_runner_module,
        "build_llm",
        lambda settings: _AcceptingHeuristicLLMProvider(),
    )
    runner_one.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()
    runner_two.get_runtime("ai_jobs").llm_provider = _AcceptingHeuristicLLMProvider()

    first, second = await asyncio.wait_for(
        asyncio.gather(
            runner_one.run_tenant("ai_jobs"),
            runner_two.run_tenant("ai_jobs"),
        ),
        timeout=60.0,
    )
    output_path = tmp_path / "artifacts" / "ai_jobs.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    # Invariant: exactly one concurrent run performs work; the other returns
    # a no-op summary instead of immediately re-running and clobbering output.
    assert first.new_groups_created + second.new_groups_created == 1
    assert first.merged_into_group + second.merged_into_group == 0
    assert first.emitted + second.emitted == 1
    assert len(payload["items"]) == 1

    await runner_one.close()
    await runner_two.close()


@pytest.mark.asyncio
async def test_run_terminal_log_is_emitted_before_the_telemetry_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """record_run_metrics ends by flushing the OTel providers.

    Writing the run-terminal log after that flush leaves the only record that
    carries the run id queued in the batch processor, so an OpenObserve query by
    run id right after the run returns metrics but no operational log.
    """
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant = TenantConfig.model_validate(
        {
            "tenant_id": "ai_jobs",
            "display_name": "AI Jobs",
            "sources": [{"type": "local_fixture", "path": fixture_path.as_posix()}],
            "store_backend": "sqlite",
            "store_path": str(tmp_path / "{tenant_id}" / "store.db"),
            "job_group_store_backend": "sqlite",
            "job_backend": "sqlite",
            "search_backend": "sqlite",
            "output": {"path": str(tmp_path / "artifacts" / "{tenant_id}.json")},
        }
    )
    calls: list[str] = []

    from job_ftch.infrastructure.observability import openobserve as openobserve_module

    monkeypatch.setattr(
        openobserve_module,
        "record_run_metrics",
        lambda summary: calls.append("flush"),
    )

    real_logger = tenant_runner_module.logger

    class _OrderingLogger:
        def info(self, event: str, **kwargs: Any) -> None:
            if event == "tenant_run_complete":
                calls.append("log")
            real_logger.info(event, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(real_logger, name)

    monkeypatch.setattr(tenant_runner_module, "logger", _OrderingLogger())

    runner = TenantRunner.from_tenants([tenant], base_settings=_isolated_base_settings())
    await runner.run_tenant("ai_jobs")
    await runner.close()

    assert calls == ["log", "flush"]
