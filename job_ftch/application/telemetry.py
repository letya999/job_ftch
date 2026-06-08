"""OpenTelemetry bootstrap for local runs."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def configure_telemetry(service_name: str, *, console_exporter: bool) -> None:
    current_provider = trace.get_tracer_provider()
    if isinstance(current_provider, TracerProvider):
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    if console_exporter:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
