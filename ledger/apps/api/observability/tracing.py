from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from ledger.settings import get_settings


def init_tracing(app: FastAPI) -> None:
    # Initialize tracing only when an OTLP endpoint is provided.
    if trace.get_tracer_provider().__class__.__name__ != "ProxyTracerProvider":
        return

    s = get_settings()
    endpoint = s.otel_exporter_otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        # Tracing disabled unless explicitly configured
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", s.app_name or "doublex-ledger")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
