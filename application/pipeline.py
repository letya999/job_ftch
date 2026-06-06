from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, cast

import structlog
from opentelemetry import trace

from application.context import ProcessingContext
from application.outcomes import NodeOutcome, OutcomeKind, PipelineStage, RejectReason
from application.pipeline_handlers import PipelineHandlers
from application.rejections import RawItemRejected
from application.run_summary import RunSummary

if TYPE_CHECKING:
    from collections.abc import Sequence

    from application.contracts import Node, Sink, Source, Store
    from domain import QuarantinedRawItem

PipelineItem = TypeVar("PipelineItem")


class Pipeline[PipelineItem](PipelineHandlers):
    def __init__(
        self,
        source: Source[PipelineItem],
        nodes: Sequence[Node[Any, Any]],
        sink: Sink[Any],
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

    async def run(
        self,
        max_items: int | None = None,
        context: ProcessingContext | None = None,
    ) -> RunSummary:
        context = context or ProcessingContext()
        summary = RunSummary(run_id=context.run_id, started_at=context.started_at)
        with self._tracer.start_as_current_span("pipeline.run") as span:
            try:
                await self._run_items(context=context, summary=summary, max_items=max_items)
            finally:
                await self._finalize_sinks(summary)
                summary.finish()
                span.set_attribute("job_ftch.fetched", summary.fetched)
                span.set_attribute("job_ftch.dropped", summary.dropped)
                span.set_attribute("job_ftch.emitted", summary.emitted)
                span.set_attribute("job_ftch.quarantined", summary.quarantined)
                span.set_attribute("job_ftch.failed", summary.failed)
        self._logger.info("pipeline_run_summary", **summary.as_dict())
        return summary

    async def _run_items(
        self,
        *,
        context: ProcessingContext,
        summary: RunSummary,
        max_items: int | None,
    ) -> None:
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
            summary.source_records += 1
            summary.record_stage(PipelineStage.SOURCE)
            source_key = self._source_key(item)
            if source_key is not None:
                summary.record_source(source_key)

            if await self._handle_pre_quarantined_item(item, summary):
                continue
            await self._process_item(cast("PipelineItem", item), context, summary)

    async def _process_item(
        self,
        item: PipelineItem,
        context: ProcessingContext,
        summary: RunSummary,
    ) -> None:
        item_id = getattr(item, "stable_id", None)
        with self._tracer.start_as_current_span("pipeline.item") as item_span:
            item_span.set_attribute("job_ftch.item_id", str(item_id or ""))
            current: object | None = item
            for node in self._nodes:
                if current is None:
                    return
                try:
                    outcome = await self._run_node(node, current, context)
                except RawItemRejected as exc:
                    await self._handle_raw_item_rejection(exc, summary, node.stage)
                    item_span.set_attribute("job_ftch.result", "quarantined")
                    return
                except Exception as exc:
                    await self._handle_node_exception(exc, current, summary, node.stage)
                    item_span.set_attribute("job_ftch.result", "failed")
                    return

                summary.record_stage(node.stage)
                if node.stage is PipelineStage.SANITIZE and outcome.kind is OutcomeKind.PASS:
                    summary.sanitized += 1
                handled = await self._handle_outcome(
                    outcome=outcome,
                    original_item=item,
                    summary=summary,
                    stage=node.stage,
                )
                if handled:
                    item_span.set_attribute("job_ftch.result", outcome.kind.value)
                    return
                current = outcome.item

            if current is None:
                return
            current_id = getattr(current, "stable_id", None)
            item_span.set_attribute("job_ftch.item_id", str(current_id or item_id or ""))
            if current_id is not None and await self._store.has_processed(current_id):
                summary.dropped += 1
                summary.duplicates += 1
                summary.record_reason(RejectReason.ALREADY_PROCESSED)
                item_span.set_attribute("job_ftch.result", "already_processed")
                self._logger.info(
                    "pipeline_item_skipped",
                    item_id=current_id,
                    reason=RejectReason.ALREADY_PROCESSED.value,
                )
                return
            try:
                await self._sink.emit(current)
            except Exception as exc:
                await self._handle_sink_exception(exc, current, summary)
                item_span.set_attribute("job_ftch.result", "sink_failed")
                return
            summary.emitted += 1
            summary.record_stage(PipelineStage.EMIT)
            item_span.set_attribute("job_ftch.result", "emitted")
            if current_id is not None:
                await self._store.mark_processed(current_id)

    async def _run_node(
        self,
        node: Node[Any, Any],
        item: object,
        context: ProcessingContext,
    ) -> NodeOutcome[Any]:
        result = await node.process(item, context)
        if isinstance(result, NodeOutcome):
            return result
        if result is None:
            return NodeOutcome.drop(reason=RejectReason.NODE_DROPPED)
        return NodeOutcome.pass_(result)
