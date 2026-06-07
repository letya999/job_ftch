"""Async source -> nodes -> sink orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from application.contracts import (
        FlushableSink,
        ProcessingNode,
        SanitizingNode,
        Sink,
        Source,
        Store,
    )

import structlog
from opentelemetry import trace

from application.drops import RawItemDropped
from application.rejections import RawItemRejected
from domain import (
    Job,
    JobExtractionStatus,
    QuarantinedRawItem,
    RawItem,
    RawItemRejectionReason,
    RejectedItem,
    RejectedOutcome,
    processed_key_for_raw_item,
)

PipelineInput = TypeVar("PipelineInput")
PipelineOutput = TypeVar("PipelineOutput")


@dataclass(slots=True)
class StatsBase:
    fetched: int = 0
    sanitized: int = 0
    triaged: int = 0
    extracted: int = 0
    partial: int = 0
    review: int = 0
    duplicates: int = 0
    dropped: int = 0
    emitted: int = 0
    posted: int = 0
    rejected: int = 0
    quarantined: int = 0
    failed: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)
    quarantine_reasons: dict[str, int] = field(default_factory=dict)

    def record_drop(self, reason: str) -> None:
        self.dropped += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1

    def record_quarantine(self, reason: str) -> None:
        self.quarantined += 1
        self.quarantine_reasons[reason] = self.quarantine_reasons.get(reason, 0) + 1

    def record_rejected(self) -> None:
        self.rejected += 1

    def record_duplicate(self) -> None:
        self.duplicates += 1

    def record_failure(self) -> None:
        self.failed += 1


@dataclass(slots=True)
class SourceRunStats(StatsBase):
    pass


@dataclass(slots=True)
class RunSummary(StatsBase):
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


class Pipeline[PipelineInput, PipelineOutput]:
    def __init__(
        self,
        source: Source[PipelineInput],
        sanitize_node: SanitizingNode[PipelineInput],
        nodes: Sequence[ProcessingNode[Any]],
        sink: Sink[PipelineOutput] | Sequence[Sink[PipelineOutput]],
        store: Store,
        quarantine_sink: Sink[QuarantinedRawItem] | None = None,
        rejected_sink: Sink[RejectedItem] | None = None,
    ) -> None:
        self._source = source
        self._sanitize_node = sanitize_node
        self._nodes = list(nodes)
        self._sink = self._coerce_sink(sink)
        self._store = store
        self._quarantine_sink = quarantine_sink
        self._rejected_sink = rejected_sink
        self._logger = structlog.get_logger("job_ftch.pipeline")
        self._tracer = trace.get_tracer("job_ftch.pipeline")

    async def run(self, max_items: int | None = None) -> RunSummary:
        summary = RunSummary()
        await self._set_run_state("pipeline.started_at", summary.started_at.isoformat())
        await self._set_run_state("pipeline.status", "running")
        run_interrupted = False
        with self._tracer.start_as_current_span("pipeline.run") as span:
            source_iter = self._source.fetch().__aiter__()
            while True:
                try:
                    item = await source_iter.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    summary.record_failure()
                    await self._emit_source_failure(exc, summary)
                    run_interrupted = True
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
                failure_item: object = item
                failure_reason = "item_processing_failed"
                failure_stage = self._sanitize_node.__class__.__name__
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
                        failure_item = item
                        sanitized = await self._sanitize_node.process(cast("PipelineInput", item))
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
                        current: Any = sanitized
                        failure_item = current
                        for node in self._nodes:
                            if current is None:
                                break
                            failure_stage = node.__class__.__name__
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
                            failure_item = current
                        if current is None:
                            continue
                        summary.triaged += 1
                        summary.source_stats(source_kind).triaged += 1
                        self._record_output_stats(current, summary, source_kind)
                        failure_reason = "sink_emit_failed"
                        failure_stage = self._sink.__class__.__name__
                        failure_item = current
                        await self._sink.emit(cast("PipelineOutput", current))
                        summary.emitted += 1
                        summary.source_stats(source_kind).emitted += 1
                        item_span.set_attribute("job_ftch.result", "emitted")
                        finalized = True
                except RawItemDropped as exc:
                    summary.record_source_drop(source_kind, exc.reason.value)
                    summary.record_rejected()
                    summary.source_stats(source_kind).record_rejected()
                    if exc.reason.value.startswith("duplicate_"):
                        summary.record_duplicate()
                        summary.source_stats(source_kind).record_duplicate()
                    self._logger.info(
                        "pipeline_item_dropped",
                        item_id=item_id,
                        reason=exc.reason.value,
                        details=exc.details,
                    )
                    await self._emit_rejected_item(
                        item=exc.item,
                        outcome=RejectedOutcome.DROPPED,
                        reason=exc.reason.value,
                        details=exc.details,
                        stage=exc.stage,
                    )
                    finalized = True
                except RawItemRejected as exc:
                    summary.record_source_drop(source_kind, exc.reason.value)
                    summary.record_source_quarantine(source_kind, exc.reason.value)
                    summary.record_rejected()
                    summary.source_stats(source_kind).record_rejected()
                    self._logger.info(
                        "pipeline_item_quarantined",
                        item_id=item_id,
                        reason=exc.reason.value,
                        details=exc.details,
                    )
                    await self._emit_rejected_item(
                        item=exc.item,
                        outcome=RejectedOutcome.QUARANTINED,
                        reason=exc.reason.value,
                        details=exc.details,
                        stage="SanitizeNode",
                    )
                    if self._quarantine_sink is not None:
                        await self._quarantine_sink.emit(exc.to_quarantined())
                    finalized = True
                except Exception as exc:
                    summary.record_failure()
                    summary.record_rejected()
                    summary.source_stats(source_kind).record_failure()
                    summary.source_stats(source_kind).record_rejected()
                    self._logger.exception(
                        "pipeline_item_failed",
                        item_id=item_id,
                        reason=failure_reason,
                        stage=failure_stage,
                    )
                    await self._emit_rejected_item(
                        item=failure_item,
                        outcome=RejectedOutcome.FAILED,
                        reason=failure_reason,
                        details=str(exc) or exc.__class__.__name__,
                        stage=failure_stage,
                    )
                    finalized = failure_reason != "sink_emit_failed"
                finally:
                    if finalized and processed_key is not None:
                        await self._store.mark_processed(processed_key)
                        await self._set_run_state("pipeline.last_processed_key", processed_key)
            summary.finish()
            try:
                await self._flush_if_supported(self._sink)
            except Exception:
                summary.record_failure()
                run_interrupted = True
                self._logger.exception(
                    "pipeline_sink_flush_failed",
                    sink=self._sink.__class__.__name__,
                )
            if self._quarantine_sink is not None:
                await self._flush_if_supported(self._quarantine_sink)
            if self._rejected_sink is not None:
                await self._flush_if_supported(self._rejected_sink)
            span.set_attribute("job_ftch.fetched", summary.fetched)
            span.set_attribute("job_ftch.sanitized", summary.sanitized)
            span.set_attribute("job_ftch.triaged", summary.triaged)
            span.set_attribute("job_ftch.extracted", summary.extracted)
            span.set_attribute("job_ftch.partial", summary.partial)
            span.set_attribute("job_ftch.review", summary.review)
            span.set_attribute("job_ftch.duplicates", summary.duplicates)
            span.set_attribute("job_ftch.dropped", summary.dropped)
            span.set_attribute("job_ftch.emitted", summary.emitted)
            span.set_attribute("job_ftch.rejected", summary.rejected)
            span.set_attribute("job_ftch.quarantined", summary.quarantined)
            span.set_attribute("job_ftch.failed", summary.failed)
        finished_at = summary.finished_at or datetime.now(UTC)
        await self._set_run_state("pipeline.finished_at", finished_at.isoformat())
        if run_interrupted:
            await self._set_run_state("pipeline.status", "failed")
        elif summary.failed > 0:
            await self._set_run_state("pipeline.status", "completed_with_failures")
        else:
            await self._set_run_state("pipeline.status", "completed")
        self._logger.info("pipeline_run_summary", **summary.as_dict())
        return summary

    async def _handle_pre_quarantined_item(
        self,
        item: PipelineInput | QuarantinedRawItem,
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
        summary.record_rejected()
        summary.source_stats(source_kind).record_rejected()
        await self._emit_rejected_item(
            item=item,
            outcome=RejectedOutcome.QUARANTINED,
            reason=item.reason.value,
            details=item.details,
            stage="source",
        )
        if self._quarantine_sink is not None:
            await self._quarantine_sink.emit(item)
        return True

    async def _emit_source_failure(self, exc: Exception, summary: RunSummary) -> None:
        summary.record_source_quarantine("unknown", RawItemRejectionReason.SOURCE_FETCH_ERROR.value)
        summary.record_rejected()
        summary.source_stats("unknown").record_failure()
        summary.source_stats("unknown").record_rejected()
        self._logger.exception("pipeline_source_failed")
        await self._emit_rejected_item(
            item=QuarantinedRawItem(
                reason=RawItemRejectionReason.SOURCE_FETCH_ERROR,
                details=str(exc) or exc.__class__.__name__,
                snapshot={
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            ),
            outcome=RejectedOutcome.FAILED,
            reason=RawItemRejectionReason.SOURCE_FETCH_ERROR.value,
            details=str(exc) or exc.__class__.__name__,
            stage="source",
        )
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

    def _record_output_stats(
        self,
        item: object,
        summary: RunSummary,
        source_kind: object | None,
    ) -> None:
        if not isinstance(item, Job):
            return
        summary.extracted += 1
        summary.source_stats(source_kind).extracted += 1
        if item.extraction_status is JobExtractionStatus.PARTIAL:
            summary.partial += 1
            summary.source_stats(source_kind).partial += 1
        if item.review_reasons:
            summary.review += 1
            summary.source_stats(source_kind).review += 1

    async def _emit_rejected_item(
        self,
        *,
        item: object,
        outcome: RejectedOutcome,
        reason: str,
        details: str,
        stage: str | None,
    ) -> None:
        if self._rejected_sink is None:
            return
        payload = (
            item.model_dump(mode="json") if hasattr(item, "model_dump") else {"item": str(item)}
        )
        rejected = RejectedItem(
            outcome=outcome,
            reason=reason,
            details=details,
            stage=stage,
            item_type=item.__class__.__name__,
            source_kind=self._optional_str(getattr(item, "source_kind", None)),
            source_name=self._optional_str(getattr(item, "source_name", None)),
            stable_id=self._optional_str(getattr(item, "stable_id", None)),
            raw_item_id=self._optional_str(getattr(item, "raw_item_id", None)),
            snapshot=cast("dict[str, object]", payload),
        )
        await self._rejected_sink.emit(rejected)

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _coerce_sink(
        self,
        sink: Sink[PipelineOutput] | Sequence[Sink[PipelineOutput]],
    ) -> Sink[PipelineOutput]:
        from sinks.fanout import FanOutSink

        if isinstance(sink, Sequence):
            return FanOutSink(sink)
        return sink

    async def _flush_if_supported(self, sink: object) -> None:
        flushable = cast("FlushableSink[object] | None", sink if hasattr(sink, "flush") else None)
        if flushable is not None:
            await flushable.flush()

    async def _set_run_state(self, key: str, value: str) -> None:
        await self._store.set_run_state(key, value)
