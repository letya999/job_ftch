import pytest

from job_ftch.nodes.extraction import ExtractionNode


@pytest.mark.asyncio
async def test_structured_source_skips_llm_and_preserves_core_fields(make_raw_item) -> None:
    class ExplodingLLM:
        async def extract(self, *_args, **_kwargs):
            raise AssertionError("structured source must not call LLM")

    item = make_raw_item(
        metadata={
            "monitor_type": "greenhouse",
            "title": "Senior Python Engineer",
            "company": "Example",
            "location": "Remote",
            "extraction_cost_hint": "structured",
        }
    )
    draft = await ExtractionNode(ExplodingLLM()).process(item)
    assert draft is not None
    assert draft.title_raw == "Senior Python Engineer"
    assert draft.company_name_raw == "Example"
