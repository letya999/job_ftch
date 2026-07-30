"""OpenTelemetry setup for Langfuse."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from opentelemetry import trace

if TYPE_CHECKING:
    from job_ftch.application.pipeline import RunSummary
    from job_ftch.config import Settings


# Set of TracerProvider object ids that this process has already configured.
# Replaces the previous `hasattr(provider, "_is_job_ftch_configured")` duck
# typing, which silently accepted any object that happened to have the
# marker (a test mock, a class with a class-level attribute, etc.).
_CONFIGURED_PROVIDER_IDS: set[int] = set()

logger = logging.getLogger(__name__)


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("CONTAINER", "").lower() in {
        "docker",
        "podman",
    }


def _resolve_langfuse_host(langfuse_host: str) -> str:
    host = langfuse_host.strip().rstrip("/")
    parsed = urlparse(host if "://" in host else f"http://{host}")
    if not parsed.scheme or not parsed.netloc:
        return host

    if _running_in_container() and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",  # nosec B104 - hostname comparison, not a listening socket
    }:
        fallback = os.environ.get(
            "JOB_FTCH_LANGFUSE_CONTAINER_HOST", "http://host.docker.internal:3001"
        )
        fallback = fallback.strip().rstrip("/")
        fallback_parsed = urlparse(fallback if "://" in fallback else f"http://{fallback}")
        if fallback_parsed.scheme and fallback_parsed.netloc:
            logger.warning(
                "langfuse_host_rewritten_for_container original=%s resolved=%s",
                host,
                fallback_parsed.geturl(),
            )
            return fallback_parsed.geturl().rstrip("/")

    return parsed._replace(path="", params="", query="", fragment="").geturl().rstrip("/")


def configure_tracing(settings: Settings) -> None:
    """Configure OTel tracing with Langfuse as exporter."""
    if not settings.tracing_enabled:
        return

    if not (
        settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_host
    ):
        logger.warning(
            "Tracing enabled but Langfuse keys or host missing. Tracing will not be configured."
        )
        return

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.error(
            "Tracing enabled but opentelemetry-exporter-otlp-proto-http is not installed. "
            "Install it with 'pip install job-ftch[tracing]'."
        )
        return

    # Check if already configured. We use a process-level set of provider
    # object ids; this is more robust than `hasattr` duck-typing which silently
    # treats any object with the attribute as configured.
    current_provider = trace.get_tracer_provider()
    current_provider_id = id(current_provider)
    if current_provider_id in _CONFIGURED_PROVIDER_IDS:
        return

    # Langfuse OTLP endpoint
    langfuse_host = _resolve_langfuse_host(settings.langfuse_host)
    endpoint = f"{langfuse_host.rstrip('/')}/api/public/otel/v1/traces"

    # Auth header
    secret_value = (
        settings.langfuse_secret_key.get_secret_value()
        if settings.langfuse_secret_key is not None
        else ""
    )
    auth_str = f"{settings.langfuse_public_key}:{secret_value}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()
    headers = {"Authorization": f"Basic {auth_b64}"}

    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
    processor = BatchSpanProcessor(exporter)
    if isinstance(current_provider, TracerProvider):
        provider = current_provider
    else:
        resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)
    provider.add_span_processor(processor)
    _CONFIGURED_PROVIDER_IDS.add(id(provider))
    logger.info("OpenTelemetry tracing configured with Langfuse exporter at %s", langfuse_host)


def force_flush_tracing() -> None:
    """Best-effort flush for BatchSpanProcessor-backed exporters."""
    provider = trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        try:
            flush()
        except Exception:
            logger.warning("tracing_force_flush_failed", exc_info=True)


def record_final_run_trace(summary: RunSummary) -> None:
    """Write the post-sink run summary used for cross-tool reconciliation."""
    tracer = trace.get_tracer("job_ftch.pipeline")
    with tracer.start_as_current_span("pipeline.run.final") as span:
        span.set_attribute("job_ftch.source_run_id", summary.source_run_id or "")
        span.set_attribute("job_ftch.tenant_id", summary.tenant_id or "")
        span.set_attribute("job_ftch.routing_accepted", summary.emitted)
        span.set_attribute("job_ftch.routed_review", summary.review)
        span.set_attribute("job_ftch.routed_rejected", summary.rejected)
        span.set_attribute("job_ftch.routed_deferred", summary.deferred)
        span.set_attribute("job_ftch.groups_created", summary.new_groups_created)
        span.set_attribute("job_ftch.groups_merged", summary.merged_into_group)
        span.set_attribute("job_ftch.posted", summary.posted)
        span.set_attribute("job_ftch.source_failures", len(summary.source_failures))
        span.set_attribute("job_ftch.source_partial", summary.source_partial)
        span.set_attribute("job_ftch.graph_hash", summary.graph_hash or "")
        span.set_attribute("job_ftch.fetched", summary.fetched)
        span.set_attribute("job_ftch.extracted", summary.extracted)
        span.set_attribute("job_ftch.dropped", summary.dropped)
        span.set_attribute("job_ftch.deferred", summary.deferred)
        span.set_attribute("job_ftch.failed", summary.failed)
        span.set_attribute("job_ftch.llm_usage_requests", summary.llm_usage_requests)
        span.set_attribute("job_ftch.llm_tokens_in", summary.llm_tokens_in)
        span.set_attribute("job_ftch.llm_cached_tokens_in", summary.llm_cached_tokens_in)
        span.set_attribute("job_ftch.llm_tokens_out", summary.llm_tokens_out)
        span.set_attribute("job_ftch.llm_cost_usd", summary.llm_cost_usd)
        span.set_attribute("job_ftch.llm_cost_is_complete", summary.llm_cost_is_complete)
        span.set_attribute(
            "job_ftch.llm_cost_pricing_version", summary.llm_cost_pricing_version or ""
        )
    force_flush_tracing()
