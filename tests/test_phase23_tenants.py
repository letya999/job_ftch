from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from job_ftch.application.auth import resolve_auth_provider
from job_ftch.application.registry import create_auth_provider
from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.pipeline import RunSummary
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.cli import _handle_tenants
from job_ftch.config import Settings
from job_ftch.domain import JobLineage, RawItem, SourceKind, TenantConfig
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

    await runner.close()


@pytest.mark.asyncio
async def test_tenants_cli_lineage_outputs_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lineage = JobLineage(
        tenant_id="ai_jobs",
        job_id="job-1",
        group_id="group-1",
        raw_item_id="raw-1",
        source_record_id="1",
        source_kind="debug",
        source_name="fixture",
        source_url="https://example.com/jobs/1",
        canonical_url="https://example.com/jobs/1",
        fetched_at=datetime(2026, 6, 12, tzinfo=UTC),
        pipeline_stages=("sanitize", "extraction", "aggregation"),
        source_run_id="run-123",
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

    first, second = await asyncio.gather(
        runner_one.run_tenant("ai_jobs"),
        runner_two.run_tenant("ai_jobs"),
    )
    output_path = tmp_path / "artifacts" / "ai_jobs.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert sorted([first.emitted, second.emitted]) == [0, 1]
    assert len(payload["items"]) == 1

    await runner_one.close()
    await runner_two.close()
