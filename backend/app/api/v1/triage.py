import json
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.decisions.service import (
    DecisionValidationError,
    latest_decision_for_recommendation,
    record_decision,
)
from app.models.core import Analysis, Issue, Recommendation, StageAttempt
from app.schemas.core import (
    ErrorResponse,
    TriageDecisionRequest,
    TriageHumanReview,
    TriageRequest,
    TriageResult,
    TriageStageAttempt,
    TriageStartedResponse,
    TriageStatusEvent,
)
from app.triage.rules import TRIAGE_RULESET_VERSION
from app.triage.service import status_history
from app.workflow.tasks import execute_triage_workflow, record_transition

router = APIRouter(prefix="/issues", tags=["triage"])


def get_triage_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


TriageSession = Annotated[Session, Depends(get_triage_session)]


def _error_response(status_code: int, error: str, message: str) -> JSONResponse:
    payload = ErrorResponse(error=error, message=message)
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _human_review_for_recommendation(
    session: Session, recommendation: Recommendation
) -> TriageHumanReview:
    decision = latest_decision_for_recommendation(session, recommendation.id)
    return TriageHumanReview(
        recommendation_status=recommendation.status,
        human_review_status=(
            "awaiting_human_review" if recommendation.status == "proposed" else "decided"
        ),
        decision=decision.decision if decision else None,
        decided_by=decision.actor_id if decision else None,
        decided_at=decision.created_at if decision else None,
        rationale=decision.rationale if decision else None,
    )


def _triage_result_from_analysis(session: Session, analysis: Analysis) -> TriageResult:
    recommendation = analysis.recommendations[0] if analysis.recommendations else None
    content = json.loads(recommendation.content) if recommendation else None
    history = status_history(session, analysis.issue_id, analysis.id)
    attempts = (
        session.query(StageAttempt)
        .filter(StageAttempt.analysis_id == analysis.id)
        .order_by(StageAttempt.created_at, StageAttempt.id)
        .all()
    )

    return TriageResult(
        analysis_id=analysis.id,
        issue_id=analysis.issue_id,
        status=analysis.status,
        current_stage=analysis.current_stage,
        attempt_count=analysis.attempt_count,
        classification=content["classification"] if content else None,
        evidence=content["evidence"] if content else [],
        assessment=content["assessment"] if content else None,
        proposed_action=content["proposed_action"] if content else None,
        # .get, not [] — a recommendation created before Milestone 2.2/2.3
        # has no "ai_inference"/"retrieved_evidence" key in its stored
        # content; treat that as absent rather than a lookup error.
        retrieved_evidence=content.get("retrieved_evidence") if content else None,
        ai_inference=content.get("ai_inference") if content else None,
        human_review=(
            _human_review_for_recommendation(session, recommendation) if recommendation else None
        ),
        status_history=[
            TriageStatusEvent(status=transition_status, created_at=created_at)
            for transition_status, created_at in history
        ],
        stage_attempts=[
            TriageStageAttempt(
                stage=attempt.stage,
                attempt_number=attempt.attempt_number,
                status=attempt.status,
                error=attempt.error,
                created_at=attempt.created_at,
            )
            for attempt in attempts
        ],
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )


@router.post(
    "/{issue_id}/triage",
    response_model=TriageStartedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def start_issue_triage(
    issue_id: UUID,
    session: TriageSession,
    request: TriageRequest = TriageRequest(),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TriageStartedResponse | JSONResponse:
    if request.ruleset_version != TRIAGE_RULESET_VERSION:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            "invalid_ruleset_version",
            "The requested triage ruleset version is not supported.",
        )

    try:
        issue = session.scalar(select(Issue).where(Issue.id == issue_id))
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_unavailable",
            "Deterministic triage is unavailable.",
        )

    if issue is None:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "issue_not_found",
            "Issue not found.",
        )

    if idempotency_key:
        try:
            existing = session.scalar(
                select(Analysis).where(Analysis.idempotency_key == idempotency_key)
            )
        except SQLAlchemyError:
            return _error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "triage_unavailable",
                "Deterministic triage is unavailable.",
            )
        if existing is not None:
            if existing.issue_id != issue_id:
                return _error_response(
                    status.HTTP_409_CONFLICT,
                    "idempotency_key_conflict",
                    "This idempotency key is already associated with a different issue.",
                )
            return _workflow_started_response(existing)

    analysis = Analysis(issue_id=issue_id, status="queued", idempotency_key=idempotency_key)
    session.add(analysis)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(Analysis).where(Analysis.idempotency_key == idempotency_key)
        )
        if existing is not None and existing.issue_id == issue_id:
            return _workflow_started_response(existing)
        return _error_response(
            status.HTTP_409_CONFLICT,
            "idempotency_key_conflict",
            "This idempotency key is already associated with a different issue.",
        )

    session.refresh(analysis)
    record_transition(session, issue, analysis, "queued")
    execute_triage_workflow.delay(str(analysis.id))

    return _workflow_started_response(analysis)


def _workflow_started_response(analysis: Analysis) -> TriageStartedResponse:
    return TriageStartedResponse(
        analysis_id=analysis.id,
        issue_id=analysis.issue_id,
        status=analysis.status,
        poll_url=f"/api/v1/issues/{analysis.issue_id}/triage",
    )


def _latest_analysis_for_issue(session: Session, issue_id: UUID) -> Analysis | None:
    # populate_existing: this analysis is written by a background worker on a
    # separate session, so a row already cached in this session's identity
    # map (from an earlier read in the same session) must be overwritten with
    # its current durable state rather than silently reused stale.
    return session.scalar(
        select(Analysis)
        .where(Analysis.issue_id == issue_id)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        .execution_options(populate_existing=True)
    )


@router.get(
    "/{issue_id}/triage",
    response_model=TriageResult,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def get_issue_triage(issue_id: UUID, session: TriageSession) -> TriageResult | JSONResponse:
    try:
        issue = session.scalar(select(Issue).where(Issue.id == issue_id))
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_unavailable",
            "Deterministic triage is unavailable.",
        )

    if issue is None:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "issue_not_found",
            "Issue not found.",
        )

    try:
        analysis = _latest_analysis_for_issue(session, issue_id)
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_unavailable",
            "Deterministic triage is unavailable.",
        )

    if analysis is None:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "triage_not_found",
            "No deterministic triage has been run for this issue yet.",
        )

    return _triage_result_from_analysis(session, analysis)


@router.post(
    "/{issue_id}/triage/decision",
    response_model=TriageResult,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def record_issue_triage_decision(
    issue_id: UUID,
    request: TriageDecisionRequest,
    session: TriageSession,
) -> TriageResult | JSONResponse:
    try:
        issue = session.scalar(select(Issue).where(Issue.id == issue_id))
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_unavailable",
            "Deterministic triage is unavailable.",
        )

    if issue is None:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "issue_not_found",
            "Issue not found.",
        )

    try:
        analysis = _latest_analysis_for_issue(session, issue_id)
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_unavailable",
            "Deterministic triage is unavailable.",
        )

    recommendation = analysis.recommendations[0] if analysis and analysis.recommendations else None
    if recommendation is None:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "recommendation_not_found",
            "No triage recommendation exists for this issue yet.",
        )

    try:
        record_decision(session, issue, recommendation, request.decision, request.rationale)
    except DecisionValidationError as exc:
        status_code = (
            status.HTTP_409_CONFLICT
            if exc.error == "recommendation_not_proposed"
            else status.HTTP_400_BAD_REQUEST
        )
        return _error_response(status_code, exc.error, exc.message)
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_unavailable",
            "Deterministic triage is unavailable.",
        )

    session.refresh(analysis)
    return _triage_result_from_analysis(session, analysis)
