from __future__ import annotations

import pytest

from job_ftch.application import Pipeline
from job_ftch.domain import (
    DuplicateRejectionReason,
    RawItem,
    SourceKind,
    processed_key_for_raw_item,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import DedupNode, HeuristicTriageNode, SanitizeNode


class StubSource:
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self):  # type: ignore[no-untyped-def]
        async def _items():  # type: ignore[no-untyped-def]
            for item in self._items:
                yield item

        return _items()


class CollectSink:
    def __init__(self) -> None:
        self.items: list[RawItem] = []

    async def emit(self, item: RawItem) -> None:
        self.items.append(item)


def _career_item(*, external_id: str, job_url: str, text: str) -> RawItem:
    return RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_name="ClickHouse",
        external_id=external_id,
        url=job_url,
        text=text,
        metadata={
            "job_url": job_url,
            "title": "Senior AI Engineer",
            "company": "Acme",
            "location": "Remote Europe",
        },
    )


def _telegram_item(*, external_id: str, text: str, job_url: str | None = None) -> RawItem:
    metadata = {"title": "Senior AI Engineer", "company": "Acme"}
    if job_url is not None:
        metadata["job_url"] = job_url
    return RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="AI Jobs Board",
        external_id=external_id,
        url=f"https://t.me/ai_jobs_board/{external_id}",
        text=text,
        metadata=metadata,
    )


def _pipeline(items: list[RawItem], store: InMemoryStore, sink: CollectSink) -> Pipeline[RawItem]:
    return Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(allowed_career_site_hosts=("careers.acme.com",)),
        nodes=[HeuristicTriageNode(), DedupNode(store)],
        sink=sink,
        store=store,
    )


@pytest.mark.asyncio
async def test_pipeline_dedups_by_canonical_url_and_records_reason() -> None:
    store = InMemoryStore()
    sink = CollectSink()
    job_url = "https://careers.acme.com/jobs/42"
    first = _career_item(
        external_id="42",
        job_url=job_url,
        text="Senior AI Engineer\nAcme\nRemote Europe\nBuild LLM systems",
    )
    second = _career_item(
        external_id="vacancy-42-reposted",
        job_url=job_url,
        text="Senior AI Engineer\nAcme\nRemote Europe\nBuild LLM systems and partner with product",
    )

    summary = await _pipeline([first, second], store, sink).run()
    duplicate_records = await store.list_duplicate_records()

    assert summary.emitted == 1
    assert summary.dropped == 1
    assert sink.items == [first]
    assert duplicate_records[0].reason is DuplicateRejectionReason.DUPLICATE_URL
    assert await store.has_processed(processed_key_for_raw_item(second)) is True


@pytest.mark.asyncio
async def test_pipeline_detects_exact_content_duplicates_across_sources() -> None:
    store = InMemoryStore()
    sink = CollectSink()
    first = _career_item(
        external_id="42",
        job_url="https://careers.acme.com/jobs/42",
        text="Senior AI Engineer\nAcme\nRemote Europe\nBuild LLM systems",
    )
    second = _telegram_item(
        external_id="102",
        text="Senior AI Engineer\nAcme\nRemote Europe\nBuild LLM systems",
    )

    summary = await _pipeline([first, second], store, sink).run()
    duplicate_records = await store.list_duplicate_records()

    assert summary.emitted == 1
    assert summary.dropped == 1
    assert duplicate_records[0].reason is DuplicateRejectionReason.DUPLICATE_CONTENT
    assert duplicate_records[0].matched_source_kind is SourceKind.CAREER_SITE


@pytest.mark.asyncio
async def test_pipeline_detects_near_duplicates_and_reruns_are_idempotent() -> None:
    store = InMemoryStore()
    first_sink = CollectSink()
    first = _career_item(
        external_id="42",
        job_url="https://careers.acme.com/jobs/42",
        text="Senior AI Engineer\nAcme\nRemote Europe\nBuild LLM systems for internal agents",
    )
    second = _telegram_item(
        external_id="103",
        text="Acme hiring Senior AI Engineer in remote Europe to build internal LLM agents now",
    )

    first_summary = await _pipeline([first, second], store, first_sink).run()
    rerun_sink = CollectSink()
    rerun_summary = await _pipeline([second], store, rerun_sink).run()
    duplicate_records = await store.list_duplicate_records()

    assert first_summary.emitted == 1
    assert first_summary.dropped == 1
    assert duplicate_records[0].reason is DuplicateRejectionReason.DUPLICATE_NEAR_MATCH
    assert duplicate_records[0].score is not None
    assert rerun_summary.emitted == 0
    assert rerun_summary.drop_reasons["already_processed"] == 1
    assert rerun_sink.items == []
