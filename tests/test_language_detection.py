"""Tests for language detection node and adapter."""

from __future__ import annotations

import pytest

from job_ftch.domain.models import JobRecord, SourceKind
from job_ftch.nodes.language_detection import LanguageDetectionNode


def make_job_record(**kwargs) -> JobRecord:
    """Minimal JobRecord fixture."""
    defaults = dict(
        raw_item_id="raw-1",
        source_kind=SourceKind.DEBUG,
        source_name="TestSource",
        title="Python Developer",
        company="Acme",
        description="We are hiring a Python developer.",
    )
    defaults.update(kwargs)
    return JobRecord(**defaults)


class _MockDetector:
    def __init__(self, lang: str) -> None:
        self._lang = lang

    def detect(self, text: str) -> str:
        return self._lang


@pytest.mark.asyncio
async def test_language_detection_node_stores_in_metadata():
    """LanguageDetectionNode stores detected_language in job metadata."""
    job = make_job_record(title="Python developer", description="We need an engineer")
    node = LanguageDetectionNode(_MockDetector("en"))
    result = await node.process(job)
    assert result.metadata.get("detected_language") == "en"


@pytest.mark.asyncio
async def test_language_detection_node_russian():
    job = make_job_record(title="Разработчик Python", description="Нужен инженер")
    node = LanguageDetectionNode(_MockDetector("ru"))
    result = await node.process(job)
    assert result.metadata.get("detected_language") == "ru"


@pytest.mark.asyncio
async def test_language_detection_node_empty_job():
    """Node handles minimal title and description gracefully."""
    job = make_job_record(title="x", description="y")
    node = LanguageDetectionNode(_MockDetector("unknown"))
    result = await node.process(job)
    # Empty text — node returns item unchanged (no crash)
    assert result is not None


@pytest.mark.asyncio
async def test_language_detection_preserves_existing_metadata():
    """LanguageDetectionNode does not remove existing metadata keys."""
    job = make_job_record(title="Test", description="content")
    # Manually set some existing metadata
    job = job.model_copy(update={"metadata": {"existing_key": "existing_value"}})
    node = LanguageDetectionNode(_MockDetector("en"))
    result = await node.process(job)
    assert result.metadata.get("existing_key") == "existing_value"
    assert result.metadata.get("detected_language") == "en"
