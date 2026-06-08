"""Best-effort wrapper for secondary sinks."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

import structlog

if TYPE_CHECKING:
    from job_ftch.application.contracts import Sink

ItemT = TypeVar("ItemT")


class FailureTolerantSink[ItemT]:
    def __init__(self, sink: Sink[ItemT], *, sink_name: str) -> None:
        self._sink = sink
        self._sink_name = sink_name
        self._logger = structlog.get_logger("job_ftch.sinks.failure_tolerant")
        self.emit_failures = 0
        self.flush_failures = 0

    async def emit(self, item: ItemT) -> None:
        try:
            await self._sink.emit(item)
        except Exception:
            self.emit_failures += 1
            self._logger.exception("secondary_sink_emit_failed", sink=self._sink_name)

    async def flush(self) -> None:
        if not hasattr(self._sink, "flush"):
            return
        try:
            await self._sink.flush()
        except Exception:
            self.flush_failures += 1
            self._logger.exception("secondary_sink_flush_failed", sink=self._sink_name)
