import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.application.pipeline import Pipeline
from job_ftch.domain import (
    DuplicateRejectionReason,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.dedup import DedupNode
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.sinks.null_sink import NullSink


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def dedup_node(store):
    return DedupNode(store)


@pytest.mark.anyio
async def test_dedup_node_passes_new_item(dedup_node, store, make_raw_item):
    item = make_raw_item(external_id="new")
    processed = await dedup_node.process(item)
    assert processed is item
    # DedupNode remembers durable content keys.
    from job_ftch.domain import dedup_content_key_for_raw_item

    assert await store.has_dedup_key(dedup_content_key_for_raw_item(item))


@pytest.mark.anyio
async def test_personal_mode_does_not_drop_seen_item(make_raw_item):
    node = DedupNode(InMemoryStore(), personal_mode=True)
    first = make_raw_item(external_id="seen", url="https://example.com/seen")
    second = make_raw_item(external_id="seen-again", url="https://example.com/seen")
    assert await node.process(first) is first
    assert await node.process(second) is second


@pytest.mark.anyio
async def test_dedup_node_drops_duplicate_url(dedup_node, store, make_raw_item):
    url = "https://example.com/job1"
    item1 = make_raw_item(external_id="1", url=url)
    await dedup_node.process(item1)

    item2 = make_raw_item(external_id="2", url=url)
    with pytest.raises(RawItemDropped) as exc:
        await dedup_node.process(item2)
    assert exc.value.reason == DuplicateRejectionReason.DUPLICATE_CONTENT


@pytest.mark.anyio
async def test_dedup_node_drops_exact_content(dedup_node, store, make_raw_item):
    text = "Exact same description"
    item1 = make_raw_item(external_id="1", text=text)
    await dedup_node.process(item1)

    item2 = make_raw_item(external_id="2", text=text)
    with pytest.raises(RawItemDropped) as exc:
        await dedup_node.process(item2)
    assert exc.value.reason == DuplicateRejectionReason.DUPLICATE_CONTENT


@pytest.mark.anyio
async def test_dedup_node_store_error_propagates(make_raw_item):
    class ExplodingStore:
        async def has_dedup_key(self, key: str) -> bool:
            raise RuntimeError("store unavailable")

        async def has_processed(self, key: str) -> bool:
            return False

        async def get_dedup_key(self, key: str):
            raise RuntimeError("store unavailable")

        async def list_dedup_keys(self, kind=None):
            return ()

        async def remember_dedup_key(self, record):
            pass

        async def record_duplicate(self, record):
            pass

        async def mark_processed(self, key):
            pass

    node = DedupNode(ExplodingStore())
    item = make_raw_item()
    with pytest.raises(RuntimeError, match="store unavailable"):
        await node.process(item)


@pytest.mark.anyio
async def test_dedup_node_keeps_cross_source_near_duplicates(dedup_node, make_raw_item):
    item1 = make_raw_item(
        external_id="1",
        source_name="telegram_a",
        text="Senior AI engineer. Build LLM agents and automation workflows for support.",
    )
    await dedup_node.process(item1)

    item2 = make_raw_item(
        external_id="2",
        source_name="telegram_b",
        text="Senior AI engineer. Build LLM agents and automation workflows for support team.",
    )
    processed = await dedup_node.process(item2)

    assert processed is item2


@pytest.mark.anyio
async def test_deferred_claim_releases_after_retryable_failure(store, make_raw_item):
    item = make_raw_item(external_id="retry")
    node = DedupNode(store, defer_commit=True)
    assert await node.process(item) is item
    await node.release_claim(item.stable_id)

    retry = DedupNode(store, defer_commit=True)
    assert await retry.process(item) is item


@pytest.mark.anyio
async def test_pipeline_failure_after_deferred_dedup_does_not_poison_retry(store, make_raw_item):
    item = make_raw_item(external_id="pipeline-retry")

    class Source:
        def fetch(self):
            async def items():
                yield item

            return items()

    class Explode:
        async def process(self, raw):
            raise RuntimeError("downstream failure")

    pipeline = Pipeline(
        source=Source(),
        sanitize_node=SanitizeNode(),
        nodes=[DedupNode(store, defer_commit=True), Explode()],
        sink=NullSink(),
        store=store,
    )
    await pipeline.run()

    retry = DedupNode(store, defer_commit=True)
    assert await retry.process(item) is item
