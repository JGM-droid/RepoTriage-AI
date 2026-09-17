"""Centralized tenant-scoping query helpers (Milestone 3.1 Slice 2; see
ADR 0014).

Every helper here joins back to `Repository.tenant_id` -- the single
tenant-ownership boundary established by ADR 0013 -- rather than adding a
redundant `tenant_id` column to every downstream table. `Issue` is the one
directly-owned entry point (`Issue.repository_id`); `Analysis`,
`Recommendation`, `HumanDecision`, and `StageAttempt` are reached only by
following foreign keys from an already tenant-verified `Issue`/`Analysis`,
so a single verified entry point safely scopes everything beneath it.
`RepositoryDocument`, `RetrievalChunk`, and `AuditEvent` denormalize
`repository_id` directly (an existing Milestone 2.3/1.1 design choice) and
are scoped the same way, just one join shorter.

A resource that does not belong to the given organization behaves exactly
like a resource that does not exist: every helper returns `None`/no rows,
never a distinguishing signal a caller could use to detect that another
organization's id is merely forbidden rather than absent (see ADR 0014's
404-for-cross-tenant rationale).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.core import (
    Analysis,
    AuditEvent,
    HumanDecision,
    Issue,
    Recommendation,
    Repository,
    RepositoryDocument,
    RetrievalChunk,
    StageAttempt,
)


def issues_query_for_tenant(organization_id: UUID) -> Select:
    return (
        select(Issue)
        .join(Repository, Issue.repository_id == Repository.id)
        .where(Repository.tenant_id == organization_id)
    )


def get_issue_for_tenant(session: Session, issue_id: UUID, organization_id: UUID) -> Issue | None:
    return session.scalar(issues_query_for_tenant(organization_id).where(Issue.id == issue_id))


def get_repository_for_tenant(
    session: Session, repository_id: UUID, organization_id: UUID
) -> Repository | None:
    return session.scalar(
        select(Repository).where(
            Repository.id == repository_id, Repository.tenant_id == organization_id
        )
    )


def get_analysis_for_tenant(
    session: Session, analysis_id: UUID, organization_id: UUID
) -> Analysis | None:
    return session.scalar(
        select(Analysis)
        .join(Issue, Analysis.issue_id == Issue.id)
        .join(Repository, Issue.repository_id == Repository.id)
        .where(Analysis.id == analysis_id, Repository.tenant_id == organization_id)
    )


def get_recommendation_for_tenant(
    session: Session, recommendation_id: UUID, organization_id: UUID
) -> Recommendation | None:
    return session.scalar(
        select(Recommendation)
        .join(Analysis, Recommendation.analysis_id == Analysis.id)
        .join(Issue, Analysis.issue_id == Issue.id)
        .join(Repository, Issue.repository_id == Repository.id)
        .where(Recommendation.id == recommendation_id, Repository.tenant_id == organization_id)
    )


def get_human_decision_for_tenant(
    session: Session, decision_id: UUID, organization_id: UUID
) -> HumanDecision | None:
    return session.scalar(
        select(HumanDecision)
        .join(Recommendation, HumanDecision.recommendation_id == Recommendation.id)
        .join(Analysis, Recommendation.analysis_id == Analysis.id)
        .join(Issue, Analysis.issue_id == Issue.id)
        .join(Repository, Issue.repository_id == Repository.id)
        .where(HumanDecision.id == decision_id, Repository.tenant_id == organization_id)
    )


def repository_documents_query_for_tenant(organization_id: UUID) -> Select:
    return (
        select(RepositoryDocument)
        .join(Repository, RepositoryDocument.repository_id == Repository.id)
        .where(Repository.tenant_id == organization_id)
    )


def retrieval_chunks_query_for_tenant(organization_id: UUID) -> Select:
    return (
        select(RetrievalChunk)
        .join(Repository, RetrievalChunk.repository_id == Repository.id)
        .where(Repository.tenant_id == organization_id)
    )


def stage_attempts_query_for_tenant(organization_id: UUID) -> Select:
    return (
        select(StageAttempt)
        .join(Analysis, StageAttempt.analysis_id == Analysis.id)
        .join(Issue, Analysis.issue_id == Issue.id)
        .join(Repository, Issue.repository_id == Repository.id)
        .where(Repository.tenant_id == organization_id)
    )


def audit_events_query_for_tenant(organization_id: UUID) -> Select:
    return (
        select(AuditEvent)
        .join(Repository, AuditEvent.repository_id == Repository.id)
        .where(Repository.tenant_id == organization_id)
    )
