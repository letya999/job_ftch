import pytest

from job_ftch.application.search_text import build_job_embedding_text
from job_ftch.domain import SkillTag, WorkMode
from job_ftch.nodes.embedding import EmbeddingNode


class MockEmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [(0.1, 0.2, 0.3) for _ in texts]


class MockVectorBackend:
    def __init__(self):
        self.stored = []
        self.batches = []

    async def upsert(self, job_id: str, vector: tuple[float, ...], payload: dict) -> None:
        self.stored.append((job_id, vector, payload))

    async def upsert_many(self, records: list[tuple[str, tuple[float, ...], dict]]) -> None:
        self.batches.append(records)
        self.stored.extend(records)


@pytest.mark.unit
def test_build_embedding_text_all_fields(make_job_record):
    job = make_job_record(
        title="ML Engineer",
        company="OpenAI",
        location="SF",
        work_mode=WorkMode.REMOTE,
        skills_explicit=(SkillTag(canonical_name="python"),),
        description="Build AI",
    )
    text = build_job_embedding_text(job)
    assert "ML Engineer" in text
    assert "OpenAI" in text
    assert "SF" in text
    assert "remote" in text
    assert "python" in text
    assert "Build AI" in text


@pytest.mark.unit
def test_build_embedding_text_skips_empty_fields(make_job_record):
    job = make_job_record(title="ML Engineer", company=None, description=None)
    text = build_job_embedding_text(job)
    # The factory provides a default description if not specified,
    # but make_job_record(description=None) should override it.
    assert "OpenAI" not in text
    assert "ML Engineer" in text


@pytest.mark.unit
def test_build_embedding_text_unknown_work_mode_skipped(make_job_record):
    job = make_job_record(title="ML", work_mode=WorkMode.UNKNOWN)
    text = build_job_embedding_text(job)
    assert "unknown" not in text


@pytest.mark.unit
def test_build_embedding_text_skills_joined(make_job_record):
    job = make_job_record(
        title="ML",
        skills_explicit=(SkillTag(canonical_name="python"), SkillTag(canonical_name="pytorch")),
    )
    text = build_job_embedding_text(job)
    assert "python, pytorch" in text


@pytest.mark.anyio
async def test_embedding_node_calls_provider_and_stores_vector(make_job_record):
    provider = MockEmbeddingProvider()
    backend = MockVectorBackend()
    node = EmbeddingNode(provider, backend)
    job = make_job_record(group_id="g1", title="ML")
    processed = await node.process(job)
    await node.flush()
    assert processed.metadata["embedding_vector"] == (0.1, 0.2, 0.3)
    assert len(backend.stored) == 1
    assert backend.stored[0][0] == job.stable_id


@pytest.mark.anyio
async def test_embedding_node_raises_without_group_id(make_job_record):
    provider = MockEmbeddingProvider()
    backend = MockVectorBackend()
    node = EmbeddingNode(provider, backend)
    # Ensure both group_id and metadata['group_id'] are missing
    job = make_job_record(group_id=None)
    if "group_id" in job.metadata:
        del job.metadata["group_id"]

    with pytest.raises(ValueError, match="group_id is required"):
        await node.process(job)


@pytest.mark.anyio
async def test_embedding_node_batches_vector_upserts(make_job_record):
    provider = MockEmbeddingProvider()
    backend = MockVectorBackend()
    node = EmbeddingNode(provider, backend, upsert_batch_size=2)

    first = await node.process(make_job_record(group_id="g1", title="ML 1"))
    second = await node.process(make_job_record(group_id="g2", title="ML 2"))
    third = await node.process(make_job_record(group_id="g3", title="ML 3"))
    await node.flush()

    assert first.metadata["embedding_vector"] == (0.1, 0.2, 0.3)
    assert second.metadata["embedding_vector"] == (0.1, 0.2, 0.3)
    assert third.metadata["embedding_vector"] == (0.1, 0.2, 0.3)
    assert [len(batch) for batch in backend.batches] == [2, 1]
