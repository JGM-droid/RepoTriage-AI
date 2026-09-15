"""Celery task and durable orchestration for the deterministic triage workflow.

Executes classify -> retrieve_fixture_evidence -> assess -> propose ->
ai_inference -> human_review in a background worker (Milestone 2.1;
`ai_inference` added in Milestone 2.2, see ADR 0008) instead of inside the
HTTP request. `ai_inference` supplements the deterministic classification,
severity, and proposed action with an AI-generated narrative grounded in
the same evidence — it never changes them, and it never creates a
`HumanDecision`. PostgreSQL persists the workflow-run (`Analysis`) status,
current stage, attempt count, and one `StageAttempt` row per stage attempt
(including that stage's deterministic output), so a crashed or re-delivered
task resumes at the first incomplete stage of its attempt instead of
re-running stages that already succeeded: a terminal analysis is never
re-executed, a stage already `succeeded` in the current attempt is reused
from its persisted output rather than recomputed, and an already-created
recommendation is never duplicated. A database-level unique constraint on
(analysis_id, stage, attempt_number) makes "at most one attempt row per
stage per attempt" a hard invariant, not just an application convention. The
worker never creates a `HumanDecision` (see ADR 0003); a completed run only
ever produces a proposal awaiting explicit human review.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import UUID

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session

from app.ai_gateway.contracts import AIResponse
from app.ai_gateway.stage import run_ai_inference_stage
from app.config import get_settings
from app.database import SessionLocal
from app.models.core import Analysis, AuditEvent, Issue, Recommendation, StageAttempt
from app.triage.rules import (
    TRIAGE_RULESET_VERSION,
    Assessment,
    Classification,
    EvidenceItem,
    HumanReviewBoundary,
    ProposedAction,
    assess,
    classify,
    human_review,
    propose,
    retrieve_fixture_evidence,
)
from app.workflow.celery_app import celery_app

STAGE_ORDER = (
    "classify",
    "retrieve_fixture_evidence",
    "assess",
    "propose",
    "ai_inference",
    "human_review",
)
TERMINAL_STATUSES = ("completed", "failed", "timed_out")
_STATUS_EVENT_TYPE = "triage_status_transition"
_AI_ROUTING_EVENT_TYPE = "ai_inference_routing"

settings = get_settings()


class WorkflowRunNotFoundError(LookupError):
    """Raised when a workflow task references an unknown analysis id."""


class _RetryableWorkflowError(RuntimeError):
    """Raised only to trigger a bounded Celery-level retry.

    By the time this is raised, PostgreSQL already reflects the
    `retrying` status and the failed stage attempt.
    """


def record_transition(
    session: Session, issue: Issue, analysis: Analysis, transition_status: str
) -> None:
    session.add(
        AuditEvent(
            repository_id=issue.repository_id,
            issue_id=issue.id,
            event_type=_STATUS_EVENT_TYPE,
            metadata_={"analysis_id": str(analysis.id), "status": transition_status},
        )
    )
    session.commit()


def _record_stage_attempt(
    session: Session,
    analysis: Analysis,
    stage: str,
    attempt_number: int,
    status: str,
    error: str | None = None,
    output: str | None = None,
) -> None:
    session.add(
        StageAttempt(
            analysis_id=analysis.id,
            stage=stage,
            attempt_number=attempt_number,
            status=status,
            error=error,
            output=output,
        )
    )
    session.commit()


def _serialize_stage_output(stage: str, result: object) -> str:
    if stage == "retrieve_fixture_evidence":
        return json.dumps([asdict(item) for item in result])
    return json.dumps(asdict(result))


def _deserialize_stage_output(stage: str, raw: str) -> object:
    data = json.loads(raw)
    if stage == "classify":
        return Classification(
            label=data["label"],
            matched_rule=data["matched_rule"],
            matched_keywords=tuple(data["matched_keywords"]),
        )
    if stage == "retrieve_fixture_evidence":
        return tuple(EvidenceItem(**item) for item in data)
    if stage == "assess":
        return Assessment(**data)
    if stage == "propose":
        return ProposedAction(**data)
    if stage == "ai_inference":
        return AIResponse(**data)
    if stage == "human_review":
        return HumanReviewBoundary(**data)
    raise ValueError(f"Unknown stage: {stage}")  # pragma: no cover - exhaustive STAGE_ORDER


def _existing_succeeded_attempt(
    session: Session, analysis: Analysis, stage: str, attempt_number: int
) -> StageAttempt | None:
    return (
        session.query(StageAttempt)
        .filter_by(
            analysis_id=analysis.id,
            stage=stage,
            attempt_number=attempt_number,
            status="succeeded",
        )
        .one_or_none()
    )


def _run_stage(session, analysis, stage, attempt_number, func, *args):
    # Resume, don't re-run: a crash between two stages of the same attempt
    # leaves earlier stages already durably `succeeded` with their output
    # persisted. Redelivery must reuse that output rather than re-executing
    # the stage and inserting a second row — the database-level unique
    # constraint on (analysis_id, stage, attempt_number) makes that a hard
    # invariant, not just an application-level convention.
    existing = _existing_succeeded_attempt(session, analysis, stage, attempt_number)
    if existing is not None:
        return _deserialize_stage_output(stage, existing.output)

    analysis.current_stage = stage
    session.commit()
    try:
        result = func(*args)
    except SoftTimeLimitExceeded:
        _record_stage_attempt(session, analysis, stage, attempt_number, "timed_out")
        raise
    except Exception as exc:
        _record_stage_attempt(session, analysis, stage, attempt_number, "failed", error=str(exc))
        raise
    _record_stage_attempt(
        session,
        analysis,
        stage,
        attempt_number,
        "succeeded",
        output=_serialize_stage_output(stage, result),
    )
    return result


def _serialize_content(classification, evidence, assessment, proposal, ai_response, review) -> str:
    return json.dumps(
        {
            "ruleset_version": TRIAGE_RULESET_VERSION,
            "classification": asdict(classification),
            "evidence": [asdict(item) for item in evidence],
            "assessment": asdict(assessment),
            "proposed_action": asdict(proposal),
            "ai_inference": asdict(ai_response),
            "human_review": asdict(review),
        }
    )


def _record_ai_routing_event(
    session: Session,
    issue: Issue,
    analysis: Analysis,
    attempt_number: int,
    ai_response: AIResponse,
) -> None:
    """Record which provider served this attempt and why (routing decision
    and any fallback reason) — a visible, independently retrievable audit
    record, matching how `record_transition` already documents status
    changes. Only called when the `ai_inference` stage actually executed
    this call (see the `existing_ai_attempt` check in
    `process_workflow_run`), so redelivery never duplicates this event."""
    session.add(
        AuditEvent(
            repository_id=issue.repository_id,
            issue_id=issue.id,
            event_type=_AI_ROUTING_EVENT_TYPE,
            metadata_={
                "analysis_id": str(analysis.id),
                "attempt_number": attempt_number,
                "provider": ai_response.provider,
                "model": ai_response.model,
                "status": ai_response.status,
                "fallback_reason": ai_response.fallback_reason,
            },
        )
    )
    session.commit()


def process_workflow_run(session: Session, analysis_id: UUID) -> Analysis:
    """Execute or safely resume one durable workflow run.

    Idempotent: calling this more than once for the same analysis never
    re-runs a terminal (`completed`/`failed`/`timed_out`) run and never
    creates a second recommendation.
    """
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise WorkflowRunNotFoundError(f"Unknown analysis id: {analysis_id}")

    if analysis.status in TERMINAL_STATUSES:
        return analysis

    issue = session.get(Issue, analysis.issue_id)

    existing_recommendation = (
        session.query(Recommendation).filter_by(analysis_id=analysis.id).one_or_none()
    )
    if existing_recommendation is not None:
        analysis.status = "completed"
        session.commit()
        record_transition(session, issue, analysis, "completed")
        return analysis

    analysis.status = "running"
    session.commit()
    record_transition(session, issue, analysis, "running")

    attempt_number = analysis.attempt_count + 1
    try:
        classification = _run_stage(session, analysis, "classify", attempt_number, classify, issue)
        evidence = _run_stage(
            session,
            analysis,
            "retrieve_fixture_evidence",
            attempt_number,
            retrieve_fixture_evidence,
            issue,
            classification,
        )
        assessment = _run_stage(
            session, analysis, "assess", attempt_number, assess, issue, classification, evidence
        )
        proposal = _run_stage(
            session, analysis, "propose", attempt_number, propose, issue, classification, assessment
        )
        # Detected before `_run_stage` runs, so the routing/fallback audit
        # event below is written only when this attempt genuinely called
        # the provider (or mock) fresh — never duplicated on a resumed or
        # re-delivered task, whose `ai_inference` stage is already
        # `succeeded` and gets skipped entirely by `_run_stage`.
        ai_stage_already_succeeded = (
            _existing_succeeded_attempt(session, analysis, "ai_inference", attempt_number)
            is not None
        )
        ai_response = _run_stage(
            session,
            analysis,
            "ai_inference",
            attempt_number,
            run_ai_inference_stage,
            issue,
            classification,
            evidence,
            assessment,
            proposal,
        )
        if not ai_stage_already_succeeded:
            _record_ai_routing_event(session, issue, analysis, attempt_number, ai_response)
        review = _run_stage(
            session, analysis, "human_review", attempt_number, human_review, proposal
        )
    except SoftTimeLimitExceeded:
        analysis.attempt_count = attempt_number
        analysis.status = "timed_out"
        session.commit()
        record_transition(session, issue, analysis, "timed_out")
        return analysis
    except Exception as exc:
        analysis.attempt_count = attempt_number
        if attempt_number >= settings.triage_max_attempts:
            analysis.status = "failed"
            session.commit()
            record_transition(session, issue, analysis, "failed")
            return analysis
        analysis.status = "retrying"
        session.commit()
        record_transition(session, issue, analysis, "retrying")
        raise _RetryableWorkflowError(str(exc)) from exc

    recommendation = Recommendation(
        analysis_id=analysis.id,
        status=review.recommendation_status,
        content=_serialize_content(
            classification, evidence, assessment, proposal, ai_response, review
        ),
    )
    session.add(recommendation)
    analysis.attempt_count = attempt_number
    session.commit()

    analysis.status = "completed"
    session.commit()
    record_transition(session, issue, analysis, "completed")
    return analysis


@celery_app.task(
    bind=True,
    soft_time_limit=settings.triage_stage_timeout_seconds,
    max_retries=settings.triage_max_attempts,
    ignore_result=True,
)
def execute_triage_workflow(self, analysis_id: str) -> None:
    """Celery entry point: run (or resume) one durable workflow run.

    Bounded retries are genuine Celery retries, not an in-task sleep loop:
    `process_workflow_run` persists the `retrying` state and attempt count
    to PostgreSQL before raising `_RetryableWorkflowError`, and `self.retry`
    turns that into a new task message enqueued on the broker with a delay.
    A worker crash during the backoff window loses nothing, because nothing
    is running during that window — the retry message already lives in the
    broker independent of this process's lifetime, so any available worker
    picks it up once the countdown elapses.

    ignore_result=True: PostgreSQL, not the Celery result backend, is the
    only source of truth this app ever reads (nothing calls `.get()` or
    inspects `AsyncResult`); it also means the `throw=False` return value
    below (a `Retry` sentinel object) never gets pushed to the JSON result
    backend, which cannot serialize it.
    """
    with SessionLocal() as session:
        try:
            process_workflow_run(session, UUID(analysis_id))
        except _RetryableWorkflowError as exc:
            # throw=False: return the retry sentinel instead of raising it.
            # `self.retry(...)` re-enqueues the new task message either way
            # (that side effect doesn't depend on throw); throw=False only
            # keeps this fire-and-forget task from ever surfacing Celery's
            # retry-signaling exception to `.delay()`'s caller, which matters
            # in eager (synchronous test) mode where that caller is the HTTP
            # request handler itself.
            return self.retry(
                exc=exc, countdown=settings.triage_retry_countdown_seconds, throw=False
            )
