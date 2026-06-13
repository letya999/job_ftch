from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from job_ftch.adapters.profile_inputs import build_candidate_profile_from_payload
from job_ftch.application.auth import resolve_auth_provider
from job_ftch.application.pipeline import RunSummary, SourceRunStats
from job_ftch.application.registry import create_auth_provider
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
    SourceKind,
    TenantConfig,
    source_spec_identifier,
)
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.auth.env_auth import EnvAuthProvider

if TYPE_CHECKING:
    from pathlib import Path


def _write_fixture(path: Path) -> None:
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="1",
        text="Senior ML Engineer\nRemote\nCompany: OpenAI\nSalary: USD 100000 - 150000",
        metadata={"company": "OpenAI", "title": "Senior ML Engineer"},
    )
    path.write_text(json.dumps([item.model_dump(mode="json")]), encoding="utf-8")


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
    runner = TenantRunner.from_tenants([tenant])

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
async def test_tenant_runner_builds_job_lineage(tmp_path: Path) -> None:
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
    runner = TenantRunner.from_tenants([tenant])

    await runner.run_tenant("ai_jobs")
    jobs = await runner.latest_jobs("ai_jobs", limit=1)
    lineage = await runner.get_job_lineage(jobs[0].job_id, tenant_id="ai_jobs")

    assert lineage is not None
    assert lineage.tenant_id == "ai_jobs"
    assert lineage.job_id == jobs[0].job_id
    assert lineage.raw_item_id == jobs[0].raw_item_id
    assert lineage.source_record_id == "1"
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
    runner = TenantRunner.from_tenants([tenant])

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
    assert health["status"] == "healthy"
    assert health["success_count"] == 2
    assert health["failure_streak"] == 0

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
    runner = TenantRunner.from_tenants([tenant])

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

    runner = TenantRunner.from_tenants([tenant])
    try:
        added = await runner.add_source_spec("ai_jobs", runtime_spec, added_via="test")
        listed = await runner.list_sources("ai_jobs")
        tenants = await runner.list_tenants()

        assert added["source_id"] == source_id
        assert any(item["source_id"] == source_id and item["origin"] == "runtime" for item in listed)
        assert tenants[0].source_count == 2
    finally:
        await runner.close()

    reloaded = TenantRunner.from_tenants([tenant])
    try:
        listed = await reloaded.list_sources("ai_jobs")
        assert any(item["source_id"] == source_id and item["enabled"] is True for item in listed)

        disabled = await reloaded.disable_source("ai_jobs", source_id)
        listed_after_disable = await reloaded.list_sources("ai_jobs")
        tenants = await reloaded.list_tenants()

        assert disabled["status"] == "disabled"
        assert any(item["source_id"] == source_id and item["status"] == "disabled" for item in listed_after_disable)
        assert tenants[0].source_count == 1
    finally:
        await reloaded.close()


@pytest.mark.asyncio
async def test_tenant_runner_persists_candidate_profiles_and_reranks_latest_jobs(tmp_path: Path) -> None:
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
    runner = TenantRunner.from_tenants([tenant])
    try:
        profile = build_candidate_profile_from_payload(
            user_id="1",
            profile_id="ml",
            payload={"summary": "machine learning engineer"},
        )
        saved = await runner.save_candidate_profile(
            "ai_jobs",
            ManagedCandidateProfile(user_id="1", profile_id="ml", profile=profile),
        )
        activated = await runner.set_active_candidate_profile("ai_jobs", "1", "ml")
        await runner.run_tenant("ai_jobs")
        jobs = await runner.latest_jobs("ai_jobs", user_id="1", limit=5)
        profiles = await runner.list_candidate_profiles("ai_jobs", "1")

        assert saved["profile_id"] == "ml"
        assert activated["active"] is True
        assert profiles[0]["active"] is True
        assert jobs[0].best_profile_id == "ml"
        assert jobs[0].best_score is not None
    finally:
        await runner.close()


def test_update_source_health_payload_marks_drift_and_failure_streak() -> None:
    stats = SourceRunStats(emitted=0, failed=0, fetched=2)
    payload = _update_source_health_payload(
        {
            "baseline_emitted": 10.0,
            "failure_streak": 0,
            "success_count": 3,
            "last_success_at": "2026-06-12T00:00:00+00:00",
        },
        source_id="career_site:bcc_ml",
        source_kind="career_site",
        source_name="bcc_ml",
        stats=stats,
        finished_at=datetime(2026, 6, 13, tzinfo=UTC),
    )

    assert payload["degraded"] is True
    assert payload["status"] == "degraded"
    assert payload["drift_ratio"] == 0.0

    failed = SourceRunStats(emitted=0, failed=1)
    failed_payload = _update_source_health_payload(
        payload,
        source_id="career_site:bcc_ml",
        source_kind="career_site",
        source_name="bcc_ml",
        stats=failed,
        finished_at=datetime(2026, 6, 13, 1, tzinfo=UTC),
    )

    assert failed_payload["failure_streak"] == 1


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
async def test_two_tenant_runners_do_not_duplicate_output(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)
    tenant_payload = {
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
    tenant = TenantConfig.model_validate(tenant_payload)
    runner_one = TenantRunner.from_tenants([tenant])
    runner_two = TenantRunner.from_tenants([tenant])

    first, second = await asyncio.wait_for(
        asyncio.gather(
            runner_one.run_tenant("ai_jobs"),
            runner_two.run_tenant("ai_jobs"),
        ),
        timeout=5.0,
    )
    output_path = tmp_path / "artifacts" / "ai_jobs.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert sorted([first.emitted, second.emitted]) == [0, 1]
    assert len(payload["items"]) == 1

    await runner_one.close()
    await runner_two.close()
