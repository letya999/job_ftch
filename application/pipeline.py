"""Async source -> nodes -> sink orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from application.contracts import Node, Sink, Source, Store

import structlog
from opentelemetry import trace

PipelineItem = TypeVar("PipelineItem")


@dataclass(slots=True)
class RunSummary:
    fetched: int = 0
    dropped: int = 0
    emitted: int = 0
    failed: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def finish(self) -> RunSummary:
        self.finished_at = datetime.now(UTC)
        return self

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class Pipeline[PipelineItem]:
    def __init__(
        self,
        source: Source[PipelineItem],
        nodes: Sequence[Node[PipelineItem]],
        sink: Sink[PipelineItem],
        store: Store,
    ) -> None:
        if not nodes:
            msg = "Pipeline requires at least one node."
            raise ValueError(msg)
        if not nodes[0].is_sanitize:
            msg = "SanitizeNode must be the first node in the pipeline chain."
            raise ValueError(msg)
        self._source = source
        self._nodes = list(nodes)
        self._sink = sink
        self._store = store
        self._logger = structlog.get_logger("job_ftch.pipeline")
        self._tracer = trace.get_tracer("job_ftch.pipeline")

    async def run(self, max_items: int | None = None) -> RunSummary:
        summary = RunSummary()
        with self._tracer.start_as_current_span("pipeline.run") as span:
            async for item in self._source.fetch():
                if max_items is not None and summary.fetched >= max_items:
                    break
                summary.fetched += 1
                item_id = getattr(item, "stable_id", None)
                try:
                    with self._tracer.start_as_current_span("pipeline.item") as item_span:
                        item_span.set_attribute("job_ftch.item_id", str(item_id or ""))
                        if item_id is not None and await self._store.has_processed(item_id):
                            summary.dropped += 1
                            item_span.set_attribute("job_ftch.result", "already_processed")
                            self._logger.info(
                                "pipeline_item_skipped",
                                item_id=item_id,
                                reason="already_processed",
                            )
                            continue
                        current: PipelineItem | None = item
                        for node in self._nodes:
                            if current is None:
                                break
                            next_item = await node.process(current)
                            if next_item is None:
                                current = None
                                summary.dropped += 1
                                item_span.set_attribute("job_ftch.result", "dropped")
                                self._logger.info(
                                    "pipeline_item_dropped",
                                    item_id=item_id,
                                    reason="node_returned_none",
                                )
                                break
                            current = next_item
                        if current is None:
                            continue
                        await self._sink.emit(current)
                        summary.emitted += 1
                        item_span.set_attribute("job_ftch.result", "emitted")
                        if item_id is not None:
                            await self._store.mark_processed(item_id)
                except Exception:
                    summary.failed += 1
                    self._logger.exception("pipeline_item_failed", item_id=item_id)
            summary.finish()
            span.set_attribute("job_ftch.fetched", summary.fetched)
            span.set_attribute("job_ftch.dropped", summary.dropped)
            span.set_attribute("job_ftch.emitted", summary.emitted)
            span.set_attribute("job_ftch.failed", summary.failed)
        self._logger.info("pipeline_run_summary", **summary.as_dict())
        return summary
