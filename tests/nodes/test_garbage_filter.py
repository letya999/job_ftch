import pytest

from job_ftch.domain import RawItem, SourceKind
from job_ftch.nodes.garbage_filter import GarbageFilterNode


def make_item(text: str) -> RawItem:
    return RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        external_id="1",
        text=text,
    )


def make_career_item(text: str, url: str) -> RawItem:
    return RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="career",
        external_id=url,
        url=url,
        text=text,
    )


@pytest.fixture
def node() -> GarbageFilterNode:
    return GarbageFilterNode()


@pytest.mark.anyio
async def test_garbage_filter_passes_plain_job_posting(node):
    item = make_item("Python Engineer wanted, remote, salary 200k")
    result = await node.process(item)
    assert result is item


@pytest.mark.anyio
async def test_garbage_filter_marks_freelancer_service_ad_as_evidence(node):
    item = make_item("#помогу #разрабатываю создаю: ботов и автоматизацию под ключ")
    result = await node.process(item)
    assert "freelancer" in result.metadata["garbage_evidence"]
    atom = result.metadata["evidence_atoms"][-1]
    assert atom["claim"] == "is_job"
    assert atom["polarity"] == "contradicts"


@pytest.mark.anyio
async def test_garbage_filter_marks_project_spec_as_evidence(node):
    item = make_item("Идея: разработать систему мониторинга...")
    assert "project spec" in (await node.process(item)).metadata["garbage_evidence"]


@pytest.mark.anyio
async def test_garbage_filter_marks_chat_rules_as_evidence(node):
    item = make_item("Правила чата: не спамить, не рекламировать...")
    assert (await node.process(item)).metadata["early_triage_state"] == "uncertain"


@pytest.mark.anyio
async def test_garbage_filter_passes_vacancy_with_job_tag(node):
    item = make_item("#вакансия #помогу Ищем Python разработчика")
    result = await node.process(item)
    assert result is item


@pytest.mark.anyio
async def test_garbage_filter_empty_text_passes(node):
    for empty_text in ("", None):
        item = RawItem.model_construct(
            source_kind=SourceKind.TELEGRAM_CHANNEL,
            source_name="test",
            external_id="1",
            text=empty_text,
        )
        result = await node.process(item)
        assert result is item


@pytest.mark.anyio
async def test_garbage_filter_never_returns_none_for_content_evidence(node):
    item = make_item("Правила чата: не спамить, не рекламировать...")
    assert await node.process(item) is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("text", "url"),
    [
        (
            "Our locations\nWe work together across time zones",
            "https://example.com/careers/locations/",
        ),
        (
            "AI-инженер вакансии напрямую от компаний",
            "https://example.com/ai-engineering-jobs",
        ),
        (
            "Что у нас есть крутого?\nРелевантные предложения для кандидатов",
            "https://example.com/content/forgeeks",
        ),
        (
            "Technology jobs count endpoint",
            "https://api.example.com/vacancies?clusters=true&per_page=0",
        ),
        (
            "At Example, we create technology and list open roles.",
            "https://example.com/companies/example-inc/vacancies/",
        ),
    ],
)
async def test_garbage_filter_marks_career_site_non_job_pages(node, text, url):
    item = make_career_item(text, url)

    assert "career-site" in (await node.process(item)).metadata["garbage_evidence"]


@pytest.mark.anyio
async def test_garbage_filter_keeps_career_site_specific_job(node):
    item = make_career_item(
        "Senior AI Automation Engineer\nResponsibilities: build LLM workflows.",
        "https://example.com/vacancy/senior-ai-automation-engineer-123",
    )

    result = await node.process(item)

    assert result is item
