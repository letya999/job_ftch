from typing import Any

import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.run_budget import AsyncCallBudget
from job_ftch.domain import PostType, RawItem, SourceKind
from job_ftch.nodes.extraction import ExtractedJobFields, ExtractionNode


class FakeLLM:
    def __init__(self, response: ExtractedJobFields):
        self.response = response
        self.last_text = ""

    async def extract(self, text: str, schema: type[Any]) -> Any:
        self.last_text = text
        return self.response


@pytest.mark.anyio
async def test_extraction_prepends_target_roles():
    response = ExtractedJobFields(title="ML Engineer", search_relevance=1.0)
    llm = FakeLLM(response)
    node = ExtractionNode(llm, target_roles=("ML Engineer", "Data Scientist"))

    item = RawItem(
        text="ищем спеца",
        source_name="tg",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="123",
    )
    await node.process(item)

    # Target roles are fenced in a labelled section (relevance-scoring only) so the
    # LLM cannot mistake them for the job's own fields.
    assert "CANDIDATE_TARGET_ROLES" in llm.last_text
    assert "ML Engineer, Data Scientist" in llm.last_text
    assert "JOB_POSTING" in llm.last_text


@pytest.mark.anyio
async def test_extraction_drops_candidate_seeking_post():
    # Fast pre-classifier let a resume through, but the LLM correctly labels it.
    response = ExtractedJobFields(
        title="Open to work", post_type=PostType.CANDIDATE_SEEKING, search_relevance=1.0
    )
    llm = FakeLLM(response)
    node = ExtractionNode(llm)
    item = RawItem(
        text="#resume ищу работу ml",
        source_name="tg",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert exc.value.reason.value == "irrelevant_content"
    assert "candidate_seeking" in exc.value.details


@pytest.mark.anyio
async def test_extraction_drops_announcement_post():
    response = ExtractedJobFields(
        title="ML meetup", post_type=PostType.ANNOUNCEMENT, search_relevance=1.0
    )
    node = ExtractionNode(FakeLLM(response))
    item = RawItem(
        text="webinar about llms",
        source_name="tg",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="1",
    )
    with pytest.raises(RawItemDropped):
        await node.process(item)


@pytest.mark.anyio
async def test_extraction_keeps_career_site_detail_when_llm_mislabels_announcement():
    response = ExtractedJobFields(
        title="Backend Python Developer",
        canonical_url="https://hh.ru/vacancy/134273174",
        post_type=PostType.ANNOUNCEMENT,
        search_relevance=0.9,
        hiring_intent=0.1,
    )
    node = ExtractionNode(FakeLLM(response))
    item = RawItem(
        text="Backend Python-разработчик в команду AI-агентов",
        source_name="hh_llm",
        source_kind=SourceKind.CAREER_SITE,
        external_id="hh-1",
        url="https://hh.ru/vacancy/134273174",
    )

    draft = await node.process(item)

    assert draft is not None
    assert draft.post_type is PostType.JOB_POSTING
    assert draft.metadata["llm_post_type_raw"] == PostType.ANNOUNCEMENT.value
    assert draft.metadata["llm_post_type_override"] == PostType.JOB_POSTING.value


@pytest.mark.anyio
async def test_extraction_keeps_structured_vacancy_when_llm_mislabels_announcement():
    response = ExtractedJobFields(
        title="Python Developer (AI-агенты и LegalTech)",
        post_type=PostType.ANNOUNCEMENT,
        search_relevance=0.9,
        hiring_intent=0.1,
    )
    node = ExtractionNode(FakeLLM(response))
    item = RawItem(
        text=(
            "Python Developer (AI-агенты и LegalTech)\n\n"
            "Обязанности:\n- строить LLM пайплайны\n"
            "Требования:\n- Python, RAG, LangChain\n"
            "Мы предлагаем:\n- удаленка\n"
            "Контакты:\nhr@example.com"
        ),
        source_name="Vakansii_rabotasd",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="tg-1",
        url="https://t.me/Vakansii_rabotasd/2821",
    )

    draft = await node.process(item)

    assert draft is not None
    assert draft.post_type is PostType.JOB_POSTING
    assert draft.metadata["llm_post_type_raw"] == PostType.ANNOUNCEMENT.value
    assert draft.metadata["llm_post_type_override"] == PostType.JOB_POSTING.value


@pytest.mark.anyio
async def test_extraction_strips_prompt_scaffnewing_from_description():
    response = ExtractedJobFields(
        title="ML Engineer",
        description=(
            "### CANDIDATE_TARGET_ROLES (relevance scoring only — DO NOT extract as job fields):\n"
            "ML Engineer\n\n"
            "### JOB_POSTING (extract every field from THIS section only):\n"
            "Actual vacancy body"
        ),
        post_type=PostType.JOB_POSTING,
    )
    node = ExtractionNode(FakeLLM(response), target_roles=("ML Engineer",))
    item = RawItem(
        text="Original posting text",
        source_name="hh_llm",
        source_kind=SourceKind.CAREER_SITE,
        external_id="hh-2",
        url="https://hh.ru/vacancy/2",
    )

    draft = await node.process(item)

    assert draft is not None
    assert draft.description_raw == "Actual vacancy body"


@pytest.mark.anyio
async def test_extraction_title_not_polluted_by_target_roles():
    # LLM echoes the candidate's target roles into the title; guard must reject it
    # and fall back to the posting's first line.
    roles = ("ai engineer", "ml engineer", "llm systems engineer", "ai product manager")
    polluted = "ai engineer, ml engineer, llm systems engineer, ai product manager"
    response = ExtractedJobFields(
        title=polluted,
        company="Delivery Hero",
        post_type=PostType.JOB_POSTING,
        search_relevance=1.0,
    )
    node = ExtractionNode(FakeLLM(response), target_roles=roles)
    item = RawItem(
        text="Group Product Manager, DMarts\nDelivery Hero\nBerlin",
        source_name="forproducts",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="9990",
    )
    draft = await node.process(item)
    assert draft is not None
    assert draft.title_raw != polluted
    assert draft.title_raw == "Group Product Manager, DMarts"


@pytest.mark.anyio
async def test_fallback_company_no_longer_uses_source_name():
    # Career-site item with no company metadata must NOT get source_name as company.
    response = ExtractedJobFields(title="Senior AI Engineer", post_type=PostType.JOB_POSTING)
    node = ExtractionNode(FakeLLM(response))
    item = RawItem(
        text="We are hiring a Senior AI Engineer",
        source_name="ru_hirify",
        source_kind=SourceKind.CAREER_SITE,
        external_id="1",
        url="https://hirify.me/jobs/1",
    )
    draft = await node.process(item)
    assert draft is not None
    assert draft.company_name_raw is None


@pytest.mark.anyio
async def test_extraction_drops_low_relevance():
    # LLM says it's a job but not for the candidate
    response = ExtractedJobFields(
        title="QA Engineer", post_type=PostType.JOB_POSTING, search_relevance=0.1
    )
    llm = FakeLLM(response)
    node = ExtractionNode(llm, min_search_relevance=0.3)

    item = RawItem(
        text="ищем QA", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="123"
    )

    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert exc.value.reason == "job_out_of_scope"
    assert exc.value.reason.value == "job_out_of_scope"
    assert "LLM search_relevance=0.10 below min=0.30" in exc.value.details


@pytest.mark.anyio
async def test_extraction_defers_on_budget_exhaustion():
    response = ExtractedJobFields(title="ML Engineer")
    llm = FakeLLM(response)
    # Already used the only allowed call
    node = ExtractionNode(llm, max_calls=1)
    item = RawItem(
        text="job 1", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="1"
    )
    await node.process(item)

    item2 = RawItem(
        text="job 2", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="2"
    )
    deferred = await node.process(item2)

    assert deferred.metadata["budget_outcome"] == "deferred"
    assert "budget_deferred" in deferred.review_reasons


@pytest.mark.anyio
async def test_extraction_defers_on_shared_async_budget_exhaustion():
    response = ExtractedJobFields(title="ML Engineer")
    llm = FakeLLM(response)
    node = ExtractionNode(llm, max_calls=10, budget=AsyncCallBudget(1))
    item = RawItem(
        text="job 1", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="1"
    )
    await node.process(item)

    item2 = RawItem(
        text="job 2", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="2"
    )
    deferred = await node.process(item2)

    assert deferred.metadata["budget_outcome"] == "deferred"


@pytest.mark.anyio
async def test_extraction_no_drop_when_threshold_zero():
    response = ExtractedJobFields(
        title="QA Engineer", post_type=PostType.JOB_POSTING, search_relevance=0.1
    )
    llm = FakeLLM(response)
    node = ExtractionNode(llm, min_search_relevance=0.0)  # default

    item = RawItem(
        text="ищем QA", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="123"
    )

    # Should not raise RawItemDropped
    draft = await node.process(item)
    assert draft is not None
    assert draft.metadata["llm_search_relevance"] == 0.1


def test_extracted_job_fields_coerces_none_arrays_to_empty() -> None:
    payload = ExtractedJobFields.model_validate(
        {
            "title": "Technical Writer",
            "requirements_nice": None,
            "skills_explicit": None,
            "skills_inferred": None,
            "tools_stack": None,
            "benefits": None,
            "culture_signals": None,
            "domain_knowledge": None,
            "soft_skills": None,
            "certifications": None,
        }
    )

    assert payload.requirements_nice == ()
    assert payload.skills_explicit == ()
    assert payload.skills_inferred == ()
    assert payload.tools_stack == ()
    assert payload.benefits == ()
    assert payload.culture_signals == ()
    assert payload.domain_knowledge == ()
    assert payload.soft_skills == ()
    assert payload.certifications == ()


def test_extracted_job_fields_coerces_openai_null_scores_and_empty_compensation() -> None:
    payload = ExtractedJobFields.model_validate(
        {
            "title": "Python developer",
            "ai_relevance": None,
            "search_relevance": None,
            "hiring_intent": None,
            "compensation": {
                "currency": "RUB",
                "min_amount": None,
                "max_amount": None,
                "period": "year",
                "gross": None,
            },
        }
    )

    assert payload.ai_relevance == 0.0
    assert payload.search_relevance == 0.5
    assert payload.hiring_intent == 0.5
    assert payload.compensation is None
