from unittest.mock import AsyncMock

import pytest

from job_ftch.application.contracts import ClassificationResult, LLMProvider
from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import FilterProfile, PostType, RawItem, SourceKind
from job_ftch.infrastructure.classifiers.keyword_classifier import KeywordClassifierProvider
from job_ftch.infrastructure.classifiers.llm_classifier import (
    LLMClassifierProvider,
    _PostTypeSchema,
)
from job_ftch.nodes.source_classifier import SourceClassifierNode


@pytest.fixture
def raw_item():
    return RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test_chan",
        text="Sample ML job for Data Scientist with LLM experience.",
        external_id="ext_1",
    )


@pytest.mark.asyncio
async def test_keyword_classifier_detects_candidate_post():
    profile = FilterProfile.default()
    classifier = KeywordClassifierProvider(profile=profile)
    text = "Hi, i'm looking for opportunities as a Junior ML Engineer #candidate"
    result = await classifier.classify(text)
    assert result.label == "candidate_seeking"
    assert result.confidence >= 0.80


@pytest.mark.asyncio
async def test_keyword_classifier_detects_spam_odds():
    profile = FilterProfile.default()
    classifier = KeywordClassifierProvider(profile=profile)
    text = "Winning strategy for betting! 483.00 odds today!"
    result = await classifier.classify(text)
    assert result.label == "spam"
    assert result.confidence >= 0.80


@pytest.mark.asyncio
async def test_keyword_classifier_passes_unknown(raw_item):
    classifier = KeywordClassifierProvider()
    result = await classifier.classify(raw_item.text)
    assert result.label == "unknown"


@pytest.mark.asyncio
async def test_source_classifier_node_drops_candidate(raw_item):
    classifier = KeywordClassifierProvider()
    node = SourceClassifierNode(classifier)
    item = raw_item.model_copy(update={"text": "I am looking for a job #candidate"})

    with pytest.raises(RawItemDropped) as excinfo:
        await node.process(item)
    assert "candidate_seeking" in excinfo.value.details


@pytest.mark.asyncio
async def test_source_classifier_node_passes_unknown(raw_item):
    classifier = KeywordClassifierProvider()
    node = SourceClassifierNode(classifier)
    result = await node.process(raw_item)
    assert result is raw_item


@pytest.mark.asyncio
async def test_source_classifier_node_respects_threshold(raw_item):
    # Mock a classifier that returns a rejected label but with low confidence
    mock_classifier = AsyncMock()
    mock_classifier.classify.return_value = ClassificationResult(
        label="spam", confidence=0.70, model_id="test_model"
    )

    node = SourceClassifierNode(mock_classifier, confidence_threshold=0.80)
    result = await node.process(raw_item)
    assert result is raw_item


@pytest.mark.asyncio
async def test_llm_classifier_uses_provider():
    # Ensure schema is rebuilt if there were any lazy imports
    _PostTypeSchema.model_rebuild()

    mock_llm = AsyncMock(spec=LLMProvider)
    mock_llm.extract.return_value = _PostTypeSchema(
        post_type=PostType.JOB_POSTING,
        ai_relevance=0.9,
        reasoning="Clearly a job post about AI.",
    )

    classifier = LLMClassifierProvider(mock_llm)
    result = await classifier.classify("Some job text")

    assert result.label == "job_posting"
    assert result.confidence == 0.9
    assert result.model_id == "llm_classifier_v1"
