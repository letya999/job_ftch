"""Raw item validation node for practical ingestion guards."""

from __future__ import annotations

from typing import TYPE_CHECKING

from application.outcomes import NodeOutcome, PipelineStage, RejectReason

if TYPE_CHECKING:
    from application.context import ProcessingContext
    from domain import RawItem


class ValidateRawNode:
    name = "validate_raw"
    stage = PipelineStage.RAW_VALIDATE
    is_sanitize = False

    async def process(self, item: RawItem, context: ProcessingContext) -> NodeOutcome[RawItem]:
        source_name = str(item.source_name).strip()
        text = str(item.text).strip()
        external_id = str(item.external_id).strip() if item.external_id is not None else None

        if not source_name:
            return NodeOutcome.quarantine(
                item=item,
                reason=RejectReason.EMPTY_SOURCE_NAME,
                message="Raw item source_name is empty.",
                metadata=self._snapshot(item),
            )
        if not text:
            return NodeOutcome.quarantine(
                item=item,
                reason=RejectReason.EMPTY_TEXT,
                message="Raw item text is empty.",
                metadata=self._snapshot(item),
            )
        if len(text) > context.max_text_length:
            return NodeOutcome.quarantine(
                item=item,
                reason=RejectReason.TEXT_TOO_LONG,
                message=(
                    f"Raw item text length {len(text)} exceeds "
                    f"max_text_length {context.max_text_length}."
                ),
                metadata={
                    **self._snapshot(item),
                    "text_length": len(text),
                    "max_text_length": context.max_text_length,
                },
            )
        if not external_id and item.url is None:
            return NodeOutcome.quarantine(
                item=item,
                reason=RejectReason.MISSING_LOCATOR,
                message="Raw item must have external_id or url.",
                metadata=self._snapshot(item),
            )
        return NodeOutcome.pass_(item)

    def _snapshot(self, item: RawItem) -> dict[str, object]:
        return item.model_dump(mode="json", warnings=False)
