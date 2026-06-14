"""Tests for TranslationNode."""

from __future__ import annotations

import pytest

from job_ftch.domain import JobRecord


@pytest.fixture
def make_job_record():
    def _make(**kwargs) -> JobRecord:
        defaults = {
            "raw_item_id": "test-id",
            "source_kind": "debug",
            "source_name": "test-source",
            "title": "Job",
            "company": "Company",
            "description": "Desc",
            "metadata": {},
        }
        defaults.update(kwargs)
        return JobRecord(**defaults)

    return _make


class _MockTranslator:
    def __init__(self, supported: set[tuple[str, str]] | None = None) -> None:
        self._supported = supported or {("en", "ru")}
        self.calls: list[tuple[str, str, str]] = []

    def supports(self, source: str, target: str) -> bool:
        return (source, target) in self._supported

    async def translate(self, text: str, source: str, target: str) -> str:
        self.calls.append((text, source, target))
        return f"[translated:{source}->{target}] {text}"


@pytest.mark.asyncio
async def test_translation_node_translates_en_to_ru(make_job_record):
    from job_ftch.nodes.translation import TranslationNode

    translator = _MockTranslator(supported={("en", "ru")})
    node = TranslationNode(translator, target_language="ru")

    job = make_job_record(title="Python Engineer", description="Remote position available")
    job = job.model_copy(update={"metadata": {**job.metadata, "detected_language": "en"}})

    result = await node.process(job)
    assert "[translated:en->ru]" in (result.title or "")
    assert result.metadata.get("original_title") == "Python Engineer"


@pytest.mark.asyncio
async def test_translation_node_skips_same_language(make_job_record):
    from job_ftch.nodes.translation import TranslationNode

    translator = _MockTranslator()
    node = TranslationNode(translator, target_language="ru")

    job = make_job_record(title="Разработчик", description="Описание")
    job = job.model_copy(update={"metadata": {**job.metadata, "detected_language": "ru"}})

    result = await node.process(job)
    assert result.title == "Разработчик"  # unchanged
    assert len(translator.calls) == 0


@pytest.mark.asyncio
async def test_translation_node_skips_kz(make_job_record):
    """KZ is not supported — TranslationNode must skip silently."""
    from job_ftch.nodes.translation import TranslationNode

    translator = _MockTranslator(supported={("en", "ru")})  # KZ not supported
    node = TranslationNode(translator, target_language="ru")

    job = make_job_record(title="Жұмыс", description="Сипаттамасы")
    job = job.model_copy(update={"metadata": {**job.metadata, "detected_language": "kz"}})

    result = await node.process(job)
    assert result.title == "Жұмыс"  # unchanged
    assert len(translator.calls) == 0


@pytest.mark.asyncio
async def test_translation_node_skips_unknown_language(make_job_record):
    from job_ftch.nodes.translation import TranslationNode

    translator = _MockTranslator()
    node = TranslationNode(translator, target_language="ru")

    job = make_job_record(title="Some text", description="Content")
    job = job.model_copy(update={"metadata": {**job.metadata, "detected_language": "unknown"}})

    result = await node.process(job)
    assert result.title == "Some text"  # unchanged
