# DEPRECATED: migrated to tests/nodes/test_hard_filter.py
import json

import pytest
from pydantic import ValidationError

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.filter_profile_loader import load_filter_profile
from job_ftch.domain import (
    FilterProfile,
    Job,
    JobValidationRejectionReason,
    RawItem,
    SourceKind,
    TriageRejectionReason,
)
from job_ftch.nodes.relevance import AIRoleRelevanceNode
from job_ftch.nodes.triage import HeuristicTriageNode

# Note: JobValidationRelevanceReason is not in domain/__init__.py, let's check domain/job_quality.py
# Actually, the plan uses JobValidationRejectionReason.JOB_OUT_OF_SCOPE.
# Let's verify imports.


def test_default_profile_is_neutral_shell():
    profile = FilterProfile.default()
    assert not profile.target_roles
    assert profile.profile_description is None
    assert not profile.positive_relevance_keywords
    assert not profile.negative_relevance_keywords
    assert not profile.preferred_skills
    assert profile.min_text_tokens == 3
    assert profile.min_text_chars == 18
    assert profile.relevance_threshold == 0.0


@pytest.mark.asyncio
async def test_triage_node_uses_profile_min_lengths():
    profile = FilterProfile(min_text_tokens=10, min_text_chars=100)
    node = HeuristicTriageNode(profile=profile)
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        text="Short text with five tokens.",  # 5 tokens, < 100 chars
        external_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert exc.value.reason == TriageRejectionReason.TOO_SHORT


@pytest.mark.asyncio
async def test_triage_node_explicit_profile_backward_compatible():
    profile = FilterProfile(
        exclude_keywords=["webinar"],
        positive_relevance_keywords=["ml", "engineer", "ai"],
    )
    node = HeuristicTriageNode(profile=profile)
    # Should drop webinar
    item_webinar = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        text="Check out our latest webinar signup now",
        external_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item_webinar)
    assert exc.value.reason == TriageRejectionReason.IRRELEVANT_CONTENT

    # Should pass job
    item_job = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        text="Senior ML engineer, remote, salary negotiable",
        external_id="124",
    )
    processed = await node.process(item_job)
    assert processed is item_job


@pytest.mark.asyncio
async def test_triage_node_custom_exclude_drops_item():
    profile = FilterProfile(exclude_keywords=["sponsor"], positive_relevance_keywords=["ai"])
    node = HeuristicTriageNode(profile=profile)
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        text="sponsor this event and get visibility in our community",
        external_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item)
    assert exc.value.reason == TriageRejectionReason.IRRELEVANT_CONTENT


@pytest.mark.asyncio
async def test_triage_node_allowed_source_kinds_filters():
    profile = FilterProfile(
        allowed_source_kinds=[SourceKind.TELEGRAM_CHANNEL], positive_relevance_keywords=["ai"]
    )
    node = HeuristicTriageNode(profile=profile)

    # Group should be dropped
    item_group = RawItem(
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="test",
        text="AI Engineer wanted",
        external_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item_group)
    assert exc.value.reason == TriageRejectionReason.IRRELEVANT_CONTENT

    # Channel should pass
    item_channel = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        text="AI Engineer wanted",
        external_id="124",
    )
    processed = await node.process(item_channel)
    assert processed is item_channel


@pytest.mark.asyncio
async def test_triage_node_required_keywords():
    profile = FilterProfile(required_keywords=["python"], positive_relevance_keywords=["ai"])
    node = HeuristicTriageNode(profile=profile)

    # No python -> drop
    item_no_python = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        text="AI Engineer wanted, Java expert",
        external_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(item_no_python)
    assert exc.value.reason == TriageRejectionReason.IRRELEVANT_CONTENT

    # With python -> pass
    item_python = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        text="AI Engineer wanted, Python expert",
        external_id="124",
    )
    processed = await node.process(item_python)
    assert processed is item_python


@pytest.mark.asyncio
async def test_relevance_node_uses_profile_keywords():
    profile = FilterProfile(
        positive_relevance_keywords=["backend", "api"],
        negative_relevance_keywords=["sales"],
        relevance_threshold=0.0,
    )
    node = AIRoleRelevanceNode(profile=profile)

    # Positive hits
    job_ok = Job(
        title="Backend API Engineer",
        description="Build APIs",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        raw_item_id="123",
    )
    processed = await node.process(job_ok)
    assert processed.relevance_score > 0

    # Negative hit
    job_bad = Job(
        title="Sales Manager",
        description="Sell things",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        raw_item_id="124",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job_bad)
    assert exc.value.reason == JobValidationRejectionReason.JOB_OUT_OF_SCOPE


@pytest.mark.asyncio
async def test_relevance_node_empty_positive_keywords_passthrough():
    profile = FilterProfile(positive_relevance_keywords=[], negative_relevance_keywords=[])
    node = AIRoleRelevanceNode(profile=profile)
    job = Job(
        title="Anything",
        description="Whatever",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        raw_item_id="123",
    )
    processed = await node.process(job)
    assert processed is job


@pytest.mark.asyncio
async def test_relevance_node_threshold():
    profile = FilterProfile(positive_relevance_keywords=["python"], relevance_threshold=0.5)
    node = AIRoleRelevanceNode(profile=profile)

    # 1 hit -> 0.33 -> drop
    job_1hit = Job(
        title="Python Developer",
        description="Coding",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        raw_item_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job_1hit)
    assert exc.value.reason == JobValidationRejectionReason.JOB_OUT_OF_SCOPE

    # 2 hits -> 0.66 -> pass (relevance score based on hits in haystack: title+company+description)
    # Wait, 2 hits of SAME keyword count as 1 in current implementation?
    # sum(1 for keyword in self._profile.positive_relevance_keywords if keyword in haystack)
    # Yes, unique keywords count.

    profile_2 = FilterProfile(
        positive_relevance_keywords=["python", "fastapi"], relevance_threshold=0.5
    )
    node_2 = AIRoleRelevanceNode(profile=profile_2)
    job_2hits = Job(
        title="Python FastAPI Developer",
        description="Coding",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        raw_item_id="124",
    )
    processed = await node_2.process(job_2hits)
    assert processed.relevance_score > 0.5


@pytest.mark.asyncio
async def test_relevance_node_explicit_profile_backward_compatible():
    profile = FilterProfile(
        positive_relevance_keywords=["llm", "ai"], negative_relevance_keywords=["marketing"]
    )
    node = AIRoleRelevanceNode(profile=profile)

    # Drop marketing
    job_marketing = Job(
        title="Marketing Director",
        description="Marketing things",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        raw_item_id="123",
    )
    with pytest.raises(RawItemDropped) as exc:
        await node.process(job_marketing)
    assert exc.value.reason == JobValidationRejectionReason.JOB_OUT_OF_SCOPE

    # Pass LLM
    job_llm = Job(
        title="LLM Engineer",
        description="AI startup",
        source_kind=SourceKind.CAREER_SITE,
        source_name="test",
        raw_item_id="124",
    )
    processed = await node.process(job_llm)
    assert processed.relevance_score > 0


def test_filter_profile_immutable():
    profile = FilterProfile.default()
    with pytest.raises(ValidationError):
        # Pydantic v2 frozen model raises ValidationError on __setattr__?
        # Actually it's often a ValidationError or AttributeError depending on config.
        # model_config = ConfigDict(frozen=True)
        profile.name = "new name"


def test_filter_profile_load_from_json(tmp_path):
    data = {"name": "custom", "min_text_tokens": 5, "positive_relevance_keywords": ["test"]}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))

    profile = load_filter_profile(path)
    assert profile.name == "custom"
    assert profile.min_text_tokens == 5
    assert "test" in profile.positive_relevance_keywords
