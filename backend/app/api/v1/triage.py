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
from app.models.core import Analysis, Issue
from app.schemas.core import ErrorResponse, TriageRequest, TriageResult, TriageStatusEvent
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
        human_review=content["human_review"] if content else None,
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
        analysis = session.scalar(
            select(Analysis)
            .where(Analysis.issue_id == issue_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc())
        )
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
