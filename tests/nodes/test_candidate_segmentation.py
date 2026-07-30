from __future__ import annotations

import pytest

from job_ftch.domain import (
    AcquisitionTransport,
    ObservationKind,
    RawItem,
    SourceFamily,
    SourceIdentity,
    SourceKind,
)
from job_ftch.nodes.candidate_segmentation import CandidateSegmentationNode


def _item(text: str, **metadata: object) -> RawItem:
    return RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="jobs",
        external_id="42",
        text=text,
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_source_declared_segments_materialize_isolated_candidates() -> None:
    item = _item(
        "digest",
        candidate_segments=[
            {"text": "Hiring Backend Engineer at Acme"},
            {"text": "Vacancy: Data Analyst at Beta"},
        ],
    )

    spans = await CandidateSegmentationNode().process(item)

    assert [span.text for span in spans] == [
        "Hiring Backend Engineer at Acme",
        "Vacancy: Data Analyst at Beta",
    ]
    assert len({span.candidate_span_id for span in spans}) == 2
    first, second = (span.materialize_raw_item() for span in spans)
    assert first.text != second.text
    assert first.metadata["parent_observation_id"] == item.stable_id
    assert first.metadata["candidate_span_id"] != second.metadata["candidate_span_id"]


@pytest.mark.asyncio
async def test_digest_splits_only_when_multiple_vacancy_signals_exist() -> None:
    spans = await CandidateSegmentationNode().process(
        _item("1. Hiring Python Engineer at Acme\n2. Vacancy: ML Engineer at Beta")
    )
    description = await CandidateSegmentationNode().process(
        _item("We are hiring.\n- build models\n- write tests")
    )

    assert len(spans) == 2
    assert len(description) == 1


@pytest.mark.asyncio
async def test_context_is_evidence_not_a_replacement_for_comment_text() -> None:
    item = _item(
        "Vacancy: Platform Engineer",
        parent_text="Parent announcement",
        reply_chain_text="Prior reply",
    )
    span = (await CandidateSegmentationNode().process(item))[0]

    assert span.text == item.text
    assert span.context_evidence == (
        "parent_text:Parent announcement",
        "reply_chain_text:Prior reply",
    )


@pytest.mark.asyncio
async def test_confirmed_detail_page_is_never_split_on_bulleted_sections() -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="tbank",
        external_id="vacancy-42",
        text=(
            "ML Product Manager\n"
            "1. Вести roadmap AI-продукта\n"
            "2. Проверять гипотезы\n"
            "3. Работать с ML-командой"
        ),
        source_identity=SourceIdentity(
            family=SourceFamily.CAREER_WEB,
            observation_kind=ObservationKind.VACANCY_DETAIL,
            transport=AcquisitionTransport.HTTP,
            adapter="generic-career-site",
            parser_version="test",
            legacy_kind=SourceKind.CAREER_SITE.value,
        ),
        metadata={"detail_vacancy_confirmed": True},
    )

    spans = await CandidateSegmentationNode().process(item)

    assert len(spans) == 1
    assert spans[0].text == item.text
    assert spans[0].source_evidence == ("confirmed_vacancy_detail",)
