"""Celery task and durable orchestration for the deterministic triage workflow.

Executes authorize -> classify -> retrieve_fixture_evidence -> assess ->
propose -> authorize_before_retrieval -> retrieve_related_evidence ->
authorize_before_ai_inference -> ai_inference -> human_review ->
authorize_before_persistence in a background worker (Milestone 2.1;
`ai_inference` added in Milestone 2.2, see ADR 0008;
`retrieve_related_evidence` added in Milestone 2.3, see ADR 0009;
`authorize` and its three re-authorization checkpoints added in Milestone
3.1 Slice 2's correction, see ADR 0014) instead of inside the HTTP
request. `retrieve_related_evidence` looks up bounded, same-repository,
resolved evidence (via pgvector) and hands only its selected top results
to `ai_inference`; `ai_inference` supplements the deterministic
classification, severity, and proposed action with an AI-generated
narrative grounded in that evidence — neither stage ever changes
classification/severity/proposed_action, and neither creates a
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

Authorization is dynamic state, not a fact that stays true once observed
-- so it is never treated as a resumable, cacheable stage result the way
`classify`'s or `assess`'s deterministic output is. A single canonical
guard function, `_authorize_workflow_run`, re-resolves the initiating
actor and the full ownership chain from PostgreSQL and is called at
**four** separate points in every attempt: once at the very start
(`authorize`), and again immediately before each of the three
tenant-sensitive actions that follow it -- `retrieve_related_evidence`
(`authorize_before_retrieval`), `ai_inference`
(`authorize_before_ai_inference`), and `Recommendation` persistence
(`authorize_before_persistence`). Each checkpoint is its own distinctly
-named stage, so even a same-attempt crash-and-resume (which reuses an
earlier stage's cached "succeeded" output rather than recomputing it,
exactly like every other stage) still forces the *next* checkpoint --
whichever one this attempt has not yet executed -- to re-verify current
state fresh; a previously successful `authorize` (or any later
checkpoint) can never be used to wave through a later one. The Celery
payload (`execute_triage_workflow`'s sole argument) is, and remains,
just the analysis id: no tenant id, actor id, or role ever crosses into
the task message. A checkpoint failure blocks every action after it --
retrieval, AI inference, and recommendation creation -- exactly like any
other stage failure, through the same bounded-retry/terminal-failure
machinery, observable as an ordinary failed `StageAttempt` row. Note
what this does *not* claim: there is no transactional protection across
the external AI provider call itself (a real network call cannot be
wrapped in a database transaction) -- which is exactly why authorization
is checked again immediately *before* that call, and a fourth time
immediately before its result is persisted, rather than assumed to still
hold from an earlier check.
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
from app.models.core import (
    ROLE_ADMINISTRATOR,
    ROLE_REVIEWER,
    Actor,
    Analysis,
    AuditEvent,
    Issue,
    Recommendation,
    Repository,
    StageAttempt,
)
from app.retrieval.contracts import RetrievalResult, RetrievedRecord
from app.retrieval.stage import run_retrieve_related_evidence_stage
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
    "authorize",
    "classify",
    "retrieve_fixture_evidence",
    "assess",
    "propose",
    "authorize_before_retrieval",
    "retrieve_related_evidence",
    "authorize_before_ai_inference",
    "ai_inference",
    "human_review",
    "authorize_before_persistence",
)
# Every one of these calls the same canonical `_authorize_workflow_run`
# guard (see that function's docstring) -- never a duplicated check.
# Kept as their own set for `_serialize_stage_output`/
# `_deserialize_stage_output`'s dispatch below.
_AUTHORIZATION_STAGES = (
    "authorize",
    "authorize_before_retrieval",
    "authorize_before_ai_inference",
    "authorize_before_persistence",
)
TERMINAL_STATUSES = ("completed", "failed", "timed_out")
_STATUS_EVENT_TYPE = "triage_status_transition"
_AI_ROUTING_EVENT_TYPE = "ai_inference_routing"
_RETRIEVAL_EVENT_TYPE = "retrieval_evidence"

settings = get_settings()


class WorkflowRunNotFoundError(LookupError):
    """Raised when a workflow task references an unknown analysis id."""


class WorkflowAuthorizationError(RuntimeError):
    """Raised by the `authorize` stage when the worker's own durable
    re-validation of the initiating actor and tenant-ownership chain
    fails. Never a trust decision made from the Celery payload -- only
    `analysis_id` ever crosses that boundary; everything checked here is
    freshly re-resolved from PostgreSQL on every attempt. The message is
    always a fixed, generic string (see `_authorize_workflow_run`): it
    names no actor, organization, or content, so it is always safe to
    persist as a `StageAttempt.error` value."""


class _RetryableWorkflowError(RuntimeError):
    """Raised only to trigger a bounded Celery-level retry.

    By the time this is raised, PostgreSQL already reflects the
    `retrying` status and the failed stage attempt.
    """


def _authorize_workflow_run(session: Session, analysis: Analysis, issue: Issue) -> None:
    """The worker's own durable re-validation -- re-run on every attempt,
    trusting nothing but `analysis.initiating_actor_id` and PostgreSQL.
    Raises `WorkflowAuthorizationError` (a fixed, generic message, never
    including actor/organization/content details) the moment any of the
    following no longer holds:

      1. an initiating actor reference was recorded at all;
      2. that actor still exists;
      3. that actor is still enabled;
      4. that actor's role still permits initiating triage;
      5. the issue's repository still exists (ownership-chain sanity);
      6. that repository's organization still matches the actor's own.

    Checked in this order so the first failing condition is what's
    reported; all six are cheap, single-row lookups against already
    -indexed columns."""
    if analysis.initiating_actor_id is None:
        raise WorkflowAuthorizationError(
            "This analysis has no recorded initiating actor and cannot proceed."
        )

    actor = session.get(Actor, analysis.initiating_actor_id)
    if actor is None or not actor.is_enabled:
        raise WorkflowAuthorizationError(
            "The initiating actor is unknown or disabled; cannot proceed."
        )

    if actor.role not in (ROLE_REVIEWER, ROLE_ADMINISTRATOR):
        raise WorkflowAuthorizationError(
            "The initiating actor's role no longer permits triage; cannot proceed."
        )

    repository = session.get(Repository, issue.repository_id)
    if repository is None:
        raise WorkflowAuthorizationError(
            "This issue's repository is missing; ownership chain is inconsistent."
        )

    if repository.tenant_id != actor.organization_id:
        raise WorkflowAuthorizationError(
            "The initiating actor no longer belongs to this analysis's organization."
        )


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
    if stage in _AUTHORIZATION_STAGES:
        return json.dumps(None)  # side-effect only: success means "no exception raised"
    if stage == "retrieve_fixture_evidence":
        return json.dumps([asdict(item) for item in result])
    return json.dumps(asdict(result))


def _deserialize_stage_output(stage: str, raw: str) -> object:
    data = json.loads(raw)
    if stage in _AUTHORIZATION_STAGES:
        return None
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
    if stage == "retrieve_related_evidence":
        return RetrievalResult(
            items=tuple(RetrievedRecord(**item) for item in data["items"]),
            query_summary=data["query_summary"],
            candidates_considered=data["candidates_considered"],
            total_excerpt_chars=data["total_excerpt_chars"],
            mechanism=data["mechanism"],
            status=data["status"],
            failure_reason=data.get("failure_reason"),
        )
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


def _serialize_content(
    classification, evidence, assessment, proposal, retrieval_result, ai_response, review
) -> str:
    return json.dumps(
        {
            "ruleset_version": TRIAGE_RULESET_VERSION,
            "classification": asdict(classification),
            "evidence": [asdict(item) for item in evidence],
            "assessment": asdict(assessment),
            "proposed_action": asdict(proposal),
            "retrieved_evidence": asdict(retrieval_result),
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
                "prompt_id": ai_response.prompt_id,
                "prompt_version": ai_response.prompt_version,
                "prompt_status": ai_response.prompt_status,
                "prompt_template_hash": ai_response.prompt_template_hash,
                "rendered_prompt_hash": ai_response.rendered_prompt_hash,
                # Pattern names only (e.g. "openai_api_key") -- never the
                # matched value itself. See app.ai_gateway.redaction. Policy
                # id/version/status/hash are always present, even when
                # `redaction_events` is empty, so "no redaction occurred" is
                # attributable to a specific, historically-resolvable policy
                # version rather than ambiguous with "no policy ran".
                "redaction_events": list(ai_response.redaction_events),
                "redaction_policy_id": ai_response.redaction_policy_id,
                "redaction_policy_version": ai_response.redaction_policy_version,
                "redaction_policy_status": ai_response.redaction_policy_status,
                "redaction_policy_hash": ai_response.redaction_policy_hash,
            },
        )
    )
    session.commit()


def _record_retrieval_event(
    session: Session,
    issue: Issue,
    analysis: Analysis,
    attempt_number: int,
    retrieval_result: RetrievalResult,
) -> None:
    """Record the retrieval decision: mechanism, candidate/selected counts,
    context size, and which sources were actually selected — identifiers
    only, never full excerpts/bodies. Only called when this attempt's
    `retrieve_related_evidence` stage actually executed fresh (mirrors
    `_record_ai_routing_event`'s resume-safety), so redelivery never
    duplicates this event."""
    session.add(
        AuditEvent(
            repository_id=issue.repository_id,
            issue_id=issue.id,
            event_type=_RETRIEVAL_EVENT_TYPE,
            metadata_={
                "analysis_id": str(analysis.id),
                "attempt_number": attempt_number,
                "repository_id": str(issue.repository_id),
                "mechanism": retrieval_result.mechanism,
                "candidates_considered": retrieval_result.candidates_considered,
                "selected_count": len(retrieval_result.items),
                "total_excerpt_chars": retrieval_result.total_excerpt_chars,
                "selected_identifiers": [item.identifier for item in retrieval_result.items],
                "status": retrieval_result.status,
                "failure_reason": retrieval_result.failure_reason,
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
        _run_stage(
            session,
            analysis,
            "authorize",
            attempt_number,
            _authorize_workflow_run,
            session,
            analysis,
            issue,
        )
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
        # Detected before `_run_stage` runs, so each audit event below is
        # written only when this attempt genuinely executed that stage
        # fresh — never duplicated on a resumed or re-delivered task, whose
        # stage is already `succeeded` and gets skipped entirely by
        # `_run_stage`.
        retrieval_already_succeeded = (
            _existing_succeeded_attempt(
                session, analysis, "retrieve_related_evidence", attempt_number
            )
            is not None
        )
        _run_stage(
            session,
            analysis,
            "authorize_before_retrieval",
            attempt_number,
            _authorize_workflow_run,
            session,
            analysis,
            issue,
        )
        retrieval_result = _run_stage(
            session,
            analysis,
            "retrieve_related_evidence",
            attempt_number,
            run_retrieve_related_evidence_stage,
            session,
            issue,
            classification,
        )
        if not retrieval_already_succeeded:
            _record_retrieval_event(session, issue, analysis, attempt_number, retrieval_result)

        ai_stage_already_succeeded = (
            _existing_succeeded_attempt(session, analysis, "ai_inference", attempt_number)
            is not None
        )
        _run_stage(
            session,
            analysis,
            "authorize_before_ai_inference",
            attempt_number,
            _authorize_workflow_run,
            session,
            analysis,
            issue,
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
            retrieval_result.items,
        )
        if not ai_stage_already_succeeded:
            _record_ai_routing_event(session, issue, analysis, attempt_number, ai_response)
        review = _run_stage(
            session, analysis, "human_review", attempt_number, human_review, proposal
        )
        # Last checkpoint: re-verified immediately before the
        # Recommendation is built and persisted below -- a permission
        # change after retrieval/AI inference succeeded but before this
        # point must still block persistence.
        _run_stage(
            session,
            analysis,
            "authorize_before_persistence",
            attempt_number,
            _authorize_workflow_run,
            session,
            analysis,
            issue,
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
            classification, evidence, assessment, proposal, retrieval_result, ai_response, review
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
