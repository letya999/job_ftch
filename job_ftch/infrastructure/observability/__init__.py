"""Observability infrastructure."""

from job_ftch.config import Settings
from job_ftch.infrastructure.observability.openobserve import configure_openobserve
from job_ftch.infrastructure.observability.otel_setup import configure_tracing, force_flush_tracing


def configure_observability(settings: Settings) -> None:
    """Configure operational telemetry and optional safe trace export."""
    configure_tracing(settings)
    configure_openobserve(settings)
    from job_ftch.infrastructure.observability import openobserve

    try:
        configured = openobserve._configure_safe_trace_export(settings)
        if settings.tracing_enabled and not configured:
            openobserve.logger.warning("Safe trace export unavailable")
    except Exception:
        openobserve.logger.warning("Safe trace export could not be configured")


__all__ = [
    "configure_observability",
    "configure_openobserve",
    "configure_tracing",
    "force_flush_tracing",
]
