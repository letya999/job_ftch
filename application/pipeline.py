"""Async source -> nodes -> sink orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

    from application.contracts import Node, Sink, Source, Store

import structlog
from opentelemetry import trace

from application.rejections import RawItemRejected
from domain import QuarantinedRawItem, RawItemRejectionReason

PipelineItem = TypeVar("PipelineItem")


@dataclass(slots=True)
class RunSummary:
    fetched: int = 0
    dropped: int = 0
    emitted: int = 0
    quarantined: int = 0
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
        quarantine_sink: Sink[QuarantinedRawItem] | None = None,
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
        self._quarantine_sink = quarantine_sink
        self._logger = structlog.get_logger("job_ftch.pipeline")
        self._tracer = trace.get_tracer("job_ftch.pipeline")

    async def run(self, max_items: int | None = None) -> RunSummary:
        summary = RunSummary()
        with self._tracer.start_as_current_span("pipeline.run") as span:
            source_iter = self._source.fetch().__aiter__()
            while True:
                try:
                    item = await source_iter.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    summary.failed += 1
                    await self._emit_source_failure(exc, summary)
                    break
                if max_items is not None and summary.fetched >= max_items:
                    break
                summary.fetched += 1
                if await self._handle_pre_quarantined_item(item, summary):
                    continue
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
                        current: PipelineItem | None = cast("PipelineItem", item)
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
                except RawItemRejected as exc:
                    summary.dropped += 1
                    summary.quarantined += 1
                    self._logger.info(
                        "pipeline_item_quarantined",
                        item_id=item_id,
                        reason=exc.reason.value,
                        details=exc.details,
                    )
                    if self._quarantine_sink is not None:
                        await self._quarantine_sink.emit(exc.to_quarantined())
            summary.finish()
            span.set_attribute("job_ftch.fetched", summary.fetched)
            span.set_attribute("job_ftch.dropped", summary.dropped)
            span.set_attribute("job_ftch.emitted", summary.emitted)
            span.set_attribute("job_ftch.quarantined", summary.quarantined)
            span.set_attribute("job_ftch.failed", summary.failed)
        self._logger.info("pipeline_run_summary", **summary.as_dict())
        return summary

    async def _handle_pre_quarantined_item(
        self,
        item: PipelineItem | QuarantinedRawItem,
        summary: RunSummary,
    ) -> bool:
        if not isinstance(item, QuarantinedRawItem):
            return False
        summary.dropped += 1
        summary.quarantined += 1
        self._logger.info(
            "pipeline_source_item_quarantined",
            reason=item.reason.value,
            details=item.details,
        )
        if self._quarantine_sink is not None:
            await self._quarantine_sink.emit(item)
        return True

    async def _emit_source_failure(self, exc: Exception, summary: RunSummary) -> None:
        summary.quarantined += 1
        self._logger.exception("pipeline_source_failed")
        if self._quarantine_sink is None:
            return
        await self._quarantine_sink.emit(
            QuarantinedRawItem(
                reason=RawItemRejectionReason.SOURCE_FETCH_ERROR,
                details=str(exc) or exc.__class__.__name__,
                snapshot={
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            )
        )
