from __future__ import annotations

import pytest

from job_ftch.application.enrichment import PostAcceptEnrichmentQueue
from job_ftch.domain import EnrichmentTask, MatchDecision
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.post_accept_enrichment import PostAcceptEnrichment


@pytest.mark.asyncio
async def test_post_accept_enrichment_is_idempotent() -> None:
    queue = PostAcceptEnrichmentQueue(InMemoryStore())
    task = EnrichmentTask(
        observation_id="obs-1",
        group_id="group-1",
        operations=("presentation", "embedding"),
        policy_version="evidence-v1",
    )
    await queue.enqueue(task)
    await queue.enqueue(task)
    pending = await queue.list_pending()
    assert pending == (task,)


@pytest.mark.asyncio
async def test_synchronous_post_accept_enriches_and_persists_before_return(
    make_job_record,
) -> None:
    class Stage:
        async def process(self, job):
            return job.model_copy(update={"description": "enriched"})

    class GroupStore:
        def __init__(self) -> None:
            self.saved = None

        async def merge(self, group_id, job, merge_confidence=1.0):
            self.saved = (group_id, job, merge_confidence)

    store = GroupStore()
    node = PostAcceptEnrichment(stages=(Stage(),), group_store=store)
    job = make_job_record(
        group_id="group-1",
        routing_decision=MatchDecision.ACCEPT,
    )

    result = await node.process(job)

    assert result.description == "enriched"
    assert result.metadata["post_accept_enrichment"] == "completed"
    assert store.saved == ("group-1", result, 1.0)
