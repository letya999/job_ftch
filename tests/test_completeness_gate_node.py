"""Tests for CompletenessGateNode."""

from __future__ import annotations

import pytest

from job_ftch.domain.models import RawItem, SourceKind
from job_ftch.nodes.completeness_gate import CompletenessGateNode, score_completeness


def _make_raw_item(
    *,
    source_kind: SourceKind = SourceKind.CAREER_SITE,
    text: str = "Build distributed systems for our ML platform. " * 10,
    url: str = "https://boards.greenhouse.io/acme/jobs/123",
    metadata: dict | None = None,
) -> RawItem:
    meta = metadata or {}
    return RawItem(
        source_kind=source_kind,
        source_name="test_source",
        text=text,
        url=url,
        external_id=url,
        metadata=meta,
    )


@pytest.mark.asyncio
class TestCompletenessGateNode:
    async def test_trusted_ats_emits_field_evidence_not_job_draft(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            metadata={
                "monitor_type": "greenhouse",
                "title": "Senior Engineer",
                "company": "Acme",
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)
        assert result.metadata["extraction_cost_hint"] == "structured"
        assert result.metadata["structured_source_evidence"][0]["field_name"] == "title"
        assert "hiring_intent" not in result.metadata

    async def test_json_ld_source_emits_evidence(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            metadata={
                "extraction_source": "json_ld",
                "title": "Backend Developer",
                "company": "Corp",
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)
        assert result.metadata["extraction_cost_hint"] == "structured"

    async def test_telegram_structured_emits_evidence(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            source_kind=SourceKind.TELEGRAM_CHANNEL,
            url="https://t.me/jobs/123",
            metadata={
                "extraction_source": "telegram_structured",
                "title": "ML Engineer",
                "company": "StartupX",
                "location": "Remote",
                "salary": "300k",
            },
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)
        assert {entry["field_name"] for entry in result.metadata["structured_source_evidence"]} >= {
            "title",
            "company",
            "location",
            "salary",
        }

    async def test_high_completeness_without_monitor_is_only_cost_hint(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            metadata={
                "title": "DevOps Lead",
                "company": "BigCo",
                "location": "Berlin",
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)
        assert result.metadata["extraction_cost_hint"] == "structured"

    async def test_untrusted_low_completeness_passes_through(self):
        node = CompletenessGateNode()
        item = _make_raw_item(metadata={"title": "Some Role"})
        result = await node.process(item)
        assert isinstance(result, RawItem)

    async def test_missing_title_passes_through(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            metadata={
                "monitor_type": "greenhouse",
                "company": "Acme",
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)

    async def test_short_text_lowers_completeness(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            text="Short",
            metadata={"title": "Eng", "company": "Co"},
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)

    async def test_location_from_list(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            metadata={
                "monitor_type": "greenhouse",
                "title": "Engineer",
                "locations": ["Berlin", "Munich"],
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)
        assert result.metadata["extraction_cost_hint"] == "structured"

    async def test_location_from_city(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            metadata={
                "extraction_source": "json_ld",
                "title": "Analyst",
                "city": "London",
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)
        assert any(
            entry["field_name"] == "location" and entry["value"] == "London"
            for entry in result.metadata["structured_source_evidence"]
        )

    async def test_custom_threshold(self):
        node = CompletenessGateNode(threshold=0.95)
        item = _make_raw_item(
            metadata={
                "title": "DevOps",
                "company": "Co",
                "location": "NY",
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)

    async def test_completeness_never_sets_extraction_complete_or_hiring_intent(self):
        node = CompletenessGateNode()
        item = _make_raw_item(
            metadata={
                "monitor_type": "lever",
                "title": "SRE",
                "company": "Acme",
            }
        )
        result = await node.process(item)
        assert isinstance(result, RawItem)
        assert "_extraction_complete" not in result.metadata
        assert "hiring_intent" not in result.metadata


class TestScoreCompleteness:
    def test_full_metadata(self):
        meta = {
            "title": "Eng",
            "company": "Co",
            "canonical_url": "https://x.com",
            "location": "NY",
            "salary": "100k",
        }
        assert score_completeness(meta, "x " * 100) == 1.0

    def test_empty_metadata(self):
        assert score_completeness({}, "") == 0.0

    def test_title_only(self):
        assert score_completeness({"title": "Eng"}, "short") == 0.25
