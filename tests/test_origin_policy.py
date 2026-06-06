from __future__ import annotations

import pytest

from application import ProcessingContext
from application.outcomes import OutcomeKind, RejectReason
from domain import RawItem, SourceKind
from nodes import OriginPolicyNode


@pytest.mark.asyncio
async def test_origin_policy_allows_configured_career_site_host() -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="greenhouse",
        external_id="job-1",
        url="https://job-boards.greenhouse.io/company/jobs/1",
        text="Hiring AI engineer",
    )

    outcome = await OriginPolicyNode(
        allowed_career_site_hosts=("job-boards.greenhouse.io",)
    ).process(item, ProcessingContext())

    assert outcome.kind is OutcomeKind.PASS
    assert outcome.item == item


@pytest.mark.asyncio
async def test_origin_policy_rejects_disallowed_career_site_host() -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="greenhouse",
        external_id="job-1",
        url="https://evil.example/jobs/1",
        text="Hiring AI engineer",
    )

    outcome = await OriginPolicyNode(
        allowed_career_site_hosts=("job-boards.greenhouse.io",)
    ).process(item, ProcessingContext())

    assert outcome.kind is OutcomeKind.QUARANTINE
    assert outcome.reason is RejectReason.DISALLOWED_URL_HOST


@pytest.mark.asyncio
async def test_origin_policy_rejects_private_career_site_host() -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="internal",
        external_id="job-1",
        url="https://127.0.0.1/jobs/1",
        text="Hiring AI engineer",
    )

    outcome = await OriginPolicyNode(allowed_career_site_hosts=("127.0.0.1",)).process(
        item,
        ProcessingContext(),
    )

    assert outcome.kind is OutcomeKind.QUARANTINE
    assert outcome.reason is RejectReason.PRIVATE_URL_HOST


@pytest.mark.asyncio
async def test_origin_policy_allows_telegram_host_for_telegram_source() -> None:
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="ai_jobs",
        external_id="42",
        url="https://t.me/ai_jobs/42",
        text="Hiring AI engineer",
    )

    outcome = await OriginPolicyNode().process(item, ProcessingContext())

    assert outcome.kind is OutcomeKind.PASS


@pytest.mark.asyncio
async def test_origin_policy_rejects_non_telegram_host_for_telegram_source() -> None:
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_GROUP,
        source_name="ai_jobs",
        external_id="42",
        url="https://example.com/ai_jobs/42",
        text="Hiring AI engineer",
    )

    outcome = await OriginPolicyNode().process(item, ProcessingContext())

    assert outcome.kind is OutcomeKind.QUARANTINE
    assert outcome.reason is RejectReason.DISALLOWED_URL_HOST


@pytest.mark.asyncio
async def test_origin_policy_checks_metadata_origin_urls() -> None:
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="greenhouse",
        external_id="job-1",
        url="https://job-boards.greenhouse.io/company/jobs/1",
        text="Hiring AI engineer",
        metadata={"board_url": "https://evil.example/company"},
    )

    outcome = await OriginPolicyNode(
        allowed_career_site_hosts=("job-boards.greenhouse.io",)
    ).process(item, ProcessingContext())

    assert outcome.kind is OutcomeKind.QUARANTINE
    assert outcome.reason is RejectReason.DISALLOWED_ORIGIN_HOST
