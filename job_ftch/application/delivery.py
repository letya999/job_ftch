"""Explicit durable delivery targets for the outbox boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.application.contracts import Sink
    from job_ftch.domain import JobRecord


class SinkDeliveryTarget:
    """Adapt one output sink as a stable, replayable delivery target."""

    def __init__(self, target_id: str, sink: Sink[JobRecord]) -> None:
        self._target_id = target_id
        self._sink = sink

    @property
    def target_id(self) -> str:
        return self._target_id

    async def deliver(self, item: JobRecord) -> None:
        await self._sink.emit(item)
