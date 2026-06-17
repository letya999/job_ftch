"""Incremental snapshot filter: drop items already seen in the previous run."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain.triage import TriageRejectionReason

if TYPE_CHECKING:
    from job_ftch.domain.models import RawItem

logger = structlog.get_logger(__name__)


def _content_hash(item: RawItem) -> str:
    """Stable hash of the item content used for change detection."""
    parts = [
        str(item.url or ""),
        str(item.text or ""),
        str(item.source_name or ""),
    ]
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SnapshotFilterNode:
    """Drop raw items whose stable_id+hash already exist in a stored snapshot."""

    def __init__(
        self,
        store: Any,
        snapshot_by_source: dict[str, dict[str, str]],
    ) -> None:
        self._store = store
        self._snapshot_by_source = snapshot_by_source
        self._new_snapshots: dict[str, dict[str, str]] = {}

    async def process(self, item: RawItem) -> RawItem | None:
        source = str(item.source_name or item.source_kind or "unknown")
        item_id = str(item.stable_id or "")
        if not item_id:
            # Cannot snapshot items without a stable id; let them through.
            return item

        snapshot = self._snapshot_by_source.get(source, {})
        new_hash = _content_hash(item)
        previous_hash = snapshot.get(item_id)
        if previous_hash == new_hash:
            raise RawItemDropped(
                reason=TriageRejectionReason.ALREADY_SEEN,
                details="Item unchanged since previous run (snapshot match).",
                item=item,
                stage=self.__class__.__name__,
            )

        self._new_snapshots.setdefault(source, {})[item_id] = new_hash
        return item

    async def save(self) -> None:
        """Persist merged snapshots (previous + newly seen items)."""
        from job_ftch.application.tenant_runner import TenantStore

        if not isinstance(self._store, TenantStore):
            return

        for source, new_snapshot in self._new_snapshots.items():
            previous = await self._store.load_source_snapshot(source)
            merged = {**previous, **new_snapshot}
            await self._store.save_source_snapshot(source, merged)
            logger.info("snapshot_saved", source=source, items=len(merged))
