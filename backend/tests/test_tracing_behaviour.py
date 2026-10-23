"""Behaviour tests for app/observability/tracing.py.

No real OpenTelemetry SDK is required: the optional imports inside
configure_tracing()/get_tracer() are exercised through fake modules
injected into sys.modules, so every branch (enabled/disabled, with and
without endpoint, import failure, null fallback) is deterministic in any
environment. No network access happens anywhere in this file.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.observability import tracing
from app.observability.tracing import traced


# --------------------------------------------------------------------------- #
# fakes and fixtures
# --------------------------------------------------------------------------- #


class FakeSettings:
    """Stand-in for app.config.settings with only the otel_* fields."""

    def __init__(self, enabled=False, endpoint=None, service_name="ecommerce-agent"):
        self.otel_enabled = enabled
        self.otel_exporter_otlp_endpoint = endpoint
        self.otel_service_name = service_name


class ExplodingSettings:
    """Any attribute access fails; proves configure_tracing short-circuits."""

    def __getattr__(self, name):
        raise AssertionError(f"settings.{name} should not be touched")


@pytest.fixture(autouse=True)
def reset_tracing_state():
    """Reset module-level state around every test in this file."""
    saved = (tracing._tracer, tracing._configured)
    tracing._tracer = None
    tracing._configured = False
    yield
    tracing._tracer, tracing._configured = saved


@pytest.fixture
def fake_settings(monkeypatch):
    def _install(**kwargs):
        settings = FakeSettings(**kwargs)
        monkeypatch.setattr(tracing, "settings", settings)
        return settings

    return _install


class RecordingTracer:
    """Fake tracer whose spans record every attribute set on them."""

    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name, *args, **kwargs):
        span = RecordingSpan(name)
        self.spans.append(span)
        return span


class RecordingSpan:
    """Fake span context manager that records attributes and exit calls."""

    def __init__(self, name):
        self.name = name
        self.attributes = []
        self.exit_calls = []
        self.suppress = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exit_calls.append((exc_type, exc, tb))
        return self.suppress

    def set_attribute(self, key, value):
        self.attributes.append((key, value))

    def record_exception(self, *args, **kwargs):
        pass


def install_fake_otel(monkeypatch, *, with_sdk=True):
    """Register fake opentelemetry modules in sys.modules.

    Returns a dict of recording objects: set_provider_calls, get_tracer_calls,
    provider_processors, exporter_endpoints, resource_attrs, tracer (a
    RecordingTracer), and provider once constructed.
    """
    records = {
        "set_provider_calls": [],
        "get_tracer_calls": [],
        "provider_processors": [],
        "exporter_endpoints": [],
        "resource_attrs": None,
        "tracer": RecordingTracer(),
    }

    trace_mod = types.ModuleType("opentelemetry.trace")

    def set_tracer_provider(provider):
        records["set_provider_calls"].append(provider)

    def get_tracer(name, *args, **kwargs):
        records["get_tracer_calls"].append((name, args, kwargs))
        return records["tracer"]

    trace_mod.set_tracer_provider = set_tracer_provider
    trace_mod.get_tracer = get_tracer

    otel_pkg = types.ModuleType("opentelemetry")
    otel_pkg.trace = trace_mod

    modules = {
        "opentelemetry": otel_pkg,
        "opentelemetry.trace": trace_mod,
    }

    if with_sdk:
        exporter_mod = types.ModuleType(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )

        class OTLPSpanExporter:
            def __init__(self, endpoint=None):
                records["exporter_endpoints"].append(endpoint)

        exporter_mod.OTLPSpanExporter = OTLPSpanExporter

        resources_mod = types.ModuleType("opentelemetry.sdk.resources")

        @staticmethod
        def _resource_create(attrs):
            records["resource_attrs"] = attrs
            return ("resource", attrs)

        resources_mod.Resource = type(
            "Resource", (), {"create": staticmethod(_resource_create)}
        )

        sdk_trace_mod = types.ModuleType("opentelemetry.sdk.trace")

        class TracerProvider:
            def __init__(self, resource=None):
                self.resource = resource
                records["provider"] = self

            def add_span_processor(self, processor):
                records["provider_processors"].append(processor)

        sdk_trace_mod.TracerProvider = TracerProvider

        export_mod = types.ModuleType("opentelemetry.sdk.trace.export")

        class BatchSpanProcessor:
            def __init__(self, exporter):
                self.exporter = exporter

        export_mod.BatchSpanProcessor = BatchSpanProcessor
        records["exporter_class"] = OTLPSpanExporter

        # Parent packages so dotted imports resolve cleanly.
        sdk_pkg = types.ModuleType("opentelemetry.sdk")
        exporter_root = types.ModuleType("opentelemetry.exporter")
        otlp = types.ModuleType("opentelemetry.exporter.otlp")
        proto = types.ModuleType("opentelemetry.exporter.otlp.proto")
        http_pkg = types.ModuleType("opentelemetry.exporter.otlp.proto.http")

        sdk_pkg.resources = resources_mod
        sdk_pkg.trace = sdk_trace_mod
        sdk_trace_mod.export = export_mod
        exporter_root.otlp = otlp
        otlp.proto = proto
        proto.http = http_pkg
        http_pkg.trace_exporter = exporter_mod
        otel_pkg.sdk = sdk_pkg
        otel_pkg.exporter = exporter_root

        modules.update(
            {
                "opentelemetry.exporter": exporter_root,
                "opentelemetry.exporter.otlp": otlp,
                "opentelemetry.exporter.otlp.proto": proto,
                "opentelemetry.exporter.otlp.proto.http": http_pkg,
                "opentelemetry.exporter.otlp.proto.http.trace_exporter": exporter_mod,
                "opentelemetry.sdk": sdk_pkg,
                "opentelemetry.sdk.resources": resources_mod,
                "opentelemetry.sdk.trace": sdk_trace_mod,
                "opentelemetry.sdk.trace.export": export_mod,
            }
        )

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    return records




# --------------------------------------------------------------------------- #
# configure_tracing()
# --------------------------------------------------------------------------- #


def test_configure_disabled_returns_false_and_short_circuits(fake_settings):
    fake_settings(enabled=False)

    assert tracing.configure_tracing() is False
    assert tracing._configured is True
    assert tracing._tracer is None

    # Second call hits the already-configured branch and reports no tracer.
    assert tracing.configure_tracing() is False


def test_configure_already_configured_with_tracer_returns_true(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(tracing, "_configured", True)
    monkeypatch.setattr(tracing, "_tracer", sentinel)

    # ExplodingSettings proves the settings object is never touched.
    monkeypatch.setattr(tracing, "settings", ExplodingSettings())

    assert tracing.configure_tracing() is True


def test_configure_enabled_but_import_fails_returns_false(monkeypatch, fake_settings):
    fake_settings(enabled=True)
    # Setting a sys.modules entry to None makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "opentelemetry", None)

    assert tracing.configure_tracing() is False
    assert tracing._tracer is None


def test_configure_enabled_without_endpoint(fake_settings, monkeypatch):
    fake_settings(enabled=True, service_name="ares-test")
    records = install_fake_otel(monkeypatch)

    assert tracing.configure_tracing() is True

    assert records["resource_attrs"] == {
        "service.name": "ares-test",
        "service.version": "1.0.0",
    }
    assert len(records["set_provider_calls"]) == 1
    assert records["provider"].resource == ("resource", records["resource_attrs"])
    assert records["provider_processors"] == []
    assert records["exporter_endpoints"] == []
    assert records["get_tracer_calls"] == [("ecommerce-agent", (), {})]
    assert tracing._tracer is records["tracer"]


def test_configure_enabled_with_endpoint_adds_batch_processor(fake_settings, monkeypatch):
    fake_settings(enabled=True, endpoint="http://collector:4318")
    records = install_fake_otel(monkeypatch)

    assert tracing.configure_tracing() is True

    assert records["exporter_endpoints"] == ["http://collector:4318/v1/traces"]
    assert len(records["provider_processors"]) == 1
    processor = records["provider_processors"][0]
    assert isinstance(processor.exporter, records["exporter_class"])


# --------------------------------------------------------------------------- #
# _service_version()
# --------------------------------------------------------------------------- #


def test_service_version_reads_app_version():
    assert tracing._service_version() == "1.0.0"


def test_service_version_falls_back_to_unknown(monkeypatch):
    import app

    monkeypatch.delattr(app, "__version__")

    assert tracing._service_version() == "unknown"


# --------------------------------------------------------------------------- #
# get_tracer()
# --------------------------------------------------------------------------- #


def test_get_tracer_returns_cached_tracer(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(tracing, "_tracer", sentinel)

    assert tracing.get_tracer() is sentinel


def test_get_tracer_caches_api_tracer_when_none(monkeypatch):
    records = install_fake_otel(monkeypatch)

    first = tracing.get_tracer()

    assert first is records["tracer"]
    assert records["get_tracer_calls"] == [("ecommerce-agent", (), {})]
    # Second call returns the cached tracer without another get_tracer call.
    assert tracing.get_tracer() is first
    assert len(records["get_tracer_calls"]) == 1


def test_get_tracer_null_fallback_when_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry", None)

    tracer = tracing.get_tracer()

    # The null fallback is not cached: every call builds a fresh instance.
    assert tracing._tracer is None
    assert tracing.get_tracer() is not tracer

    span = tracer.start_as_current_span("anything")
    with span as entered:
        assert entered is span
        span.set_attribute("k", "v")  # no-op, must not raise
        span.record_exception(ValueError())  # no-op, must not raise


# --------------------------------------------------------------------------- #
# traced() context manager
# --------------------------------------------------------------------------- #


def test_traced_sets_only_non_none_attributes(monkeypatch):
    tracer = RecordingTracer()
    monkeypatch.setattr(tracing, "_tracer", tracer)

    with traced("job.run", {"job_id": "j-1", "org_id": None, "route": "/x"}) as span:
        assert isinstance(span, RecordingSpan)
        assert span.name == "job.run"

    assert span.attributes == [("job_id", "j-1"), ("route", "/x")]
    (exc_type, exc, tb), = span.exit_calls
    assert (exc_type, exc, tb) == (None, None, None)  # exit was delegated cleanly


def test_traced_without_attributes_sets_none(monkeypatch):
    tracer = RecordingTracer()
    monkeypatch.setattr(tracing, "_tracer", tracer)

    with traced("plain"):
        pass

    assert tracer.spans[0].attributes == []


def test_traced_delegates_exit_and_propagates_exceptions(monkeypatch):
    tracer = RecordingTracer()
    monkeypatch.setattr(tracing, "_tracer", tracer)

    boom = ValueError("boom")
    with pytest.raises(ValueError, match="boom"):
        with traced("failing"):
            raise boom

    span = tracer.spans[0]
    (exc_type, exc, tb), = span.exit_calls
    assert exc is boom
    assert exc_type is ValueError


def test_traced_exit_result_suppresses_exception(monkeypatch):
    tracer = RecordingTracer()
    monkeypatch.setattr(tracing, "_tracer", tracer)

    # A real span context manager returning True suppresses the exception.
    class SuppressingSpan(RecordingSpan):
        def __exit__(self, exc_type, exc, tb):
            self.exit_calls.append((exc_type, exc, tb))
            return True

    class SuppressingTracer:
        def start_as_current_span(self, name, *args, **kwargs):
            span = SuppressingSpan(name)
            tracer.spans.append(span)
            return span

    monkeypatch.setattr(tracing, "_tracer", SuppressingTracer())

    with traced("suppressed"):
        raise RuntimeError("eaten")  # must not propagate
