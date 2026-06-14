import pytest

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import (
    DuplicateRejectionReason,
)
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.dedup import DedupNode


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
    # DedupNode remembers keys after processing
    # Note: it remembers dedup keys, not necessarily 'processed' key (which pipeline handles)
    # But it does remember content key.
    from job_ftch.domain import dedup_content_key_for_raw_item

    assert await store.has_dedup_key(dedup_content_key_for_raw_item(item))


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
