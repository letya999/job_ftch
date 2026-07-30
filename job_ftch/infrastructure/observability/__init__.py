"""Observability infrastructure."""

from job_ftch.config import Settings
from job_ftch.infrastructure.observability.openobserve import configure_openobserve
from job_ftch.infrastructure.observability.otel_setup import configure_tracing, force_flush_tracing


def configure_observability(settings: Settings) -> None:
    """Configure isolated destinations: Langfuse traces and OpenObserve ops."""
    configure_tracing(settings)
    configure_openobserve(settings)


__all__ = [
    "configure_observability",
    "configure_openobserve",
    "configure_tracing",
    "force_flush_tracing",
]
