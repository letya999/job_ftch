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
            }
        },
    )
    processed = await node.process(record)
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
