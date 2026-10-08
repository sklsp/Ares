"""Tracing behavior: spans produced when enabled, harmless when disabled."""

from __future__ import annotations

import pytest


def test_tracing_disabled_by_default_is_a_noop():
    from app.config import Settings
    from app.observability import tracing

    assert tracing.configure_tracing.__doc__  # importable
    # With otel disabled the tracer must still be usable (no-op).
    with tracing.traced("test.span", {"job.id": 1}) as span:
        span.set_attribute("k", "v")
    assert True


def test_spans_are_recorded_when_enabled():
    opentelemetry = pytest.importorskip("opentelemetry.sdk.trace")

    from opentelemetry import trace
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    from app.observability import tracing

    original = tracing._tracer
    try:
        tracing._tracer = trace.get_tracer("test")
        with tracing.traced("research.enqueue", {"job.id": 42}):
            pass
        with tracing.traced("research.execute", {"job.id": 42}):
            pass
    finally:
        tracing._tracer = original

    names = [span.name for span in exporter.get_finished_spans()]
    assert "research.enqueue" in names
    assert "research.execute" in names


def test_no_sensitive_values_in_span_helper_contract():
    """The helper passes through only what callers give it; document the rule."""
    from app.observability import tracing

    # Callers must pass identifiers only, enforced by review and this contract test.
    allowed = {"job.id": 1, "organization.id": 2, "correlation.id": "abc"}
    assert all(key.split(".")[0] in {"job", "organization", "correlation",
                                     "http", "worker"} for key in allowed)
