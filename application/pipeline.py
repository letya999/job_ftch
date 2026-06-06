"""Async source -> nodes -> sink orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

    from application.contracts import ProcessingNode, SanitizingNode, Sink, Source, Store

import structlog
from opentelemetry import trace

from application.drops import RawItemDropped
from application.rejections import RawItemRejected
from domain import QuarantinedRawItem, RawItem, RawItemRejectionReason, processed_key_for_raw_item

PipelineItem = TypeVar("PipelineItem")


@dataclass(slots=True)
class StatsBase:
    fetched: int = 0
    sanitized: int = 0
    triaged: int = 0
    dropped: int = 0
    emitted: int = 0
    quarantined: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)
    quarantine_reasons: dict[str, int] = field(default_factory=dict)

    def record_drop(self, reason: str) -> None:
        self.dropped += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1

    def record_quarantine(self, reason: str) -> None:
        self.quarantined += 1
        self.quarantine_reasons[reason] = self.quarantine_reasons.get(reason, 0) + 1


@dataclass(slots=True)
class SourceRunStats(StatsBase):
    pass


@dataclass(slots=True)
class RunSummary(StatsBase):
    failed: int = 0
    by_source_kind: dict[str, SourceRunStats] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def finish(self) -> RunSummary:
        self.finished_at = datetime.now(UTC)
        return self

    def source_stats(self, source_kind: object | None) -> SourceRunStats:
        key = str(source_kind or "unknown")
        stats = self.by_source_kind.get(key)
        if stats is None:
            stats = SourceRunStats()
            self.by_source_kind[key] = stats
        return stats

    def record_source_drop(self, source_kind: object | None, reason: str) -> None:
        self.record_drop(reason)
        self.source_stats(source_kind).record_drop(reason)

    def record_source_quarantine(self, source_kind: object | None, reason: str) -> None:
        self.record_quarantine(reason)
        self.source_stats(source_kind).record_quarantine(reason)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class Pipeline[PipelineItem]:
    def __init__(
        self,
        source: Source[PipelineItem],
        sanitize_node: SanitizingNode[PipelineItem],
        nodes: Sequence[ProcessingNode[PipelineItem]],
        sink: Sink[PipelineItem],
        store: Store,
        quarantine_sink: Sink[QuarantinedRawItem] | None = None,
    ) -> None:
        self._source = source
        self._sanitize_node = sanitize_node
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
                source_kind = getattr(item, "source_kind", None)
                summary.source_stats(source_kind).fetched += 1
                if await self._handle_pre_quarantined_item(item, summary):
                    continue
                item_id = getattr(item, "stable_id", None)
                processed_key = (
                    processed_key_for_raw_item(item) if isinstance(item, RawItem) else item_id
                )
                finalized = False
                try:
                    with self._tracer.start_as_current_span("pipeline.item") as item_span:
                        item_span.set_attribute("job_ftch.item_id", str(item_id or ""))
                        if processed_key is not None and await self._store.has_processed(
                            processed_key
                        ):
                            summary.record_source_drop(source_kind, "already_processed")
                            item_span.set_attribute("job_ftch.result", "already_processed")
                            self._logger.info(
                                "pipeline_item_skipped",
                                item_id=processed_key,
                                reason="already_processed",
                            )
                            continue
                        sanitized = await self._sanitize_node.process(cast("PipelineItem", item))
                        if sanitized is None:
                            summary.record_source_drop(source_kind, "node_returned_none")
                            item_span.set_attribute("job_ftch.result", "dropped")
                            self._logger.info(
                                "pipeline_item_dropped",
                                item_id=item_id,
                                reason="node_returned_none",
                            )
                            finalized = True
                            continue
                        summary.sanitized += 1
                        summary.source_stats(source_kind).sanitized += 1
                        current: PipelineItem | None = sanitized
                        for node in self._nodes:
                            if current is None:
                                break
                            next_item = await node.process(current)
                            if next_item is None:
                                current = None
                                summary.record_source_drop(source_kind, "node_returned_none")
                                item_span.set_attribute("job_ftch.result", "dropped")
                                self._logger.info(
                                    "pipeline_item_dropped",
                                    item_id=item_id,
                                    reason="node_returned_none",
                                )
                                finalized = True
                                break
                            current = next_item
                        if current is None:
                            continue
                        summary.triaged += 1
                        summary.source_stats(source_kind).triaged += 1
                        await self._sink.emit(current)
                        summary.emitted += 1
                        summary.source_stats(source_kind).emitted += 1
                        item_span.set_attribute("job_ftch.result", "emitted")
                        finalized = True
                except RawItemDropped as exc:
                    summary.record_source_drop(source_kind, exc.reason.value)
                    self._logger.info(
                        "pipeline_item_dropped",
                        item_id=item_id,
                        reason=exc.reason.value,
                        details=exc.details,
                    )
                    finalized = True
                except RawItemRejected as exc:
                    summary.record_source_drop(source_kind, exc.reason.value)
                    summary.record_source_quarantine(source_kind, exc.reason.value)
                    self._logger.info(
                        "pipeline_item_quarantined",
                        item_id=item_id,
                        reason=exc.reason.value,
                        details=exc.details,
                    )
                    if self._quarantine_sink is not None:
                        await self._quarantine_sink.emit(exc.to_quarantined())
                    finalized = True
                finally:
                    if finalized and processed_key is not None:
                        await self._store.mark_processed(processed_key)
            summary.finish()
            span.set_attribute("job_ftch.fetched", summary.fetched)
            span.set_attribute("job_ftch.sanitized", summary.sanitized)
            span.set_attribute("job_ftch.triaged", summary.triaged)
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
        source_kind = item.source_kind
        summary.record_source_drop(source_kind, item.reason.value)
        summary.record_source_quarantine(source_kind, item.reason.value)
        self._logger.info(
            "pipeline_source_item_quarantined",
            reason=item.reason.value,
            details=item.details,
        )
        if self._quarantine_sink is not None:
            await self._quarantine_sink.emit(item)
        return True

    async def _emit_source_failure(self, exc: Exception, summary: RunSummary) -> None:
        summary.record_source_quarantine("unknown", RawItemRejectionReason.SOURCE_FETCH_ERROR.value)
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
