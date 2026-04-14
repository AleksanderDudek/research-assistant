"""OpenTelemetry setup – call configure_telemetry() once at startup."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from agent.config import settings

_configured = False


def configure_telemetry(console_fallback: bool = False) -> None:
    """Initialise OTel with OTLP exporter pointing at Jaeger.

    Call this once from cli.py before running the agent. Subsequent calls are
    no-ops (guarded by `_configured`).

    Args:
        console_fallback: If True, also export spans to stdout (useful in CI).
    """
    global _configured
    if _configured:
        return

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)

    try:
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=True,
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    except Exception:
        # If Jaeger is not available (e.g. in tests), fall through to console
        console_fallback = True

    if console_fallback:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer(name: str = "agent") -> trace.Tracer:
    return trace.get_tracer(name)
