"""Compatibility no-ops after removing the external trace exporter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.application.pipeline import RunSummary
    from job_ftch.config import Settings


def configure_tracing(settings: Settings) -> None:
    """Keep the old composition hook without creating any exporter."""
    del settings


def force_flush_tracing() -> None:
    return


def record_final_run_trace(summary: RunSummary) -> None:
    del summary
