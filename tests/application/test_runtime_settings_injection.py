from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from job_ftch.application.builder import PipelineBuilder
from job_ftch.config import get_settings
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.sinks.null_sink import NullSink

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain import RawItem


class _EmptySource:
    def fetch(self) -> AsyncIterator[RawItem]:
        async def items() -> AsyncIterator[RawItem]:
            if False:
                yield cast("Any", None)

        return items()


def test_pipeline_builder_clone_preserves_settings_snapshot() -> None:
    settings = get_settings().model_copy(update={"pipeline_decision_version": "snapshot-v1"})
    builder = PipelineBuilder(settings=settings)
    builder.store(InMemoryStore())
    builder.with_runtime_source(_EmptySource())
    builder.stage(SanitizeNode())
    builder.sink(NullSink())

    cloned = builder.clone()
    pipeline = cloned.build()

    assert pipeline._settings is settings  # type: ignore[attr-defined]
