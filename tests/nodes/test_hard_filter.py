import pytest

from job_ftch.domain import LanguageCode, PostType
from job_ftch.domain.profile import ProfileCatalog, SearchProfile
from job_ftch.nodes.hard_filter import HardFilterNode


@pytest.fixture
def empty_catalog():
    return ProfileCatalog(profiles=[SearchProfile(profile_id="p1")])


@pytest.mark.anyio
async def test_hard_filter_passes_job_posting(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.JOB_POSTING.value})
    assert await node.process(item) is item


@pytest.mark.anyio
async def test_hard_filter_marks_candidate_seeking(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.CANDIDATE_SEEKING.value})
    result = await node.process(item)
    assert "post_type:candidate_seeking" in result.metadata["hard_filter_evidence"]
    assert result.metadata["evidence_atoms"][-1]["claim"] == "hard_constraint"


@pytest.mark.anyio
async def test_hard_filter_marks_announcement(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.ANNOUNCEMENT.value})
    assert "post_type:announcement" in (await node.process(item)).metadata["hard_filter_evidence"]


@pytest.mark.anyio
async def test_hard_filter_keeps_ai_job_misclassified_as_announcement(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(
        text=(
            "AI архитектор #удаленка #lead\n"
            "Чем предстоит заниматься: вести техническую часть GenAI проектов, "
            "интегрировать LLM, RAG и ИИ-агентов в приложения.\n"
            "Требования: Python, FastAPI, коммерческий опыт с GenAI."
        ),
        metadata={"preclassified_post_type": PostType.ANNOUNCEMENT.value},
    )
    out = await node.process(item)
    assert out.metadata["hard_filter_override"] == "ai_job_signal"


@pytest.mark.anyio
async def test_hard_filter_keeps_hh_style_ai_ml_internship_announcement(
    empty_catalog, make_raw_item
):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(
        text=(
            "Младший AI/ML-инженер (Стажер)\n"
            "Команда занимается генеративным ИИ, LLM и RAG.\n"
            "Стек: Python, PyTorch, Hugging Face, LangChain, LlamaIndex, FastAPI.\n"
            "С нами ты будешь собирать и тестировать пайплайны RAG, "
            "интегрировать open-source LLM и коммерческие API во внутренние сервисы.\n"
            "Для нас ценно понимание Transformer и опыт с LLM."
        ),
        metadata={"preclassified_post_type": PostType.ANNOUNCEMENT.value},
    )
    out = await node.process(item)
    assert out.metadata["hard_filter_override"] == "ai_job_signal"


@pytest.mark.anyio
async def test_hard_filter_marks_ai_announcement_without_job_structure(
    empty_catalog, make_raw_item
):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(
        text="Anthropic выпустили новый blog post про Claude managed agents.",
        metadata={"preclassified_post_type": PostType.ANNOUNCEMENT.value},
    )
    assert "post_type:announcement" in (await node.process(item)).metadata["hard_filter_evidence"]


@pytest.mark.anyio
async def test_hard_filter_marks_unconfirmed_spam(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.SPAM.value})
    assert "post_type:spam" in (await node.process(item)).metadata["hard_filter_evidence"]


@pytest.mark.anyio
async def test_hard_filter_passes_unknown_post_type(empty_catalog, make_raw_item):
    node = HardFilterNode(empty_catalog)
    item = make_raw_item(metadata={"preclassified_post_type": PostType.UNKNOWN.value})
    assert await node.process(item) is item


@pytest.mark.anyio
async def test_hard_filter_language_block_is_policy_evidence(make_raw_item):
    profile = SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,))
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(metadata={"detected_language": "en"})
    assert "language_not_allowed:en" in (await node.process(item)).metadata["hard_filter_evidence"]


@pytest.mark.anyio
async def test_hard_filter_language_allows_unknown(make_raw_item):
    profile = SearchProfile(profile_id="p1", allowed_languages=(LanguageCode.RU,))
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(metadata={"detected_language": "unknown"})
    assert await node.process(item) is item


@pytest.mark.anyio
async def test_hard_filter_blocked_company_in_text_is_policy_evidence(make_raw_item):
    profile = SearchProfile(profile_id="p1", blocked_companies=("EvilCorp",))
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(text="Join EvilCorp today!")
    assert "blocked_company:EvilCorp" in (await node.process(item)).metadata["hard_filter_evidence"]


@pytest.mark.anyio
async def test_hard_filter_no_profiles_allows_all_languages(make_raw_item):
    profile = SearchProfile(profile_id="p1", allowed_languages=())
    catalog = ProfileCatalog(profiles=[profile])
    node = HardFilterNode(catalog)
    item = make_raw_item(metadata={"detected_language": "en"})
    assert await node.process(item) is item
