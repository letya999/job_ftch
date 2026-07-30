import numpy as np
import pytest

from job_ftch.domain import RawItem, SourceKind


def make_raw(text="some job text"):
    return RawItem.model_construct(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        external_id="1",
        text=text,
        metadata={},
    )


class MockProvider:
    def __init__(self, dense=None, sparse=None):
        dim = 8
        self._dense = dense if dense is not None else np.ones(dim, dtype=np.float32)
        self._sparse = sparse if sparse is not None else {0: 0.5}

    def encode(self, text, *, max_length=512, return_sparse=False):
        del max_length
        assert return_sparse is True
        return {"dense": self._dense, "sparse": self._sparse}


class FailingProvider:
    def encode(self, text, *, max_length=512, return_sparse=False):
        del max_length
        raise RuntimeError("BGE model not loaded")


@pytest.mark.anyio
async def test_bge_embed_node_adds_dense_and_sparse():
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    provider = MockProvider()
    node = BgeMThreeNode(provider)
    result = await node.process(make_raw("Software engineer role"))
    assert "bgem3_dense" in result.metadata
    assert "bgem3_sparse" in result.metadata
    assert isinstance(result.metadata["bgem3_dense"], list)


@pytest.mark.anyio
async def test_bge_embed_node_encoding_failure_is_nonfatal():
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    node = BgeMThreeNode(FailingProvider())
    item = make_raw("Software engineer role")
    result = await node.process(item)
    # Item returned unchanged, no exception raised
    assert result is item


@pytest.mark.anyio
async def test_bge_embed_node_missing_sparse_is_nonfatal() -> None:
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    class DenseOnlyProvider:
        def encode(self, text, *, max_length=512, return_sparse=False):
            del max_length
            assert return_sparse is True
            return {"dense": np.ones(8, dtype=np.float32)}

    item = make_raw("Software engineer role")
    assert await BgeMThreeNode(DenseOnlyProvider()).process(item) is item


@pytest.mark.anyio
async def test_bge_embed_node_encode_runs_in_thread(monkeypatch: pytest.MonkeyPatch):
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    calls: list[tuple[object, str]] = []

    async def _fake_to_thread(func, *args, **kwargs):
        calls.append((func, args[0]))
        return func(*args, **kwargs)

    monkeypatch.setattr("job_ftch.nodes.bge_embed_node.asyncio.to_thread", _fake_to_thread)

    provider = MockProvider()
    node = BgeMThreeNode(provider)
    await node.process(make_raw("Software engineer role"))

    assert calls
    assert calls[0][0] == provider.encode


@pytest.mark.anyio
async def test_bge_embed_node_encode_error_logged_not_raised(monkeypatch: pytest.MonkeyPatch):
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    logged: list[tuple[str, dict[str, object]]] = []

    def _fake_warning(event: str, **kwargs: object) -> None:
        logged.append((event, kwargs))

    monkeypatch.setattr("job_ftch.nodes.bge_embed_node.logger.warning", _fake_warning)

    node = BgeMThreeNode(FailingProvider())
    item = make_raw("Software engineer role")
    result = await node.process(item)

    assert result is item
    assert logged
    assert logged[0][0] == "bge_embed_node_encode_failed"


@pytest.mark.anyio
async def test_bge_embed_node_empty_text_skips_encoding():
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    provider = MockProvider()
    node = BgeMThreeNode(provider)
    result = await node.process(make_raw(""))
    assert "bgem3_dense" not in result.metadata


@pytest.mark.anyio
async def test_bge_embed_node_truncates_text_to_max_chars():
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    seen = []

    class TrackingProvider:
        def encode(self, text, *, max_length=512, return_sparse=False):
            del max_length
            assert return_sparse is True
            seen.append(text)
            return {"dense": np.ones(8, dtype=np.float32), "sparse": {}}

    node = BgeMThreeNode(TrackingProvider(), max_chars=10)
    await node.process(make_raw("x" * 100))
    assert len(seen[0]) <= 10


@pytest.mark.anyio
async def test_bge_embed_node_preserves_existing_metadata():
    from job_ftch.nodes.bge_embed_node import BgeMThreeNode

    item = make_raw("Engineer job")
    item = item.model_copy(update={"metadata": {"existing": "value"}})
    result = await BgeMThreeNode(MockProvider()).process(item)
    assert result.metadata["existing"] == "value"
