import pytest

from job_ftch.application.contracts import ClassificationResult
from job_ftch.domain import PostType, SourceKind
from job_ftch.domain.source_identity import (
    AcquisitionTransport,
    ObservationKind,
    SourceFamily,
    SourceIdentity,
)
from job_ftch.nodes.post_type import PostTypeClassificationNode


class MockClassifier:
    def __init__(self, label: str, confidence: float):
        self._label = label
        self._confidence = confidence

    async def classify(self, text: str) -> ClassificationResult:
        return ClassificationResult(self._label, self._confidence, "mock_v1")


@pytest.mark.anyio
async def test_post_type_heuristic_detects_candidate_seeking(make_raw_item):
    from job_ftch.infrastructure.classifiers.keyword_lists import load_candidate_tokens

    node = PostTypeClassificationNode(candidate_tokens=load_candidate_tokens())
    item = make_raw_item(text="Something #резюме something")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.CANDIDATE_SEEKING.value


@pytest.mark.anyio
async def test_post_type_heuristic_detects_announcement(make_raw_item):
    from job_ftch.infrastructure.classifiers.keyword_lists import load_announcement_tokens

    node = PostTypeClassificationNode(announcement_tokens=load_announcement_tokens())
    item = make_raw_item(text="Join our meetup next week")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.ANNOUNCEMENT.value


@pytest.mark.anyio
async def test_post_type_heuristic_detects_spam(make_raw_item):
    from job_ftch.infrastructure.classifiers.keyword_lists import load_spam_tokens

    node = PostTypeClassificationNode(spam_tokens=load_spam_tokens())
    item = make_raw_item(text="Get rich in our casino")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.SPAM.value


@pytest.mark.anyio
async def test_post_type_heuristic_detects_job_posting(make_raw_item):
    from job_ftch.infrastructure.classifiers.keyword_lists import load_job_posting_tokens

    node = PostTypeClassificationNode(job_posting_tokens=load_job_posting_tokens())
    item = make_raw_item(text="Hiring senior ML engineer")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value


@pytest.mark.anyio
async def test_post_type_heuristic_returns_unknown_for_neutral_text(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Just some random words")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.UNKNOWN.value


@pytest.mark.anyio
async def test_post_type_classifier_used_when_confident(make_raw_item):
    classifier = MockClassifier(PostType.SPAM.value, 0.9)
    node = PostTypeClassificationNode(classifier=classifier, confidence_threshold=0.8)
    item = make_raw_item(text="Hiring senior ML engineer")  # normally JOB_POSTING via rules
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.SPAM.value
    assert processed.metadata["preclassified_model"] == "mock_v1"


@pytest.mark.anyio
async def test_post_type_classifier_fallback_when_low_confidence(make_raw_item):
    from job_ftch.infrastructure.classifiers.keyword_lists import load_job_posting_tokens

    classifier = MockClassifier(PostType.SPAM.value, 0.5)
    node = PostTypeClassificationNode(
        classifier=classifier,
        confidence_threshold=0.8,
        job_posting_tokens=load_job_posting_tokens(),
    )
    item = make_raw_item(text="Hiring senior ML engineer")
    processed = await node.process(item)
    assert processed.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert processed.metadata["preclassified_model"] == "rules_v2"


@pytest.mark.anyio
async def test_post_type_sets_metadata_fields(make_raw_item):
    node = PostTypeClassificationNode()
    item = make_raw_item(text="Hiring")
    processed = await node.process(item)
    assert "preclassified_post_type" in processed.metadata
    assert "preclassified_confidence" in processed.metadata
    assert "preclassified_model" in processed.metadata


@pytest.mark.anyio
async def test_post_type_with_explicit_tokens_classifies_correctly(make_raw_item):
    node = PostTypeClassificationNode(
        announcement_tokens=("дайджест",), job_posting_tokens=("вакансия",)
    )
    item_job = make_raw_item(text="Это вакансия разработчика")
    res_job = await node.process(item_job)
    assert res_job.metadata["preclassified_post_type"] == "job_posting"

    item_ann = make_raw_item(text="Еженедельный дайджест новостей")
    res_ann = await node.process(item_ann)
    assert res_ann.metadata["preclassified_post_type"] == "announcement"


@pytest.mark.anyio
async def test_post_type_empty_tokens_gives_unknown(make_raw_item):
    node = PostTypeClassificationNode(
        announcement_tokens=(),
        job_posting_tokens=(),
        candidate_tokens=(),
        spam_tokens=(),
        job_posting_strong_tokens=(),
    )
    item = make_raw_item(text="Ищем разработчика Python вакансия")
    res = await node.process(item)
    assert res.metadata["preclassified_post_type"] == "unknown"


@pytest.mark.anyio
async def test_post_type_default_construction_loads_yaml_tokens(make_raw_item):
    from job_ftch.infrastructure.classifiers.keyword_lists import load_job_posting_tokens

    node = PostTypeClassificationNode(job_posting_tokens=load_job_posting_tokens())
    # "вакансия" is usually in the real yaml list
    item = make_raw_item(text="Открытая вакансия Python Backend")
    res = await node.process(item)
    assert res.metadata["preclassified_post_type"] != "unknown"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text",
    (
        "Senior Product Manager – AI Apps\nCompany: Acme\nRemote\nApply: @recruiter",
        "Миддл – Лид ML-продакт-менеджер\nКоманда AI\nУдаленно\nОтклик @jobs",
        "Senior AI/ML Engineer\nKazakhtelecom JSC\nResponsibilities: build ML systems\nContacts: jobs@example.com",
        "Product Manager в VK — крупнейшая технологическая корпорация. Ищет Виолетта, её пост на LinkedIn.",
        "B&MI Product Management and Strategy\nMastercard is a global technology company.\nJob description. More details in the LinkedIn post.",
        "Миддл – Лид ML-продакт-менеджеры в Т-Банк. Ищет Владимир, его пост на LinkedIn.",
    ),
)
async def test_telegram_role_plus_hiring_context_is_a_job(make_raw_item, text: str) -> None:
    node = PostTypeClassificationNode()
    item = make_raw_item(
        text=text,
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="target_jobs",
    )

    result = await node.process(item)

    assert result.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert result.metadata["preclassified_model"] == "telegram_shape_v1"


@pytest.mark.anyio
async def test_career_role_shape_overrides_incidental_internship_announcement(
    make_raw_item,
) -> None:
    node = PostTypeClassificationNode(announcement_tokens=("стажировка",))
    item = make_raw_item(
        text=(
            "Младший AI/ML-инженер (стажер). Компания М.Видео. "
            "Команда развивает RAG и AI-ассистентов. Требования и обязанности указаны ниже."
        ),
        source_kind=SourceKind.CAREER_SITE,
        source_name="hh_llm",
    )

    result = await node.process(item)

    assert result.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert result.metadata["preclassified_model"] == "career_shape_v1"


@pytest.mark.anyio
async def test_career_role_shape_overrides_confident_announcement_classifier(
    make_raw_item,
) -> None:
    node = PostTypeClassificationNode(classifier=MockClassifier(PostType.ANNOUNCEMENT.value, 0.99))
    item = make_raw_item(
        text="AI/ML-инженер. Компания развивает RAG. Обязанности: создавать AI-ассистентов.",
        source_kind=SourceKind.CAREER_SITE,
        source_name="hh_llm",
    )

    result = await node.process(item)

    assert result.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert result.metadata["preclassified_model"] == "career_shape_v1"


@pytest.mark.anyio
async def test_confirmed_career_detail_is_never_jobness_unknown(make_raw_item) -> None:
    node = PostTypeClassificationNode(classifier=MockClassifier(PostType.ANNOUNCEMENT.value, 0.99))
    item = make_raw_item(
        text="Backend-разработчик\nОписание позиции и форма отклика.",
        source_kind=SourceKind.CAREER_SITE,
        source_name="tbank_jobs",
        metadata={"detail_vacancy_confirmed": True},
        source_identity=SourceIdentity(
            family=SourceFamily.CAREER_WEB,
            observation_kind=ObservationKind.VACANCY_DETAIL,
            transport=AcquisitionTransport.BROWSER,
            adapter="tbank",
            parser_version="test",
        ),
    )

    result = await node.process(item)

    assert result.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert result.metadata["preclassified_confidence"] == "0.99"
    assert result.metadata["preclassified_model"] == "source_contract_v1"


@pytest.mark.anyio
async def test_confirmed_detail_overrides_incidental_spam_token(make_raw_item) -> None:
    node = PostTypeClassificationNode(spam_tokens=("spam-footer",))
    item = make_raw_item(
        text="Senior ML Engineer\nBuild recommendations\nspam-footer",
        source_kind=SourceKind.CAREER_SITE,
        source_name="geekjob",
        metadata={"detail_vacancy_confirmed": True},
        source_identity=SourceIdentity(
            family=SourceFamily.CAREER_WEB,
            observation_kind=ObservationKind.VACANCY_DETAIL,
            transport=AcquisitionTransport.HTTP,
            adapter="geekjob",
            parser_version="test",
        ),
    )

    result = await node.process(item)

    assert result.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert result.metadata["preclassified_model"] == "source_contract_v1"


@pytest.mark.anyio
async def test_telegram_vacancy_shape_overrides_spam_domain_token(make_raw_item) -> None:
    node = PostTypeClassificationNode(spam_tokens=("betting", "gambling"))
    item = make_raw_item(
        text=(
            "Head of delivery\n"
            "в Betting Software — компания по разработке приложений для betting и gambling.\n"
            "$5K — $6.5K. Удалённая работа.\n"
            "Описание вакансии на GeekJob.ru."
        ),
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="forproducts",
    )

    result = await node.process(item)

    assert result.metadata["preclassified_post_type"] == PostType.JOB_POSTING.value
    assert result.metadata["preclassified_model"] == "telegram_shape_v1"


@pytest.mark.anyio
async def test_spam_domain_ad_without_vacancy_shape_remains_spam(make_raw_item) -> None:
    node = PostTypeClassificationNode(spam_tokens=("casino",))
    item = make_raw_item(
        text="Get rich in our casino today! Guaranteed winnings.",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="random_feed",
    )

    result = await node.process(item)

    assert result.metadata["preclassified_post_type"] == PostType.SPAM.value
