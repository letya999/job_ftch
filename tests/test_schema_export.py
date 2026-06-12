from __future__ import annotations

import json
from pathlib import Path
from runpy import run_path
from typing import TYPE_CHECKING

from job_ftch.domain import Job, JobDraft, JobRecord, SourceKind
from job_ftch.domain.contracts import job_to_draft, job_to_record

if TYPE_CHECKING:
    import pytest


def test_job_compatibility_conversions() -> None:
    job = Job(
        raw_item_id="raw-1",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ai_jobs",
        title="Senior ML Engineer",
        company="Acme AI",
        company_canonical="acme ai",
        description="Build and ship applied ML systems.",
    )

    draft = job_to_draft(job)
    record = job_to_record(job)

    assert isinstance(draft, JobDraft)
    assert draft.title_raw == "Senior ML Engineer"
    assert draft.company_name_raw == "Acme AI"
    assert isinstance(record, JobRecord)
    assert record.job_id == job.stable_id
    assert record.company_name_normalized == "acme ai"


def test_schema_export_script_writes_canonical_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "export_schema.py"),
        run_name="__main__",
    )

    expected = {
        "config/sources.schema.json",
        "artifacts/schemas/RawItem.schema.json",
        "artifacts/schemas/JobDraft.schema.json",
        "artifacts/schemas/JobRecord.schema.json",
        "artifacts/schemas/JobGroup.schema.json",
    }
    for relative_path in expected:
        path = tmp_path / relative_path
        assert path.exists(), relative_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "$defs" in payload or payload.get("type") == "object"

    raw_schema = json.loads(
        (tmp_path / "artifacts/schemas/RawItem.schema.json").read_text(encoding="utf-8")
    )
    record_schema = json.loads(
        (tmp_path / "artifacts/schemas/JobRecord.schema.json").read_text(encoding="utf-8")
    )
    assert raw_schema["title"] == "RawItem"
    assert record_schema["title"] == "JobRecord"
    assert "job_id" in record_schema["properties"]
    assert "source_record_id" in record_schema["properties"]
    assert "posted_at" in record_schema["properties"]
    assert "status" in record_schema["properties"]
    assert "risk_score" in record_schema["properties"]
    assert "risk_level" in record_schema["properties"]
    assert "provenance" in record_schema["properties"]

    # New Plan B assertions
    assert "$id" in raw_schema
    assert "$id" in record_schema
    assert "leadership_level" in record_schema["properties"]
    assert "aggregate_source_count" in record_schema["properties"]
    assert "years_experience" in record_schema["properties"]
    assert "role_specialization" in record_schema["properties"]
    assert "culture_summary" in record_schema["properties"]
