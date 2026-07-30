"""Tests for the three-phase career-site ingestion lifecycle."""

from __future__ import annotations

import pytest

from job_ftch.domain.ingest_models import (
    CONFIDENT_THRESHOLD,
    PARTIAL_THRESHOLD,
    DiscoveredCandidate,
    IngestItemStatus,
    score_completeness,
)
from job_ftch.domain.site_models import DiscoveredPostingPayload


class TestScoreCompleteness:
    def test_url_only(self):
        payload = DiscoveredPostingPayload(url="https://example.com/jobs/123")
        assert score_completeness(payload) == pytest.approx(0.1)

    def test_url_and_title(self):
        payload = DiscoveredPostingPayload(
            url="https://example.com/jobs/123",
            title="Senior Engineer",
        )
        assert score_completeness(payload) == pytest.approx(0.3)

    def test_full_ats_payload(self):
        payload = DiscoveredPostingPayload(
            url="https://example.com/jobs/123",
            title="Senior Engineer",
            description="x" * 150,
            locations=["Berlin"],
            date_posted="2026-01-01",
            base_salary={"min": 50000},
            metadata={"company": "Acme"},
        )
        score = score_completeness(payload)
        assert score >= CONFIDENT_THRESHOLD

    def test_short_description_not_counted(self):
        payload = DiscoveredPostingPayload(
            url="https://example.com/jobs/123",
            title="Senior Engineer",
            description="Short",
        )
        assert score_completeness(payload) == pytest.approx(0.3)

    def test_capped_at_one(self):
        payload = DiscoveredPostingPayload(
            url="https://example.com/jobs/123",
            title="Senior Engineer",
            description="x" * 200,
            locations=["Berlin", "Munich"],
            date_posted="2026-01-01",
            base_salary={"min": 50000, "max": 80000},
            metadata={"company": "Acme Corp"},
        )
        assert score_completeness(payload) <= 1.0


class TestDiscoveredCandidate:
    def test_default_status(self):
        c = DiscoveredCandidate(url="https://example.com/jobs/1")
        assert c.status == IngestItemStatus.DISCOVERED

    def test_with_rich_payload(self):
        payload = DiscoveredPostingPayload(
            url="https://example.com/jobs/1",
            title="Engineer",
            description="Build things " * 20,
        )
        c = DiscoveredCandidate(
            url="https://example.com/jobs/1",
            rich_payload=payload,
            completeness=score_completeness(payload),
        )
        assert c.completeness >= PARTIAL_THRESHOLD


class TestIngestItemStatus:
    def test_all_statuses_exist(self):
        expected = {"discovered", "processing", "new", "duplicate", "rejected", "failed", "expired"}
        assert {s.value for s in IngestItemStatus} == expected
