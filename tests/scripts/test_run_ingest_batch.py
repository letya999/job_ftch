from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.composite import SourceFetchResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _load_script_module():
    script_path = Path(__file__).parents[2] / "scripts" / "run_ingest_batch.py"
    spec = importlib.util.spec_from_file_location("run_ingest_batch", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DomainNamedParserSource:
    def __init__(self) -> None:
        self.spec = CareerSiteSpec(
            url="https://remoteok.com/",
            source_name="ingest_probe_0",
            limit=2,
            detail_limit=2,
        )

    async def fetch(self) -> AsyncIterator[RawItem]:
        yield RawItem(
            source_kind=SourceKind.CAREER_SITE,
            source_name="remoteok.com",
            external_id="job-1",
            url="https://remoteok.com/remote-jobs/1",
            text="<h1>Engineer</h1>",
        )


@pytest.mark.asyncio
async def test_probe_wrapper_preserves_input_identity_for_domain_named_parser_output() -> None:
    module = _load_script_module()
    wrapped = module._TimedProbeSource(_DomainNamedParserSource())

    items = [item async for item in wrapped.fetch()]

    assert [item.source_name for item in items] == ["ingest_probe_0"]


def test_probe_classifies_exhausted_monitors_by_their_observed_stage() -> None:
    module = _load_script_module()
    result = SourceFetchResult(
        source_id="career_site:probe",
        source_kind="career_site",
        source_name="probe",
        failed=True,
        error="source_zero_yield:all_monitors_exhausted",
    )

    assert (
        module._failure_bucket(
            item_count=0,
            parser_name=None,
            stats={"zero_reason": "all_monitors_exhausted", "monitored": 0},
            source_result=result,
        )
        == "listing_discovery_failed"
    )
    assert (
        module._failure_bucket(
            item_count=0,
            parser_name=None,
            stats={
                "zero_reason": "all_monitors_exhausted",
                "monitored": 3,
                "detail_attempted": 1,
            },
            source_result=result,
        )
        == "detail_extraction_failed"
    )


def test_probe_classifies_rejected_non_detail_links_as_discovery_failure() -> None:
    module = _load_script_module()
    result = SourceFetchResult(
        source_id="career_site:probe",
        source_kind="career_site",
        source_name="probe",
        failed=True,
        error="source_zero_yield:all_scrapers_failed",
    )

    assert (
        module._failure_bucket(
            item_count=0,
            parser_name=None,
            stats={"zero_reason": "all_scrapers_failed", "monitored": 20, "detail_attempted": 0},
            source_result=result,
        )
        == "listing_discovery_failed"
    )


def test_probe_classifies_scraped_but_unemitted_detail_pages_as_extraction_failure() -> None:
    module = _load_script_module()
    result = SourceFetchResult(
        source_id="career_site:probe",
        source_kind="career_site",
        source_name="probe",
    )

    assert (
        module._failure_bucket(
            item_count=0,
            parser_name=None,
            stats={"monitored": 20, "scraped": 4},
            source_result=result,
        )
        == "detail_extraction_failed"
    )


def test_probe_preserves_protected_primary_outcome_after_deadline() -> None:
    module = _load_script_module()
    result = SourceFetchResult(
        source_id="career_site:probe",
        source_kind="career_site",
        source_name="probe",
        terminal_outcome="protected",
        deadline_exceeded=True,
    )

    assert (
        module._failure_bucket(
            item_count=0,
            parser_name=None,
            stats={},
            source_result=result,
        )
        == "protected"
    )


def test_probe_routes_zero_item_partial_deadline_to_slow_retry() -> None:
    module = _load_script_module()
    result = SourceFetchResult(
        source_id="career_site:probe",
        source_kind="career_site",
        source_name="probe",
        partial=True,
        deadline_exceeded=True,
        terminal_outcome="unconfirmed_empty",
        completion_state="partial",
    )

    assert (
        module._failure_bucket(
            item_count=0,
            parser_name=None,
            stats={"zero_reason": "monitor_empty"},
            source_result=result,
        )
        == "deadline_exceeded"
    )


def test_timeout_result_is_a_watchdog_deadline_failure() -> None:
    module = _load_script_module()

    result = module._timeout_result("https://slow.example.test/jobs", elapsed_seconds=12.345)

    assert result["parse_status"] == "parsed_failed"
    assert result["failure_bucket"] == "timeout_global"
    assert result["eviction_kind"] == "task_watchdog"
    assert result["deadline_exceeded"] is True
    assert result["elapsed_seconds"] == 12.35


def test_probe_marks_items_from_partial_source_as_not_completed() -> None:
    module = _load_script_module()
    result = SourceFetchResult(
        source_id="career_site:probe",
        source_kind="career_site",
        source_name="probe",
        yielded=1,
        partial=True,
        terminal_outcome="partial_with_items",
        completion_state="partial",
    )

    assert (
        module._failure_bucket(
            item_count=1,
            parser_name=None,
            stats={},
            source_result=result,
        )
        == "partial_with_items"
    )


def test_load_resume_results_indexes_valid_url_records(tmp_path: Path) -> None:
    module = _load_script_module()
    output = tmp_path / "results.json"
    output.write_text(
        json.dumps([{"url": "https://example.test/jobs", "parse_status": "parsed_ok"}, None]),
        encoding="utf-8",
    )

    assert module._load_resume_results(output) == {
        "https://example.test/jobs": {
            "url": "https://example.test/jobs",
            "parse_status": "parsed_ok",
        }
    }


def test_ingest_coverage_gate_fails_below_threshold(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()

    exit_code = module._gate_exit_code(
        [
            {"url": "https://ok.example.test/jobs", "parse_status": "parsed_ok"},
            {"url": "https://failed.example.test/jobs", "parse_status": "parsed_failed"},
        ],
        min_success_rate=0.65,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "GATE FAILED" in captured.err
    assert "1/2 parsed_ok" in captured.err


def test_ingest_coverage_gate_passes_at_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_script_module()

    exit_code = module._gate_exit_code(
        [
            {"url": "https://one.example.test/jobs", "parse_status": "parsed_ok"},
            {"url": "https://two.example.test/jobs", "parse_status": "parsed_ok"},
            {"url": "https://three.example.test/jobs", "parse_status": "parsed_failed"},
        ],
        min_success_rate=0.65,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "GATE PASSED" in captured.out
    assert "2/3 parsed_ok" in captured.out


def test_slow_retry_queue_contains_only_deadline_limited_urls(tmp_path: Path) -> None:
    module = _load_script_module()
    queue_path = tmp_path / "slow.yaml"

    module._write_slow_retry_queue(
        queue_path,
        [
            {
                "url": "https://slow.test/jobs",
                "deadline_exceeded": True,
                "terminal_outcome": "deadline_exceeded",
            },
            {
                "url": "https://protected.test/jobs",
                "deadline_exceeded": True,
                "terminal_outcome": "protected",
            },
            {
                "url": "https://extraction.test/jobs",
                "deadline_exceeded": True,
                "terminal_outcome": "detail_extraction_failed",
            },
            {
                "url": "https://partial.test/jobs",
                "deadline_exceeded": True,
                "terminal_outcome": "partial_with_items",
            },
            {
                "url": "https://zero-item-deadline.test/jobs",
                "deadline_exceeded": True,
                "terminal_outcome": "unconfirmed_empty",
                "failure_bucket": "deadline_exceeded",
            },
            {"url": "https://limited.test/jobs", "limited": True},
        ],
    )

    assert module.yaml.safe_load(queue_path.read_text(encoding="utf-8")) == {
        "urls": [
            "https://slow.test/jobs",
            "https://partial.test/jobs",
            "https://zero-item-deadline.test/jobs",
        ]
    }


@pytest.mark.asyncio
async def test_resume_returns_cleanly_when_every_selected_url_is_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script_module()
    input_path = tmp_path / "sources.yaml"
    output_path = tmp_path / "results.json"
    url = "https://example.test/jobs"
    input_path.write_text(f"urls:\n  - {url}\n", encoding="utf-8")
    output_path.write_text(
        json.dumps([{"url": url, "parse_status": "parsed_ok"}]), encoding="utf-8"
    )
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ingest_batch.py",
            "--input",
            str(input_path),
            "--out-json",
            str(output_path),
            "--resume",
        ],
    )

    assert await module.main() == 0

    assert "All 1 selected URLs are already saved" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_resume_preserves_results_outside_selected_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script_module()
    input_path = tmp_path / "sources.yaml"
    output_path = tmp_path / "results.json"
    first_url = "https://first.example.test/jobs"
    second_url = "https://second.example.test/jobs"
    input_path.write_text(
        f"urls:\n  - {first_url}\n  - {second_url}\n",
        encoding="utf-8",
    )
    output_path.write_text(
        json.dumps([{"url": first_url, "parse_status": "parsed_ok"}]),
        encoding="utf-8",
    )

    class _SecondSource:
        def __init__(self, spec: CareerSiteSpec) -> None:
            self.spec = spec

        async def fetch(self) -> AsyncIterator[RawItem]:
            yield RawItem(
                source_kind=SourceKind.CAREER_SITE,
                source_name=self.spec.source_name,
                external_id="job-2",
                url=f"{second_url}/2",
                text="<h1>Engineer</h1>",
            )

    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module, "create_source_from_spec", lambda spec: _SecondSource(spec))
    monkeypatch.setattr(module, "resolve_site_parser", lambda _url: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ingest_batch.py",
            "--input",
            str(input_path),
            "--start",
            "1",
            "--end",
            "2",
            "--out-json",
            str(output_path),
            "--resume",
            "--timeout",
            "2",
            "--soft-timeout",
            "1",
        ],
    )

    assert await module.main() == 0

    rows = json.loads(output_path.read_text(encoding="utf-8"))
    assert [row["url"] for row in rows] == [first_url, second_url]
    assert rows[0]["parse_status"] == "parsed_ok"
    assert rows[1]["parse_status"] == "parsed_ok"


@pytest.mark.asyncio
async def test_batch_eval_uses_isolated_per_source_deadlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script_module()
    input_path = tmp_path / "sources.yaml"
    output_path = tmp_path / "results.json"
    url = "https://example.test/jobs"
    input_path.write_text(f"urls:\n  - {url}\n", encoding="utf-8")

    class _Source:
        def __init__(self, spec: CareerSiteSpec) -> None:
            self.spec = spec

        async def fetch(self) -> AsyncIterator[RawItem]:
            yield RawItem(
                source_kind=SourceKind.CAREER_SITE,
                source_name=self.spec.source_name,
                external_id="job-1",
                url=f"{url}/1",
                text="<h1>Engineer</h1>",
            )

    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module, "create_source_from_spec", lambda spec: _Source(spec))
    monkeypatch.setattr(module, "resolve_site_parser", lambda _url: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ingest_batch.py",
            "--input",
            str(input_path),
            "--out-json",
            str(output_path),
            "--timeout",
            "2",
            "--soft-timeout",
            "1",
        ],
    )

    assert await module.main() == 0

    row = json.loads(output_path.read_text(encoding="utf-8"))[0]
    assert row["parse_status"] == "parsed_ok"
    assert row["deadline_exceeded"] is False
    assert row["overflow_workers_started"] == 0
