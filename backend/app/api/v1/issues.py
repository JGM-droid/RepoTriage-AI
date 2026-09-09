from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models.core import Issue, Repository
from app.schemas.core import (
    ErrorResponse,
    IssueDetail,
    IssueListItem,
    IssueListResponse,
    IssueRepositorySummary,
)

router = APIRouter(prefix="/issues", tags=["issues"])


def get_issue_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


IssueSession = Annotated[Session, Depends(get_issue_session)]


def _error_response(status_code: int, error: str, message: str) -> JSONResponse:
    payload = ErrorResponse(error=error, message=message)
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _repository_summary(repository: Repository) -> IssueRepositorySummary:
    return IssueRepositorySummary(
        id=repository.id,
        name=repository.name,
        source_url=repository.source_url,
    )


def _issue_list_item(issue: Issue) -> IssueListItem:
    return IssueListItem(
        id=issue.id,
        external_number=issue.external_number,
        title=issue.title,
        state=issue.state,
        source_url=issue.source_url,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
        repository=_repository_summary(issue.repository),
    )


def _issue_detail(issue: Issue) -> IssueDetail:
    return IssueDetail(
        **_issue_list_item(issue).model_dump(),
        body=issue.body,
    )


@router.get(
    "",
    response_model=IssueListResponse,
    responses={status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse}},
)
def list_issues(session: IssueSession) -> IssueListResponse | JSONResponse:
    try:
        issues = (
            session.execute(
                select(Issue)
                .join(Issue.repository)
                .options(joinedload(Issue.repository))
                .order_by(Repository.name, Issue.external_number, Issue.id)
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "issue_list_unavailable",
            "Imported issues are unavailable.",
        )

    return IssueListResponse(issues=[_issue_list_item(issue) for issue in issues])


@router.get(
    "/{issue_id}",
    response_model=IssueDetail,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def get_issue(issue_id: UUID, session: IssueSession) -> IssueDetail | JSONResponse:
    try:
        issue = session.scalar(
            select(Issue).options(joinedload(Issue.repository)).where(Issue.id == issue_id)
        )
    except SQLAlchemyError:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "issue_detail_unavailable",
            "The imported issue is unavailable.",
        )

    if issue is None:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "issue_not_found",
            "Issue not found.",
        )

    return _issue_detail(issue)
