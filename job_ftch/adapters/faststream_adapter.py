"""FastStream adapter registration helpers."""
# mypy: disable-error-code="misc,untyped-decorator"

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from job_ftch.application.builder import PipelineBuilder
from job_ftch.domain.source_spec import SourceSpec


def register_faststream_handlers(
    broker: Any,
    *,
    subject: str,
    publish_subject: str,
    builder: PipelineBuilder,
) -> Any:
    """Register a subscriber/publisher pair on a FastStream broker."""
    source_adapter: TypeAdapter[SourceSpec] = TypeAdapter(SourceSpec)

    @broker.subscriber(subject)
    @broker.publisher(publish_subject)
    async def handle_trigger(message: dict[str, Any]) -> list[dict[str, Any]]:
        spec = source_adapter.validate_python(message["source_spec"])
        cloned = builder.clone().sources([spec])
        summary = await cloned.run_async()
        return [{"summary": summary.as_dict()}]

    return handle_trigger
