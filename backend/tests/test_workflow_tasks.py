"""Durable workflow-run tests (Milestone 2.1): retry, timeout, and recovery.

Exercises `process_workflow_run` directly (the function the Celery task
wraps) so retry, timeout, and resumption semantics are tested without
depending on real Celery timing or a live broker.
"""

import json
import os
from collections.abc import Iterator

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.workflow.tasks as workflow_tasks
from app.ai_gateway import mock_adapter
from app.ai_gateway.contracts import STATUS_FALLBACK, AIRequest, AIResponse
from app.ai_gateway.stage import run_ai_inference_stage
from app.models.core import (
    Analysis,
    AuditEvent,
    HumanDecision,
    Issue,
    Recommendation,
    Repository,
    StageAttempt,
)
from app.triage import rules as triage_rules
from app.triage.service import status_history
from app.workflow.tasks import process_workflow_run

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for durable workflow tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def add_issue(
    session: Session,
    *,
    title: str = "App crash on startup",
    body: str = "Traceback attached below",
    state: str = "open",
) -> Issue:
    repository = Repository(name="pallets/flask", source_url="https://github.com/pallets/flask")
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=1,
        title=title,
        body=body,
        state=state,
        source_url="https://github.com/pallets/flask/issues/1",
    )
    session.add(issue)
    session.commit()
    return issue


def start_analysis(session: Session, issue: Issue) -> Analysis:
    analysis = Analysis(issue_id=issue.id, status="queued")
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis


def test_process_workflow_run_completes_and_persists_stage_attempts_in_order(
    database_session: Session,
) -> None:
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    attempts = (
        database_session.query(StageAttempt)
        .filter_by(analysis_id=analysis.id)
        .order_by(StageAttempt.created_at, StageAttempt.id)
        .all()
    )
    assert [attempt.stage for attempt in attempts] == list(workflow_tasks.STAGE_ORDER)
    assert all(attempt.status == "succeeded" for attempt in attempts)
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1
    assert database_session.query(HumanDecision).count() == 0


def test_temporary_failure_retries_within_the_bound_and_recovers(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    call_count = {"value": 0}
    original_classify = workflow_tasks.classify

    def flaky_classify(issue_arg):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise ValueError("transient failure")
        return original_classify(issue_arg)

    monkeypatch.setattr(workflow_tasks, "classify", flaky_classify)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert analysis.attempt_count == 1

    # Resume: a fresh call with the same analysis id retries and now succeeds.
    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1


def test_exhausted_retries_become_failed(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    def always_fail(*args, **kwargs):
        raise ValueError("permanent failure")

    monkeypatch.setattr(workflow_tasks, "classify", always_fail)

    for _ in range(workflow_tasks.settings.triage_max_attempts - 1):
        with pytest.raises(workflow_tasks._RetryableWorkflowError):
            process_workflow_run(database_session, analysis.id)
        database_session.refresh(analysis)
        assert analysis.status == "retrying"

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "failed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0

    history = status_history(database_session, issue.id, analysis.id)
    assert history[-1][0] == "failed"


def test_timeout_becomes_timed_out(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    def time_out(*args, **kwargs):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(workflow_tasks, "classify", time_out)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "timed_out"
    attempts = database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    assert any(attempt.status == "timed_out" for attempt in attempts)


def test_a_completed_run_is_never_re_executed(database_session: Session) -> None:
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)
    process_workflow_run(database_session, analysis.id)

    # Re-invoking (simulating a re-delivered/duplicate task) is a safe no-op.
    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1


def test_an_interrupted_run_resumes_without_duplicating_the_recommendation(
    database_session: Session,
) -> None:
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    # Simulate a worker crash after the recommendation was written but before
    # the analysis was marked completed.
    analysis.status = "running"
    database_session.commit()
    database_session.add(Recommendation(analysis_id=analysis.id, status="proposed", content="{}"))
    database_session.commit()

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1


def test_a_crash_after_a_middle_stage_succeeds_resumes_without_rerunning_it(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after `assess` succeeds but before `propose` runs must resume
    at `propose`, not re-execute `classify`/`retrieve_fixture_evidence`/
    `assess` — proving persisted stage output, not just persisted status,
    survives the interruption."""
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    classification = triage_rules.classify(issue)
    evidence = triage_rules.retrieve_fixture_evidence(issue, classification)
    assessment = triage_rules.assess(issue, classification, evidence)

    # Leave the database exactly as a real crash would: the three completed
    # stages' StageAttempt rows (with output) are durably committed, the
    # analysis is mid-attempt, and nothing later has run yet.
    analysis.status = "running"
    analysis.current_stage = "assess"
    database_session.commit()
    for stage, result in (
        ("classify", classification),
        ("retrieve_fixture_evidence", evidence),
        ("assess", assessment),
    ):
        database_session.add(
            StageAttempt(
                analysis_id=analysis.id,
                stage=stage,
                attempt_number=1,
                status="succeeded",
                output=workflow_tasks._serialize_stage_output(stage, result),
            )
        )
    database_session.commit()

    def _must_not_run(*args, **kwargs):
        raise AssertionError("a stage already succeeded in this attempt was re-executed")

    monkeypatch.setattr(workflow_tasks, "classify", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "retrieve_fixture_evidence", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "assess", _must_not_run)

    result = process_workflow_run(database_session, analysis.id)

    # 3. execution resumes at the next incomplete stage.
    assert result.status == "completed"
    assert result.current_stage == "human_review"

    # 1 & 2. earlier stages were not re-executed and no stage has more than
    # one attempt row for this attempt number.
    attempts = database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    stage_counts = {stage: 0 for stage in workflow_tasks.STAGE_ORDER}
    for attempt in attempts:
        stage_counts[attempt.stage] += 1
    assert stage_counts == {stage: 1 for stage in workflow_tasks.STAGE_ORDER}
    assert all(attempt.status == "succeeded" for attempt in attempts)

    # 4. exactly one recommendation.
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1
    # 5. no HumanDecision is ever created by the worker.
    assert database_session.query(HumanDecision).count() == 0


def test_ai_inference_stage_records_one_routing_audit_event_with_mock_provenance(
    database_session: Session,
) -> None:
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    events = (
        database_session.query(AuditEvent)
        .filter_by(issue_id=issue.id, event_type="ai_inference_routing")
        .all()
    )
    assert len(events) == 1
    assert events[0].metadata_["provider"] == "mock"
    assert events[0].metadata_["status"] == "succeeded"
    assert events[0].metadata_["fallback_reason"] is None
    assert events[0].metadata_["analysis_id"] == str(analysis.id)

    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    content = json.loads(recommendation.content)
    assert content["ai_inference"]["provider"] == "mock"
    assert content["ai_inference"]["status"] == "succeeded"
    assert content["ai_inference"]["input_tokens"] == 0
    assert content["ai_inference"]["estimated_cost_usd"] == 0.0


def test_workflow_completes_with_a_deterministic_result_when_the_provider_is_unavailable(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A controlled provider failure must never block the deterministic
    workflow: the fallback AIResponse is accepted like any other successful
    stage output, and the recommendation still completes normally."""
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    def fallback_ai_inference(
        issue_arg, classification, evidence, assessment, proposed_action, retrieved_context=()
    ):
        del issue_arg, retrieved_context
        fallback = mock_adapter.call(
            AIRequest(
                task="triage_narrative",
                classification=classification,
                evidence=evidence,
                assessment=assessment,
                proposed_action=proposed_action,
            )
        )
        return AIResponse(
            narrative=fallback.narrative,
            status=STATUS_FALLBACK,
            provider=fallback.provider,
            model=fallback.model,
            prompt_name=fallback.prompt_name,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0.0,
            latency_ms=12.5,
            fallback_reason="openai_timeout",
        )

    monkeypatch.setattr(workflow_tasks, "run_ai_inference_stage", fallback_ai_inference)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    content = json.loads(recommendation.content)
    assert content["ai_inference"]["status"] == "fallback"
    assert content["ai_inference"]["fallback_reason"] == "openai_timeout"
    assert database_session.query(HumanDecision).count() == 0


def test_a_killed_worker_redelivered_after_ai_inference_succeeds_does_not_call_the_provider_again(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The single most important cost-safety property: once the paid (or
    mock) call for an attempt has succeeded and its output is persisted,
    redelivery must reuse it, never call the provider again."""
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    classification = triage_rules.classify(issue)
    evidence = triage_rules.retrieve_fixture_evidence(issue, classification)
    assessment = triage_rules.assess(issue, classification, evidence)
    proposal = triage_rules.propose(issue, classification, assessment)
    ai_response = run_ai_inference_stage(issue, classification, evidence, assessment, proposal)

    analysis.status = "running"
    analysis.current_stage = "ai_inference"
    database_session.commit()
    for stage, result in (
        ("classify", classification),
        ("retrieve_fixture_evidence", evidence),
        ("assess", assessment),
        ("propose", proposal),
        ("ai_inference", ai_response),
    ):
        database_session.add(
            StageAttempt(
                analysis_id=analysis.id,
                stage=stage,
                attempt_number=1,
                status="succeeded",
                output=workflow_tasks._serialize_stage_output(stage, result),
            )
        )
    database_session.commit()

    def _must_not_run(*args, **kwargs):
        raise AssertionError(
            "the AI provider was called again after this attempt already succeeded"
        )

    monkeypatch.setattr(workflow_tasks, "run_ai_inference_stage", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "classify", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "retrieve_fixture_evidence", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "assess", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "propose", _must_not_run)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    attempts = database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    stage_counts = {stage: 0 for stage in workflow_tasks.STAGE_ORDER}
    for attempt in attempts:
        stage_counts[attempt.stage] += 1
    assert stage_counts == {stage: 1 for stage in workflow_tasks.STAGE_ORDER}
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1
    assert database_session.query(HumanDecision).count() == 0
    # No routing event is (re)written for a stage that was skipped, not
    # freshly executed, on this resumed call.
    routing_events = (
        database_session.query(AuditEvent)
        .filter_by(issue_id=issue.id, event_type="ai_inference_routing")
        .all()
    )
    assert routing_events == []


def test_retrieval_stage_records_one_audit_event_and_bounds_the_ai_context(
    database_session: Session,
) -> None:
    from app.retrieval.embedding import FakeEmbeddingAdapter
    from app.retrieval.ingest import ingest_issues

    issue = add_issue(
        database_session,
        title="CLI crashes on Windows",
        body="Traceback: segfault in main loop",
        state="closed",
    )
    repository = database_session.get(Repository, issue.repository_id)
    related = Issue(
        repository_id=issue.repository_id,
        external_number=2,
        title="CLI crashes on startup with traceback",
        body="Similar traceback: segfault in main loop as well",
        state="closed",
        source_url="https://github.com/pallets/flask/issues/2",
    )
    database_session.add(related)
    database_session.commit()
    ingest_issues(database_session, repository, FakeEmbeddingAdapter(dimension=384))

    analysis = start_analysis(database_session, issue)
    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    events = (
        database_session.query(AuditEvent)
        .filter_by(issue_id=issue.id, event_type="retrieval_evidence")
        .all()
    )
    assert len(events) == 1
    assert events[0].metadata_["repository_id"] == str(issue.repository_id)
    assert events[0].metadata_["selected_count"] <= 3
    assert "excerpt" not in events[0].metadata_  # no full body/excerpt text in audit metadata
    assert "content" not in events[0].metadata_

    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    content = json.loads(recommendation.content)
    retrieved_items = content["retrieved_evidence"]["items"]
    assert len(retrieved_items) <= 3
    identifiers = [item["identifier"] for item in retrieved_items]
    assert f"issue:{issue.external_number}" not in identifiers  # never cites itself


def test_a_killed_worker_redelivered_after_retrieval_succeeds_does_not_re_query_or_re_embed(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same cost-safety property as the ai_inference resume test, for
    the retrieval stage: once retrieval succeeds and its output is
    persisted, redelivery must reuse it, never query/embed again."""
    from app.retrieval.stage import run_retrieve_related_evidence_stage

    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    classification = triage_rules.classify(issue)
    evidence = triage_rules.retrieve_fixture_evidence(issue, classification)
    assessment = triage_rules.assess(issue, classification, evidence)
    proposal = triage_rules.propose(issue, classification, assessment)
    retrieval_result = run_retrieve_related_evidence_stage(database_session, issue, classification)

    analysis.status = "running"
    analysis.current_stage = "retrieve_related_evidence"
    database_session.commit()
    for stage, result in (
        ("classify", classification),
        ("retrieve_fixture_evidence", evidence),
        ("assess", assessment),
        ("propose", proposal),
        ("retrieve_related_evidence", retrieval_result),
    ):
        database_session.add(
            StageAttempt(
                analysis_id=analysis.id,
                stage=stage,
                attempt_number=1,
                status="succeeded",
                output=workflow_tasks._serialize_stage_output(stage, result),
            )
        )
    database_session.commit()

    def _must_not_run(*args, **kwargs):
        raise AssertionError(
            "retrieval was queried/embedded again after this attempt already succeeded"
        )

    monkeypatch.setattr(workflow_tasks, "run_retrieve_related_evidence_stage", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "classify", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "retrieve_fixture_evidence", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "assess", _must_not_run)
    monkeypatch.setattr(workflow_tasks, "propose", _must_not_run)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    attempts = database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    stage_counts = {stage: 0 for stage in workflow_tasks.STAGE_ORDER}
    for attempt in attempts:
        stage_counts[attempt.stage] += 1
    assert stage_counts == {stage: 1 for stage in workflow_tasks.STAGE_ORDER}
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1
    assert database_session.query(HumanDecision).count() == 0
    retrieval_events = (
        database_session.query(AuditEvent)
        .filter_by(issue_id=issue.id, event_type="retrieval_evidence")
        .all()
    )
    assert retrieval_events == []


def test_workflow_completes_with_a_degraded_empty_result_when_retrieval_fails(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine retrieval failure (e.g. a transient database error) must
    degrade to an explicit empty result and let the deterministic workflow
    complete — it must never block the recommendation or human review."""
    from app.retrieval.contracts import RetrievalResult

    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    def failing_retrieval(session_arg, issue_arg, classification):
        del session_arg, issue_arg, classification
        return RetrievalResult(
            items=(),
            query_summary="retrieval failed",
            candidates_considered=0,
            total_excerpt_chars=0,
            mechanism="pgvector-cosine:fake-hash-embedder",
            status="failed",
            failure_reason="simulated transient database error",
        )

    monkeypatch.setattr(workflow_tasks, "run_retrieve_related_evidence_stage", failing_retrieval)

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    content = json.loads(recommendation.content)
    assert content["retrieved_evidence"]["status"] == "failed"
    assert content["retrieved_evidence"]["items"] == []
    assert content["ai_inference"]["citations"] == []
    assert database_session.query(HumanDecision).count() == 0


def test_database_rejects_a_second_stage_attempt_row_for_the_same_attempt(
    database_session: Session,
) -> None:
    """The unique constraint on (analysis_id, stage, attempt_number) is a
    hard invariant, not just an application-level convention."""
    issue = add_issue(database_session)
    analysis = start_analysis(database_session, issue)

    database_session.add(
        StageAttempt(
            analysis_id=analysis.id, stage="classify", attempt_number=1, status="succeeded"
        )
    )
    database_session.commit()

    database_session.add(
        StageAttempt(
            analysis_id=analysis.id, stage="classify", attempt_number=1, status="succeeded"
        )
    )
    with pytest.raises(IntegrityError):
        database_session.commit()
    database_session.rollback()
