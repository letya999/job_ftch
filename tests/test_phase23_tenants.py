from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from job_ftch.application.tenant_loader import load_tenants
from job_ftch.application.tenant_runner import TenantRunner
from job_ftch.domain import RawItem, SourceKind, TenantConfig

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
