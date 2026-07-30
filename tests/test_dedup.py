from __future__ import annotations

import pytest

from job_ftch.application import Pipeline
from job_ftch.domain import (
    DuplicateRejectionReason,
    RawItem,
    SourceKind,
    content_hash_for_raw_item,
    processed_key_for_raw_item,
    processed_key_for_url,
)
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes import DedupNode, SanitizeNode
from job_ftch.nodes.triage import HeuristicTriageNode


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


@pytest.mark.parametrize(
    ("source_name", "url"),
    [
        ("Acme Corp", "https://careers.acme.com/jobs/senior-eng-123"),
        ("MixedCase Co", "https://jobs.example.com/vacancy/42"),
        ("Юникод Компания", "https://careers.example.ru/vakansii/99"),
    ],
)
def test_locator_key_is_not_content_versioned_processed_key(source_name: str, url: str) -> None:
    """A discovered URL cannot prove the identity of its current payload."""
    scraped = build_raw_item(
        url=url,
        text="Senior Engineer\n\nJob description body",
        source_name=source_name,
        source_kind=SourceKind.CAREER_SITE,
        external_id=url,
        metadata={"title": "Senior Engineer"},
    )
    assert processed_key_for_url(SourceKind.CAREER_SITE, source_name, url) != (
        processed_key_for_raw_item(scraped)
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
    from job_ftch.domain import FilterProfile

    profile = FilterProfile(positive_relevance_keywords=["ai", "llm", "engineer"])
    return Pipeline(
        source=StubSource(items),
        sanitize_node=SanitizeNode(allowed_career_site_hosts=("careers.acme.com",)),
        nodes=[HeuristicTriageNode(profile=profile), DedupNode(store)],
        sink=sink,
        store=store,
    )


@pytest.mark.asyncio
async def test_pipeline_records_and_reprocesses_changed_content_version() -> None:
    store = InMemoryStore()
    first = _telegram_item(external_id="100", text="AI Engineer at Acme")
    changed = first.model_copy(update={"text": "AI Engineer at Acme, salary $100k"})

    first_sink = CollectSink()
    changed_sink = CollectSink()
    await Pipeline(
        source=StubSource([first]),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=first_sink,
        store=store,
    ).run()
    await Pipeline(
        source=StubSource([changed]),
        sanitize_node=SanitizeNode(),
        nodes=[],
        sink=changed_sink,
        store=store,
    ).run()

    first_entry = await store.get_observation(first.stable_id, content_hash_for_raw_item(first))
    changed_entry = await store.get_observation(
        changed.stable_id, content_hash_for_raw_item(changed)
    )
    assert [item.text for item in first_sink.items] == [first.text]
    assert [item.text for item in changed_sink.items] == [changed.text]
    assert first_entry is not None and first_entry.content_version == 1
    assert changed_entry is not None and changed_entry.content_version == 2


@pytest.mark.asyncio
async def test_pipeline_reprocesses_changed_content_at_the_same_canonical_url() -> None:
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
    expected_item = first.model_copy(deep=True)
    expected_item.metadata["original_posting_text"] = expected_item.text
    expected_item.metadata["source_run_id"] = summary.source_run_id
    changed_expected = second.model_copy(deep=True)
    changed_expected.metadata["original_posting_text"] = changed_expected.text
    changed_expected.metadata["source_run_id"] = summary.source_run_id

    assert summary.emitted == 2
    assert summary.dropped == 0
    assert sink.items == [expected_item, changed_expected]
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
async def test_pipeline_preserves_cross_source_near_duplicates_and_reruns_are_idempotent() -> None:
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

    # Cross-source near matches remain candidates for later identity/grouping;
    # only exact raw dedup is safe before extraction.
    assert first_summary.emitted == 2
    assert first_summary.dropped == 0
    assert duplicate_records == ()
    assert rerun_summary.emitted == 0
    assert rerun_summary.drop_reasons["already_processed"] == 1
    assert rerun_sink.items == []


# ---------------------------------------------------------------------------
# Store-error behaviour (P2 — from TEST_IMPROVEMENTS.md §9)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dedup_node_store_error_propagates() -> None:
    """When store.has_dedup_key() raises, DedupNode must propagate the exception."""

    class ExplodingStore:
        async def has_dedup_key(self, key: str) -> bool:
            raise RuntimeError("store unavailable")

        async def get_dedup_key(self, key: str):
            raise RuntimeError("store unavailable")

        async def list_dedup_keys(self, kind: str | None = None) -> tuple[object, ...]:
            return ()

        async def remember_dedup_key(self, record: object) -> None:
            pass

        async def record_duplicate(self, record: object) -> None:
            pass

    node = DedupNode(ExplodingStore())  # type: ignore[arg-type]
    item = _career_item(
        external_id="1",
        job_url="https://careers.acme.com/1",
        text="Senior ML Engineer at Acme Corp remote position",
    )

    with pytest.raises(RuntimeError, match="store unavailable"):
        await node.process(item)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dedup_node_does_not_load_fingerprint_records_for_raw_suppression(make_raw_item):
    class CountingStore:
        def __init__(self) -> None:
            self._records: dict[str, object] = {}
            self.fingerprint_list_calls = 0

        async def get_dedup_key(self, key: str):
            return self._records.get(key)

        async def list_dedup_keys(self, kind: str | None = None):
            if kind == "fingerprint":
                self.fingerprint_list_calls += 1
            return tuple(
                record
                for record in self._records.values()
                if kind is None or record.kind.value == kind
            )

        async def remember_dedup_key(self, record):
            self._records[record.key] = record

        async def record_duplicate(self, record):
            pass

    store = CountingStore()
    node = DedupNode(store)

    first = _career_item(
        external_id="42",
        job_url="https://careers.acme.com/jobs/42",
        text="Senior AI Engineer\nAcme\nRemote Europe\nBuild LLM systems for internal agents",
    )
    second = _telegram_item(
        external_id="103",
        text="Acme hiring Senior AI Engineer in remote Europe to build internal LLM agents now",
    )
    third = _telegram_item(
        external_id="104",
        text="Remote Europe role: Senior AI Engineer at Acme building internal LLM agents",
    )

    await node.process(first)
    assert await node.process(second) is second
    await node.process(third)

    assert store.fingerprint_list_calls == 0
