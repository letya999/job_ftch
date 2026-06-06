from __future__ import annotations

from typing import TYPE_CHECKING, Any

from application.outcomes import NodeOutcome, OutcomeKind, PipelineStage, RejectReason
from domain import QuarantinedRawItem, RawItemRejectionReason

if TYPE_CHECKING:
    from application.contracts import Sink
    from application.rejections import RawItemRejected
    from application.run_summary import RunSummary


class PipelineHandlers:
    _sink: Sink[Any]
    _quarantine_sink: Sink[QuarantinedRawItem] | None
    _logger: Any

    async def _handle_outcome(
        self,
        *,
        outcome: NodeOutcome[Any],
        original_item: object,
        summary: RunSummary,
        stage: PipelineStage,
    ) -> bool:
        if outcome.kind is OutcomeKind.PASS:
            return False

        reason = outcome.reason or RejectReason.NODE_DROPPED
        summary.record_reason(reason)
        if outcome.kind is OutcomeKind.DROP:
            summary.dropped += 1
            self._logger.info(
                "pipeline_item_dropped",
                stage=stage.value,
                reason=reason.value,
                message=outcome.message,
            )
            return True

        if outcome.kind is OutcomeKind.QUARANTINE:
            summary.dropped += 1
            summary.quarantined += 1
            await self._emit_quarantine(
                item=outcome.item or original_item,
                reason=reason,
                details=outcome.message or reason.value,
                snapshot=outcome.metadata,
            )
            return True

        summary.failed += 1
        await self._emit_quarantine(
            item=outcome.item or original_item,
            reason=reason,
            details=outcome.message or reason.value,
            snapshot=outcome.metadata,
        )
        return True

    async def _handle_pre_quarantined_item(
        self,
        item: object,
        summary: RunSummary,
    ) -> bool:
        if not isinstance(item, QuarantinedRawItem):
            return False
        summary.dropped += 1
        summary.quarantined += 1
        summary.record_reason(RejectReason.from_value(item.reason.value))
        self._logger.info(
            "pipeline_source_item_quarantined",
            reason=item.reason.value,
            details=item.details,
        )
        if self._quarantine_sink is not None:
            await self._quarantine_sink.emit(item)
        return True

    async def _handle_raw_item_rejection(
        self,
        exc: RawItemRejected,
        summary: RunSummary,
        stage: PipelineStage,
    ) -> None:
        summary.dropped += 1
        summary.quarantined += 1
        summary.record_stage(stage)
        summary.record_reason(RejectReason.from_value(exc.reason.value))
        self._logger.info(
            "pipeline_item_quarantined",
            item_id=getattr(exc.item, "stable_id", None),
            reason=exc.reason.value,
            details=exc.details,
        )
        if self._quarantine_sink is not None:
            await self._quarantine_sink.emit(exc.to_quarantined())

    async def _handle_node_exception(
        self,
        exc: Exception,
        item: object,
        summary: RunSummary,
        stage: PipelineStage,
    ) -> None:
        summary.failed += 1
        summary.record_stage(stage)
        summary.record_reason(RejectReason.NODE_FAILED)
        self._logger.exception("pipeline_node_failed", stage=stage.value)
        await self._emit_quarantine(
            item=item,
            reason=RejectReason.NODE_FAILED,
            details=str(exc) or exc.__class__.__name__,
            snapshot={
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
                "stage": stage.value,
            },
        )

    async def _handle_sink_exception(
        self,
        exc: Exception,
        item: object,
        summary: RunSummary,
    ) -> None:
        summary.failed += 1
        summary.record_stage(PipelineStage.EMIT)
        summary.record_reason(RejectReason.SINK_EMIT_ERROR)
        self._logger.exception("pipeline_sink_failed")
        await self._emit_quarantine(
            item=item,
            reason=RejectReason.SINK_EMIT_ERROR,
            details=str(exc) or exc.__class__.__name__,
            snapshot={
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            },
        )

    async def _emit_source_failure(self, exc: Exception, summary: RunSummary) -> None:
        summary.quarantined += 1
        summary.record_stage(PipelineStage.SOURCE)
        summary.record_reason(RejectReason.SOURCE_FETCH_ERROR)
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

    async def _emit_quarantine(
        self,
        *,
        item: object,
        reason: RejectReason,
        details: str,
        snapshot: dict[str, object] | None = None,
    ) -> None:
        if self._quarantine_sink is None:
            return
        await self._quarantine_sink.emit(
            QuarantinedRawItem(
                reason=self._to_raw_item_rejection_reason(reason),
                details=details,
                source_kind=str(getattr(item, "source_kind", "")) or None,
                source_name=getattr(item, "source_name", None),
                external_id=getattr(item, "external_id", None),
                url=getattr(item, "url", None),
                snapshot=snapshot or self._snapshot_item(item),
            )
        )

    async def _finalize_sinks(self, summary: RunSummary) -> None:
        for sink in (self._sink, self._quarantine_sink):
            if sink is None:
                continue
            try:
                await sink.finalize()
            except Exception as exc:
                summary.failed += 1
                summary.record_stage(PipelineStage.EMIT)
                summary.record_reason(RejectReason.SINK_FINALIZE_ERROR)
                self._logger.exception("pipeline_sink_finalize_failed")
                if sink is not self._quarantine_sink:
                    await self._emit_quarantine(
                        item={},
                        reason=RejectReason.SINK_FINALIZE_ERROR,
                        details=str(exc) or exc.__class__.__name__,
                        snapshot={
                            "error_type": exc.__class__.__name__,
                            "error_message": str(exc),
                        },
                    )

    def _source_key(self, item: object) -> str | None:
        source_kind = getattr(item, "source_kind", None)
        source_name = getattr(item, "source_name", None)
        if source_kind is None and source_name is None:
            return None
        return f"{source_kind or 'unknown'}:{source_name or 'unknown'}"

    def _snapshot_item(self, item: object) -> dict[str, object]:
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json", warnings=False)
            return dict(payload) if isinstance(payload, dict) else {"payload": payload}
        if isinstance(item, dict):
            return dict(item)
        return {"repr": repr(item)}

    def _to_raw_item_rejection_reason(self, reason: RejectReason) -> RawItemRejectionReason:
        try:
            return RawItemRejectionReason(reason.value)
        except ValueError:
            return RawItemRejectionReason.INVALID_RAW_ITEM
