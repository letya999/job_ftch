"""Attach the immutable ontology views selected for a pipeline run."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from job_ftch.domain import OntologySnapshot, RawItem


class OntologySnapshotNode:
    """Stage[RawItem, RawItem] that makes ontology provenance replayable.

    The builder creates these snapshots once, before processing starts.  This
    node only copies their canonical payload and version into item metadata;
    it never reads a live ontology store while processing an observation.
    """

    def __init__(self, snapshots: Mapping[str, OntologySnapshot]) -> None:
        self._snapshots = dict(snapshots)

    async def process(self, item: RawItem) -> RawItem:
        if not self._snapshots:
            return item
        metadata = {
            **item.metadata,
            "ontology_snapshots": {
                profile_id: {
                    "version": snapshot.version,
                    "payload_json": snapshot.payload_json,
                }
                for profile_id, snapshot in self._snapshots.items()
            },
        }
        return item.model_copy(update={"metadata": metadata})
