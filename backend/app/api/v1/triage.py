import json
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.decisions.service import (
    DecisionValidationError,
    latest_decision_for_recommendation,
    record_decision,
)
from app.models.core import Analysis, Issue, Recommendation
from app.schemas.core import (
    ErrorResponse,
    TriageDecisionRequest,
    TriageHumanReview,
    TriageRequest,
    TriageResult,
    TriageStatusEvent,
)
from app.triage.rules import TRIAGE_RULESET_VERSION
from app.triage.service import TriageStageError, run_triage, status_history

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

    return TriageResult(
        analysis_id=analysis.id,
        issue_id=analysis.issue_id,
        status=analysis.status,
        classification=content["classification"] if content else None,
        evidence=content["evidence"] if content else [],
        assessment=content["assessment"] if content else None,
        proposed_action=content["proposed_action"] if content else None,
        human_review=(
            _human_review_for_recommendation(session, recommendation) if recommendation else None
        ),
        status_history=[
            TriageStatusEvent(status=transition_status, created_at=created_at)
            for transition_status, created_at in history
        ],
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )


@router.post(
    "/{issue_id}/triage",
    response_model=TriageResult,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def start_issue_triage(
    issue_id: UUID,
    session: TriageSession,
    request: TriageRequest = TriageRequest(),
) -> TriageResult | JSONResponse:
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

    try:
        analysis = run_triage(session, issue)
    except TriageStageError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_failed",
            "Deterministic triage failed to complete.",
        )
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "triage_unavailable",
            "Deterministic triage is unavailable.",
        )

    return _triage_result_from_analysis(session, analysis)


def _latest_analysis_for_issue(session: Session, issue_id: UUID) -> Analysis | None:
    return session.scalar(
        select(Analysis)
        .where(Analysis.issue_id == issue_id)
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
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
