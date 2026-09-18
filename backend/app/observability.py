"""Single, privacy-bounded OpenTelemetry and structured-logging boundary.

Business code records only low-cardinality operational attributes here. Raw issue
text, retrieved excerpts, prompts, provider payloads, credentials, and headers are
never accepted by this module's public helpers.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from uuid import uuid4

from opentelemetry import metrics, propagate, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

CORRELATION_HEADER = "X-Correlation-ID"
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_configured = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span_context = trace.get_current_span().get_span_context()
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_observability(service_name: str, otlp_endpoint: str | None) -> None:
    """Configure one process once. No endpoint means local no-op export.

    OTLP exporters are asynchronous. Collector loss can make an export fail, but
    never runs on the request/workflow success path and never raises into it.
    """
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    if otlp_endpoint:
        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"))
        )
        trace.set_tracer_provider(tracer_provider)

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/metrics"),
            export_interval_millis=5_000,
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    _configured = True


def valid_or_new_correlation_id(candidate: str | None) -> str:
    if candidate and _CORRELATION_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


def get_correlation_id() -> str | None:
    return _correlation_id.get()


@contextlib.contextmanager
def correlation_context(correlation_id: str | None) -> Iterator[None]:
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


@contextlib.contextmanager
def span(
    name: str,
    *,
    attributes: dict[str, str | int | float | bool] | None = None,
    traceparent: str | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[trace.Span]:
    parent_context = propagate.extract({"traceparent": traceparent}) if traceparent else None
    with trace.get_tracer("repotriage").start_as_current_span(
        name, context=parent_context, kind=kind, attributes=attributes
    ) as current_span:
        try:
            yield current_span
        except Exception as exc:
            # Only the exception class is exported. Exception strings may contain
            # provider payloads, issue text, credentials, or database values.
            current_span.set_attribute("error.type", type(exc).__name__)
            current_span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


@dataclass(frozen=True)
class _Instruments:
    http_requests: Counter
    http_errors: Counter
    http_duration: Histogram
    workflow_runs: Counter
    workflow_duration: Histogram
    stage_outcomes: Counter
    stage_duration: Histogram
    workflow_retries: Counter
    retrieval_outcomes: Counter
    ai_calls: Counter
    ai_input_tokens: Counter
    ai_output_tokens: Counter
    ai_cost_usd: Counter
    human_decisions: Counter
    evaluation_runs: Counter


def _instruments() -> _Instruments:
    meter = metrics.get_meter("repotriage")
    return _Instruments(
        http_requests=meter.create_counter("repotriage.http.requests"),
        http_errors=meter.create_counter("repotriage.http.errors"),
        http_duration=meter.create_histogram("repotriage.http.duration", unit="s"),
        workflow_runs=meter.create_counter("repotriage.workflow.runs"),
        workflow_duration=meter.create_histogram("repotriage.workflow.duration", unit="s"),
        stage_outcomes=meter.create_counter("repotriage.workflow.stage.outcomes"),
        stage_duration=meter.create_histogram("repotriage.workflow.stage.duration", unit="s"),
        workflow_retries=meter.create_counter("repotriage.workflow.retries"),
        retrieval_outcomes=meter.create_counter("repotriage.retrieval.outcomes"),
        ai_calls=meter.create_counter("repotriage.ai.calls"),
        ai_input_tokens=meter.create_counter("repotriage.ai.input_tokens", unit="{token}"),
        ai_output_tokens=meter.create_counter("repotriage.ai.output_tokens", unit="{token}"),
        ai_cost_usd=meter.create_counter("repotriage.ai.estimated_cost", unit="USD"),
        human_decisions=meter.create_counter("repotriage.human_decisions"),
        evaluation_runs=meter.create_counter("repotriage.evaluation.runs"),
    )


def record_http(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    attrs = {
        "http.request.method": method,
        "http.route": route,
        "http.status_class": f"{status_code // 100}xx",
    }
    instruments = _instruments()
    instruments.http_requests.add(1, attrs)
    instruments.http_duration.record(duration_seconds, attrs)
    if status_code >= 400:
        instruments.http_errors.add(1, attrs)


def record_stage(stage: str, outcome: str, duration_seconds: float) -> None:
    attrs = {"workflow.stage": stage, "outcome": outcome}
    instruments = _instruments()
    instruments.stage_outcomes.add(1, attrs)
    instruments.stage_duration.record(duration_seconds, attrs)


def record_workflow(outcome: str, duration_seconds: float) -> None:
    attrs = {"outcome": outcome}
    instruments = _instruments()
    instruments.workflow_runs.add(1, attrs)
    instruments.workflow_duration.record(duration_seconds, attrs)


def record_retry() -> None:
    _instruments().workflow_retries.add(1)


def record_retrieval(status: str, mechanism: str) -> None:
    # Mechanism is code/config-owned (for example pgvector-cosine), never a raw id.
    _instruments().retrieval_outcomes.add(1, {"status": status, "mechanism": mechanism})


def record_ai(
    provider: str, status: str, input_tokens: int, output_tokens: int, cost: float
) -> None:
    attrs = {"provider": provider, "status": status}
    instruments = _instruments()
    instruments.ai_calls.add(1, attrs)
    instruments.ai_input_tokens.add(input_tokens, attrs)
    instruments.ai_output_tokens.add(output_tokens, attrs)
    instruments.ai_cost_usd.add(cost, attrs)


def record_human_decision(decision: str) -> None:
    _instruments().human_decisions.add(1, {"decision": decision})


def record_evaluation(passed: bool) -> None:
    _instruments().evaluation_runs.add(1, {"status": "passed" if passed else "failed"})
    provider = metrics.get_meter_provider()
    force_flush = getattr(provider, "force_flush", None)
    if force_flush:
        force_flush(timeout_millis=2_000)


def monotonic() -> float:
    return time.monotonic()
