import pytest
from typing import Any
from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import RawItem, SourceKind, PostType
from job_ftch.nodes.extraction import ExtractionNode, ExtractedJobFields


class FakeLLM:
    def __init__(self, response: ExtractedJobFields):
        self.response = response
        self.last_text = ""

    async def extract(self, text: str, schema: type[Any]) -> Any:
        self.last_text = text
        return self.response


@pytest.mark.asyncio
async def test_extraction_prepends_target_roles():
    response = ExtractedJobFields(title="ML Engineer", search_relevance=1.0)
    llm = FakeLLM(response)
    node = ExtractionNode(llm, target_roles=("ML Engineer", "Data Scientist"))
    
    item = RawItem(text="ищем спеца", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="123")
    await node.process(item)
    
    assert "[Candidate target roles: ML Engineer, Data Scientist]" in llm.last_text


@pytest.mark.asyncio
async def test_extraction_drops_low_relevance():
    # LLM says it's a job but not for the candidate
    response = ExtractedJobFields(
        title="QA Engineer", 
        post_type=PostType.JOB_POSTING, 
        search_relevance=0.1
    )
    llm = FakeLLM(response)
    node = ExtractionNode(llm, min_search_relevance=0.3)
    
    item = RawItem(text="ищем QA", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="123")
    
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert exc.value.reason == "job_out_of_scope"
    assert exc.value.reason.value == "job_out_of_scope"
    assert "LLM search_relevance=0.10 below min=0.30" in exc.value.details


@pytest.mark.asyncio
async def test_extraction_drops_on_budget_exhaustion():
    response = ExtractedJobFields(title="ML Engineer")
    llm = FakeLLM(response)
    # Already used the only allowed call
    node = ExtractionNode(llm, max_calls=1)
    item = RawItem(text="job 1", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="1")
    await node.process(item)
    
    item2 = RawItem(text="job 2", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="2")
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item2)
    
    assert exc.value.reason == "llm_budget_exceeded"
    assert exc.value.reason.value == "llm_budget_exceeded"
    assert "LLM call budget of 1 reached" in exc.value.details


@pytest.mark.asyncio
async def test_extraction_no_drop_when_threshold_zero():
    response = ExtractedJobFields(
        title="QA Engineer", 
        post_type=PostType.JOB_POSTING, 
        search_relevance=0.1
    )
    llm = FakeLLM(response)
    node = ExtractionNode(llm, min_search_relevance=0.0) # default
    
    item = RawItem(text="ищем QA", source_name="tg", source_kind=SourceKind.TELEGRAM_CHANNEL, external_id="123")
    
    # Should not raise RawItemDropped
    draft = await node.process(item)
    assert draft is not None
    assert draft.metadata["llm_search_relevance"] == 0.1
