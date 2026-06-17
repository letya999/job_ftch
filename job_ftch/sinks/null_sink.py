"""Null sink that discards all emitted items."""

from __future__ import annotations

from typing import Any

from job_ftch.application.registry import register_sink


class NullSink:
    async def emit(self, item: Any) -> None:
        pass

    async def flush(self) -> None:
        pass


@register_sink("none")
def _build_null_sink(settings: Any) -> NullSink:
    return NullSink()
