"""Synchronous, non-policy enrichment for accepted records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.domain import MatchDecision

if TYPE_CHECKING:
    from job_ftch.application.contracts import JobGroupStore, Stage
    from job_ftch.domain import JobRecord


class PostAcceptEnrichment:
    """Finish accepted records before the pipeline exposes them to adapters.

    With no injected stages this remains eval-safe.  Production injects the
    full extraction and deterministic presentation stages plus the group store.
    The final merge is important: aggregation runs immediately before this node,
    so the canonical record read by `/run` must be replaced with the enriched
    version before the graph returns.
    """

    def __init__(
        self,
        *,
        stages: tuple[Stage[Any, Any], ...] = (),
        group_store: JobGroupStore | None = None,
    ) -> None:
        self._stages = stages
        self._group_store = group_store

    async def process(self, item: JobRecord) -> JobRecord:
        if item.routing_decision is not MatchDecision.ACCEPT:
            return item
        current = item
        for stage in self._stages:
            result = await stage.process(current)
            if result is None:
                raise RuntimeError(f"post-accept stage {type(stage).__name__} returned no record")
            current = result
        current = current.model_copy(
            update={
                "metadata": {
                    **current.metadata,
                    "post_accept_enrichment": "completed",
                }
            }
        )
        if self._group_store is not None:
            if not current.group_id:
                raise RuntimeError("accepted post-accept record has no group_id")
            replace = getattr(self._group_store, "replace_member", None)
            if callable(replace):
                await replace(current.group_id, current)
            else:
                await self._group_store.merge(current.group_id, current, merge_confidence=1.0)
        return current
