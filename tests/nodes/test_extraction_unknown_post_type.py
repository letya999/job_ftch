"""UNKNOWN post_type must not slip past both non-job guards.

It is absent from ``_NON_JOB_POST_TYPES``, so it was never dropped, and the
hiring-intent gate only inspected JOB_POSTING, so it was never scored. A chat
message the model could not classify therefore reached delivery as a vacancy
("LLM-инженер, я так понимаю, это не вайбкодер?" was published to the channel).

Real vacancies do get posted conversationally, so an UNKNOWN post the model
reads as actively hiring must still pass.
"""

from typing import Any

import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import PostType, RawItem, SourceKind
from job_ftch.nodes.extraction import ExtractedJobFields, ExtractionNode


class FakeLLM:
    def __init__(self, response: ExtractedJobFields):
        self.response = response

    async def extract(self, text: str, schema: type[Any]) -> Any:
        return self.response


def _item(text: str = "обсуждение в чате") -> RawItem:
    return RawItem(
        text=text,
        source_name="tg",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        external_id="1",
    )


@pytest.mark.anyio
async def test_unknown_with_neutral_hiring_intent_is_dropped():
    """0.5 is what the schema substitutes for a declined answer, not evidence."""
    llm = FakeLLM(
        ExtractedJobFields(
            title="LLM-инженер, я так понимаю, это не вайбкодер, верно?",
            post_type=PostType.UNKNOWN,
            hiring_intent=0.5,
            search_relevance=1.0,
        )
    )
    node = ExtractionNode(llm, min_hiring_intent=0.4)
    with pytest.raises(RawItemDropped) as exc:
        await node.process(_item())
    assert "hiring_intent" in exc.value.details


@pytest.mark.anyio
async def test_unknown_with_low_hiring_intent_is_dropped():
    llm = FakeLLM(
        ExtractedJobFields(
            title="Кто-нибудь пробовал langgraph?",
            post_type=PostType.UNKNOWN,
            hiring_intent=0.1,
            search_relevance=1.0,
        )
    )
    node = ExtractionNode(llm, min_hiring_intent=0.4)
    with pytest.raises(RawItemDropped):
        await node.process(_item())


@pytest.mark.anyio
async def test_unknown_with_positive_hiring_intent_survives():
    """An informal but genuine job post is exactly what must not be lost."""
    llm = FakeLLM(
        ExtractedJobFields(
            title="ML Engineer",
            company="Acme",
            post_type=PostType.UNKNOWN,
            hiring_intent=0.9,
            search_relevance=1.0,
        )
    )
    node = ExtractionNode(llm, min_hiring_intent=0.4)
    result = await node.process(_item("Ищем ML-инженера, пишите в личку"))
    assert result is not None


@pytest.mark.anyio
async def test_job_posting_gate_still_applies():
    llm = FakeLLM(
        ExtractedJobFields(
            title="Дайджест вакансий недели",
            post_type=PostType.JOB_POSTING,
            hiring_intent=0.2,
            search_relevance=1.0,
        )
    )
    node = ExtractionNode(llm, min_hiring_intent=0.4)
    with pytest.raises(RawItemDropped):
        await node.process(_item())


@pytest.mark.anyio
async def test_confident_job_posting_unaffected():
    llm = FakeLLM(
        ExtractedJobFields(
            title="ML Engineer",
            company="Acme",
            post_type=PostType.JOB_POSTING,
            hiring_intent=1.0,
            search_relevance=1.0,
        )
    )
    node = ExtractionNode(llm, min_hiring_intent=0.4)
    assert await node.process(_item("Ищем ML-инженера")) is not None


@pytest.mark.anyio
async def test_gate_disabled_by_default_keeps_unknown():
    """min_hiring_intent=0 means the deployment opted out; do not drop."""
    llm = FakeLLM(
        ExtractedJobFields(
            title="Что-то непонятное",
            post_type=PostType.UNKNOWN,
            hiring_intent=0.5,
            search_relevance=1.0,
        )
    )
    node = ExtractionNode(llm, min_hiring_intent=0.0)
    assert await node.process(_item()) is not None
