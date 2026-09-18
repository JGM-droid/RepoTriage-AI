"""Milestone 3.3 focused evidence for correlation, tracing, metrics, and fail-open export."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.observability as observability
import app.workflow.tasks as workflow_tasks
from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    Analysis,
    AuditEvent,
    Issue,
    Repository,
)
from app.workflow.tasks import process_workflow_run
from tests.db_maintenance import truncate_for_test

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture(scope="module")
def telemetry() -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    metric_reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[metric_reader]))
    return span_exporter, metric_reader


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for observability integration tests.")
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            truncate_for_test(connection, TRUNCATE_CORE_TABLES)
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture()
def client(database_session: Session) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        yield database_session

    app.dependency_overrides[get_triage_session] = override_session
    try:
        yield TestClient(app, headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)})
    finally:
        app.dependency_overrides.clear()


def add_issue(session: Session, *, number: int = 1, body: str = "Traceback attached") -> Issue:
    repository = Repository(
        tenant_id=DEFAULT_ORGANIZATION_ID,
        name=f"observability-{number}",
        source_url=f"https://example.test/repository-{number}",
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=number,
        title="Application crashes on startup",
        body=body,
        state="open",
        source_url=f"https://example.test/issues/{number}",
    )
    session.add(issue)
    session.commit()
    return issue


def _metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    return {
        metric.name
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def test_correlation_trace_metrics_and_privacy_follow_one_analysis(
    client: TestClient,
    database_session: Session,
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    span_exporter, metric_reader = telemetry
    span_exporter.clear()
    prohibited = "SENSITIVE-ISSUE-BODY-MARKER-DO-NOT-EXPORT"
    issue = add_issue(database_session, body=f"Crash report includes {prohibited}")
    correlation_id = "walkthrough-3.3-safe"

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage",
        json={},
        headers={"X-Correlation-ID": correlation_id},
    )

    assert response.status_code == 202
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.json()["correlation_id"] == correlation_id
    analysis = database_session.get(Analysis, response.json()["analysis_id"])
    assert analysis.correlation_id == correlation_id
    assert analysis.traceparent and len(analysis.traceparent) == 55

    audit_events = database_session.query(AuditEvent).filter_by(issue_id=issue.id).all()
    assert audit_events
    assert all(event.metadata_["correlation_id"] == correlation_id for event in audit_events)

    spans = span_exporter.get_finished_spans()
    names = {item.name for item in spans}
    assert {
        "http.request",
        "authorization.resolve_identity",
        "authorization.require_role",
        "authorization.workflow",
        "workflow.run",
        "workflow.stage",
        "retrieval.query",
        "ai.inference",
        "persistence.recommendation",
    } <= names
    http_span = next(item for item in spans if item.name == "http.request")
    workflow_span = next(item for item in spans if item.name == "workflow.run")
    assert workflow_span.context.trace_id == http_span.context.trace_id
    assert workflow_span.parent.span_id == http_span.context.span_id

    telemetry_text = json.dumps(
        [dict(item.attributes or {}) for item in spans], sort_keys=True, default=str
    )
    assert prohibited not in telemetry_text
    assert "Crash report" not in telemetry_text

    assert {
        "repotriage.http.requests",
        "repotriage.http.duration",
        "repotriage.workflow.runs",
        "repotriage.workflow.stage.outcomes",
        "repotriage.workflow.stage.duration",
        "repotriage.retrieval.outcomes",
        "repotriage.ai.calls",
        "repotriage.ai.input_tokens",
        "repotriage.ai.output_tokens",
        "repotriage.ai.estimated_cost",
    } <= _metric_names(metric_reader)


def test_malformed_external_correlation_is_replaced(client: TestClient) -> None:
    response = client.get(
        "/api/v1/health", headers={"X-Correlation-ID": "contains spaces " + "x" * 1000}
    )

    generated = response.headers["X-Correlation-ID"]
    assert response.status_code == 200
    assert generated != "contains spaces " + "x" * 1000
    assert len(generated) <= 64


def test_retry_preserves_durable_trace_and_correlation_context(
    database_session: Session,
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    span_exporter, _ = telemetry
    span_exporter.clear()
    issue = add_issue(database_session, number=3)
    analysis = Analysis(
        issue_id=issue.id,
        status="queued",
        initiating_actor_id=DEFAULT_ORG_REVIEWER_ACTOR_ID,
        correlation_id="retry-stable",
        traceparent="00-11111111111111111111111111111111-2222222222222222-01",
    )
    database_session.add(analysis)
    database_session.commit()
    original = workflow_tasks.classify
    calls = 0

    def fail_once(issue_arg):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return original(issue_arg)

    monkeypatch.setattr(workflow_tasks, "classify", fail_once)
    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)
    result = process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert result.status == "completed"
    assert analysis.correlation_id == "retry-stable"
    workflow_spans = [
        item for item in span_exporter.get_finished_spans() if item.name == "workflow.run"
    ]
    assert len(workflow_spans) == 2
    assert {item.context.trace_id for item in workflow_spans} == {int("1" * 32, 16)}


class _UnavailableExporter(SpanExporter):
    def export(self, spans: tuple[ReadableSpan, ...]) -> SpanExportResult:
        del spans
        return SpanExportResult.FAILURE


def test_exporter_failure_does_not_break_core_workflow(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session, number=2)
    analysis = Analysis(
        issue_id=issue.id,
        status="queued",
        initiating_actor_id=DEFAULT_ORG_REVIEWER_ACTOR_ID,
        correlation_id="collector-unavailable",
        traceparent="00-11111111111111111111111111111111-2222222222222222-01",
    )
    database_session.add(analysis)
    database_session.commit()

    unavailable_provider = TracerProvider()
    unavailable_provider.add_span_processor(SimpleSpanProcessor(_UnavailableExporter()))
    monkeypatch.setattr(observability.trace, "get_tracer", unavailable_provider.get_tracer)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    assert result.recommendations
