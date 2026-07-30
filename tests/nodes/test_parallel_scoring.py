import numpy as np
import pytest

from job_ftch.domain import JobRecord, PostType, SourceKind
from job_ftch.nodes.parallel_scoring import ParallelScoringNode


def make_job(dense=None, sparse=None, **kwargs):
    meta = {}
    if dense is not None:
        meta["bgem3_dense"] = dense
    if sparse is not None:
        meta["bgem3_sparse"] = sparse
    meta.update(kwargs)
    return JobRecord(
        title="Test Job",
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="test",
        raw_item_id="1",
        post_type=PostType.JOB_POSTING,
        metadata=meta,
    )


dim = 1024


def unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


rng = np.random.default_rng(42)
pos = unit(rng.standard_normal((3, dim), dtype=np.float32))
neg = unit(rng.standard_normal((2, dim), dtype=np.float32))
pos_sparse = [{0: 1.0, 1: 0.5}] * 3
neg_sparse = [{0: 0.1}] * 2


@pytest.fixture
def node():
    return ParallelScoringNode(
        pos_dense=pos,
        neg_dense=neg,
        pos_sparse=pos_sparse,
        neg_sparse=neg_sparse,
        role_anchors=[],
    )


@pytest.mark.anyio
async def test_parallel_scoring_no_dense_passthrough(node):
    job = make_job()
    res = await node.process(job)
    assert "parallel_final_score" not in res.metadata


@pytest.mark.anyio
async def test_parallel_scoring_identical_pos_neg_scores_near_zero():
    query_vec = unit(rng.random((1, dim), dtype=np.float32))[0]
    n = ParallelScoringNode(
        pos_dense=np.array([query_vec]),
        neg_dense=np.array([query_vec]),
        pos_sparse=[],
        neg_sparse=[],
        role_anchors=[],
    )
    job = make_job(dense=query_vec.tolist())
    res = await n.process(job)
    # sigmoid(0) is exactly 0.5
    assert np.isclose(res.metadata["parallel_final_score"], 0.5, atol=0.1)


@pytest.mark.anyio
async def test_parallel_scoring_close_to_positive_scores_high(node):
    query_vec = pos[0]  # Exact match to first positive anchor
    job = make_job(dense=query_vec.tolist())
    res = await node.process(job)
    assert res.metadata["parallel_final_score"] > 0.6


@pytest.mark.anyio
async def test_parallel_scoring_close_to_negative_scores_low(node):
    query_vec = neg[0]  # Exact match to first negative anchor
    job = make_job(dense=query_vec.tolist())
    res = await node.process(job)
    assert res.metadata["parallel_final_score"] < 0.4


@pytest.mark.anyio
async def test_parallel_scoring_stores_branch_scores_in_metadata(node):
    query_vec = pos[0]
    job = make_job(dense=query_vec.tolist(), sparse={0: 1.0})
    res = await node.process(job)
    assert isinstance(res.metadata.get("parallel_score_dense"), float)
    assert isinstance(res.metadata.get("parallel_score_sparse"), float)
    assert isinstance(res.metadata.get("parallel_score_role"), float)


@pytest.mark.anyio
async def test_parallel_scoring_empty_role_anchors(node):
    query_vec = pos[0]
    job = make_job(dense=query_vec.tolist())
    res = await node.process(job)
    assert not np.isnan(res.metadata["parallel_final_score"])
