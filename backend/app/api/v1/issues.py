from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.api.identity import CurrentActor
from app.api.scoping import get_issue_for_tenant, issues_query_for_tenant
from app.database import get_session
from app.models.core import Issue, Repository
from app.schemas.core import (
    ErrorResponse,
    IssueDetail,
    IssueListItem,
    IssueListResponse,
    IssueRepositorySummary,
)

router = APIRouter(prefix="/issues", tags=["issues"])

# Alias, not a separate copy: `app.api.identity`'s actor-resolution
# dependency also depends on `app.database.get_session` directly, so
# overriding this one dependency (the existing test pattern) transparently
# covers identity resolution too -- see `get_session`'s docstring.
get_issue_session = get_session

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
def list_issues(session: IssueSession, actor: CurrentActor) -> IssueListResponse | JSONResponse:
    try:
        issues = (
            session.execute(
                issues_query_for_tenant(actor.organization_id)
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
def get_issue(
    issue_id: UUID, session: IssueSession, actor: CurrentActor
) -> IssueDetail | JSONResponse:
    # A cross-tenant issue id and a genuinely unknown one are
    # indistinguishable here on purpose (see ADR 0014): both come back as
    # `None` from a tenant-scoped lookup and both produce the exact same
    # 404 below, so a valid actor can never use this endpoint to learn
    # that another organization's issue id merely exists but is
    # forbidden.
    try:
        issue = get_issue_for_tenant(session, issue_id, actor.organization_id)
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
