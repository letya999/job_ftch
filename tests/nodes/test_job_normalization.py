import pytest

from job_ftch.domain import CompensationPeriod, JobDraft, JobRecord, SourceKind
from job_ftch.nodes.job_normalization import (
    CompensationParsingNode,
    TitleCompanyNormalizationNode,
    _clean_company,
    _clean_title,
    _strip_html,
)


def test_strip_html():
    assert _strip_html("Hello <b>World</b>") == "Hello World"
    assert _strip_html("Andersen </strong>is hiring an<strong>") == "Andersen is hiring an"
    assert _strip_html("Line 1&nbsp;Line 2") == "Line 1 Line 2"
    assert _strip_html("A &lt; B &amp; C &gt; D") == "A < B & C > D"


def test_clean_title():
    assert _clean_title("Hiring: Python Developer") == "Python Developer"
    assert _clean_title("Vacancy - Java <b>Engineer</b>") == "Java Engineer"
    assert _clean_title("Ищем: Data Scientist") == "Data Scientist"


def test_clean_company():
    assert _clean_company("Google") == "Google"
    assert _clean_company(" Andersen </strong>is hiring an<strong>") == "Andersen is hiring an"
    assert (
        _clean_company(
            "This is a very long company name that is actually a sentence that should definitely be rejected"
        )
        is None
    )
    assert _clean_company("Short Name") == "Short Name"
    assert _clean_company("A company with many spaces in it because it is prose and long") is None


@pytest.mark.asyncio
async def test_normalization_node_strips_html_from_description():
    class MockNormalizer:
        def infer_role_family(self, title, language="unknown"):
            return None

        def infer_seniority(self, title):
            return None

        def normalize_skills(self, skills):
            return skills

    node = TitleCompanyNormalizationNode(MockNormalizer())
    draft = JobDraft(
        raw_item_id="raw1",
        description_raw="<p>We are <strong>hiring</strong>!</p>",
        title_raw="<b>Developer</b>",
        company_name_raw="Google",
        source_kind="career_site",
        source_name="test",
        canonical_url="http://example.com",
    )

    record = await node.process(draft)
    assert record.title == "Developer"
    assert record.description == "We are hiring !"
    assert "description:html_stripped" in record.provenance.normalization


@pytest.mark.asyncio
async def test_compensation_parsing_structured_metadata():
    node = CompensationParsingNode()
    record = JobRecord(
        raw_item_id="raw1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        description="no salary here",
        metadata={
            "base_salary": {
                "currency": "USD",
                "min": 130000,
                "max": 170000,
                "period": "year",
                "gross": False,
            }
        },
    )
    processed = await node.process(record)
    assert processed.compensation.gross is False
    assert processed.compensation is not None
    assert processed.compensation.currency == "USD"
    assert processed.compensation.min_amount == 130000
    assert processed.compensation.max_amount == 170000
    assert processed.compensation.period == CompensationPeriod.YEAR


@pytest.mark.asyncio
async def test_compensation_parsing_malformed_metadata():
    node = CompensationParsingNode()
    record = JobRecord(
        raw_item_id="raw1",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        description="no salary here",
        metadata={"base_salary": "this is a string, not a dict"},
    )
    processed = await node.process(record)
    assert processed.compensation is None

    record2 = JobRecord(
        raw_item_id="raw2",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        description="no salary here",
        metadata={
            "base_salary": {
                "currency": "INVALID_TOO_LONG",
                "min": "invalid_int",
            }
        },
    )
    processed2 = await node.process(record2)
    assert processed2.compensation is None


@pytest.mark.asyncio
async def test_compensation_parsing_normalizes_reversed_text_range() -> None:
    node = CompensationParsingNode()
    record = JobRecord(
        raw_item_id="raw-reversed-range",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        description="Compensation RUB 25 000 - 12 500.",
    )

    processed = await node.process(record)

    assert processed.compensation is not None
    assert processed.compensation.min_amount == 12_500
    assert processed.compensation.max_amount == 25_000


@pytest.mark.asyncio
async def test_compensation_parsing_drops_llm_number_without_salary_evidence() -> None:
    node = CompensationParsingNode()
    record = JobRecord(
        raw_item_id="llm-false-salary",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        title="ML Engineer",
        description="Требования: опыт от 3 лет.",
        compensation={"currency": "RUB", "min_amount": 3},
    )

    processed = await node.process(record)

    assert processed.compensation is None


@pytest.mark.asyncio
async def test_compensation_parsing_uses_text_units_over_llm_number() -> None:
    node = CompensationParsingNode()
    record = JobRecord(
        raw_item_id="llm-million-salary",
        source_kind=SourceKind.DEBUG,
        source_name="test",
        description="Зарплата от 3 млн рублей.",
        compensation={"currency": "RUB", "min_amount": 3},
    )

    processed = await node.process(record)

    assert processed.compensation is not None
    assert processed.compensation.min_amount == 3_000_000


@pytest.mark.asyncio
async def test_compensation_parsing_prefers_structured_salary_over_llm_number() -> None:
    node = CompensationParsingNode()
    record = JobRecord(
        raw_item_id="structured-salary-wins",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        description="Backend Engineer role.",
        compensation={"currency": "RUB", "min_amount": 3},
        metadata={"base_salary": {"currency": "RUB", "min": 300000, "max": 450000}},
    )

    processed = await node.process(record)

    assert processed.compensation is not None
    assert processed.compensation.min_amount == 300000
    assert processed.compensation.max_amount == 450000


@pytest.mark.asyncio
async def test_compensation_period_comes_from_the_salary_not_other_numbers() -> None:
    item = JobRecord(
        raw_item_id="salary-period",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        description="One meeting per month. Salary $20-25 per hour. Work 40 hours per week.",
        compensation={"currency": "USD", "min_amount": 20, "max_amount": 25, "period": "month"},
    )

    processed = await CompensationParsingNode().process(item)

    assert processed is not None
    assert processed.compensation is not None
    assert processed.compensation.period == CompensationPeriod.HOUR


@pytest.mark.asyncio
async def test_compensation_does_not_invent_period_from_unrelated_text() -> None:
    item = JobRecord(
        raw_item_id="salary-no-period",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        description="Salary 300000 RUB. One meeting per hour.",
        compensation={"currency": "RUB", "min_amount": 300000, "period": "hour"},
    )

    processed = await CompensationParsingNode().process(item)

    assert processed is not None
    assert processed.compensation is not None
    assert processed.compensation.period == CompensationPeriod.UNKNOWN


@pytest.mark.asyncio
async def test_compensation_parsing_ignores_benefit_deposit() -> None:
    node = CompensationParsingNode()
    item = JobRecord(
        raw_item_id="benefit-deposit",
        source_kind=SourceKind.CAREER_SITE,
        source_name="cian",
        description=(
            "Кафетерий льгот: денежный депозит 25000 рублей на фитнес, обучение, "
            "развлечения и консультации психолога."
        ),
        compensation={"currency": "RUB", "min_amount": 25_000},
    )

    processed = await node.process(item)

    assert processed is not None
    assert processed.compensation is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "metadata", "expected", "reason"),
    [
        ("Бюджет на обучение €1000. Зарплата €8000 в месяц.", {}, "от 8 000 €/мес", None),
        ("Annual salary: $100,000.", {}, "от 100 000 $/год", None),
        (
            "812243 Software Engineer $235,000.00 812243. Salary: Up to $235,000.00 per year.",
            {},
            "до 235 000 $/год",
            None,
        ),
        ("Salary: 4000–8000 USD net per month.", {}, "4 000–8 000 $/мес на руки", None),
        ("Base salary $100,000 per month.", {}, "от 100 000 $/мес", None),
        ("Base salary $100,000.", {}, None, "unverified_high_salary"),
        (
            "Base salary $100,000 per year.",
            {"base_salary": {"currency": "USD", "min": 100000, "unit": "month"}},
            None,
            "conflicting_salary_period",
        ),
    ],
)
async def test_only_evidenced_compensation_reaches_card(
    make_job_record, description, metadata, expected, reason
) -> None:
    from job_ftch.publication.normalize import format_compensation

    job = await CompensationParsingNode().process(
        make_job_record(description=description, metadata=metadata)
    )
    assert job is not None
    assert format_compensation(job) == expected
    assert job.metadata.get("compensation_review_reason") == reason


@pytest.mark.asyncio
async def test_geekjob_scaled_bounds_require_review(make_job_record) -> None:
    from job_ftch.publication.normalize import format_compensation

    job = make_job_record(
        description="Зарплата: от 200 000 до 230 000 ₽ на руки.",
        metadata={
            "base_salary": {"min": 200000000, "max": 230000000, "currency": "RUB", "unit": "month"}
        },
        compensation={
            "currency": "RUB",
            "min_amount": 200000000,
            "max_amount": 230000000,
            "period": "month",
        },
    )
    assert format_compensation(job) is None
    processed = await CompensationParsingNode().process(job)
    assert processed is not None
    assert processed.metadata["compensation_review_reason"] == "conflicting_salary_scale"
    assert format_compensation(processed) is None


@pytest.mark.asyncio
async def test_market_analysis_cannot_supply_employer_compensation(make_job_record) -> None:
    from job_ftch.publication.normalize import format_compensation

    original = (
        "Старший менеджер. Анализ зарплаты Медиана 400 000 ₽ "
        "Рынок 300 000 ₽ – 550 000 ₽ Оценка ИИ Зарплата не указана. "
        "Сопроводительное письмо. Описание вакансии Ищем менеджера. "
        "Похожие вакансии Зарплата 450 000 ₽ в месяц."
    )
    legacy = make_job_record(
        description_raw=original,
        description="Зарплата 300 000–550 000 ₽ в месяц.",
        compensation={
            "currency": "RUB",
            "min_amount": 300000,
            "max_amount": 550000,
            "period": "month",
        },
    )
    assert format_compensation(legacy) is None
    processed = await CompensationParsingNode().process(legacy)
    assert processed is not None and processed.compensation is None
    assert processed.metadata["compensation_review_reason"] == "market_estimate_without_offer"
    with_offer = legacy.model_copy(
        update={
            "description_raw": original.replace(
                "Ищем менеджера.", "Оклад 180 000 ₽ – 225 000 ₽ в месяц."
            )
        }
    )
    processed = await CompensationParsingNode().process(with_offer)
    assert processed is not None
    assert format_compensation(processed) == "180 000–225 000 ₽/мес"
