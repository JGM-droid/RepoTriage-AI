from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDTimestampMixin

# Fixed to match the selected embedding model (BAAI/bge-small-en-v1.5, see
# ADR 0009); every write and every query asserts against this constant so a
# model/config mismatch fails clearly instead of corrupting the index.
EMBEDDING_DIMENSION = 384

# Deterministic, well-known organization ids (Milestone 3.1 Slice 1; see ADR
# 0013). Never randomly generated -- fixed literals so migration 0005's data
# backfill, `Repository.tenant_id`'s server-side default below, and every
# test referencing them all agree on the exact same rows, forever.
# `DEFAULT_ORGANIZATION_ID` owns every repository that existed before
# Milestone 3.1 (backfilled by the migration) and every repository created
# without an explicit organization since (via the server_default) --
# preserving today's single-tenant behavior unchanged for the importer and
# every existing test. `ISOLATION_DEMO_ORGANIZATION_ID` deliberately owns no
# repository data yet; it exists only so a second, real organization is
# provably present for cross-tenant negative tests (this slice and beyond).
DEFAULT_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000101")
ISOLATION_DEMO_ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000102")

# Milestone 3.1 Slice 2 (see ADR 0014): a constrained, closed role set.
# `viewer` may only read its own organization's data; `reviewer` and
# `administrator` may additionally start triage and record a human
# decision (see the role-policy table in ADR 0014) -- this slice has no
# administrator-only capability distinct from `reviewer`, since no
# meaningful one exists yet.
ROLE_VIEWER = "viewer"
ROLE_REVIEWER = "reviewer"
ROLE_ADMINISTRATOR = "administrator"
ACTOR_ROLES = (ROLE_VIEWER, ROLE_REVIEWER, ROLE_ADMINISTRATOR)

# Deterministic, well-known actor ids (never randomly generated -- same
# rationale as the organization ids above): five demo actors seeded by
# migration 20260918_0006, three in the default organization (one per
# role) and two in the isolation-demo organization (viewer and
# administrator only -- enough to prove cross-tenant denial without a
# third role that adds no new isolation coverage).
DEFAULT_ORG_VIEWER_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000201")
DEFAULT_ORG_REVIEWER_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000202")
DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000203")
ISOLATION_ORG_VIEWER_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000204")
ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000205")


class Organization(UUIDTimestampMixin, Base):
    """The tenant boundary every other row ultimately scopes to via
    `Repository.tenant_id` (see ADR 0005's original reservation of that
    column, and ADR 0013 for this table's own design). Deliberately
    minimal: no billing, plans, domains, SSO, or settings fields -- nothing
    this milestone's slices actually need yet."""

    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    repositories: Mapped[list["Repository"]] = relationship(back_populates="organization")
    actors: Mapped[list["Actor"]] = relationship(back_populates="organization")


class Actor(UUIDTimestampMixin, Base):
    """A synthetic demo identity (Milestone 3.1 Slice 2; see ADR 0014) --
    NOT a production user account. There is no password, session, token,
    or any credential here: `X-Demo-Actor-ID` names this row directly, and
    the server trusts the database, never the request, for organization
    and role. Deliberately minimal: no email, no login history, no
    invitations -- nothing a real identity provider would own instead.
    """

    __tablename__ = "actors"
    __table_args__ = (
        CheckConstraint(
            f"role IN ('{ROLE_VIEWER}', '{ROLE_REVIEWER}', '{ROLE_ADMINISTRATOR}')",
            name="ck_actors_role",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    organization: Mapped["Organization"] = relationship(back_populates="actors")


class Repository(UUIDTimestampMixin, Base):
    __tablename__ = "repositories"

    # Milestone 3.1 Slice 1: a real, PostgreSQL-enforced organization
    # foreign key (was a bare, unenforced nullable UUID reserved by ADR
    # 0005). RESTRICT, not CASCADE, on delete: an organization can never be
    # removed while it still owns repositories -- and therefore issues,
    # analyses, and every other downstream record -- see ADR 0013. Slice 1
    # gave this column a temporary `server_default` (the deterministic
    # default org) so the pre-Slice-2 importer, with no concept of "which
    # organization is this for", could still construct valid rows. Slice 2
    # (ADR 0014) removes that default: migration 20260918_0006 drops it at
    # the database level, and every repository-creation path now passes an
    # explicit `tenant_id` -- a caller with no tenant context fails closed
    # (NOT NULL violation) instead of silently landing in the default org.
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    organization: Mapped["Organization"] = relationship(back_populates="repositories")
    issues: Mapped[list["Issue"]] = relationship(back_populates="repository")
    documents: Mapped[list["RepositoryDocument"]] = relationship(back_populates="repository")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="repository")


class Issue(UUIDTimestampMixin, Base):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("repository_id", "external_number"),)

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    external_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    repository: Mapped["Repository"] = relationship(back_populates="issues")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="issue")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="issue")
    retrieval_chunks: Mapped[list["RetrievalChunk"]] = relationship(back_populates="issue")


class Analysis(UUIDTimestampMixin, Base):
    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    issue_id: Mapped[UUID] = mapped_column(ForeignKey("issues.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    current_stage: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Milestone 3.1 Slice 2 correction (see ADR 0014): the durable,
    # immutable record of which actor initiated this analysis. Nullable
    # -- not every Analysis-creation path in this codebase goes through
    # the authenticated API (some tests and tooling construct one
    # directly) -- but the worker's own `authorize` stage treats a
    # missing reference as a hard failure, not a silent default. RESTRICT
    # (not CASCADE/SET NULL): an actor can never be deleted while it
    # still has this durable audit trail pointing at it.
    initiating_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("actors.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    issue: Mapped["Issue"] = relationship(back_populates="analyses")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="analysis")
    stage_attempts: Mapped[list["StageAttempt"]] = relationship(back_populates="analysis")


class StageAttempt(UUIDTimestampMixin, Base):
    __tablename__ = "stage_attempts"
    __table_args__ = (UniqueConstraint("analysis_id", "stage", "attempt_number"),)

    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("analyses.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    analysis: Mapped["Analysis"] = relationship(back_populates="stage_attempts")


class Recommendation(UUIDTimestampMixin, Base):
    __tablename__ = "recommendations"

    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("analyses.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped["Analysis"] = relationship(back_populates="recommendations")
    human_decisions: Mapped[list["HumanDecision"]] = relationship(back_populates="recommendation")


class HumanDecision(UUIDTimestampMixin, Base):
    __tablename__ = "human_decisions"

    recommendation_id: Mapped[UUID] = mapped_column(
        ForeignKey("recommendations.id"), nullable=False, index=True
    )
    actor_id: Mapped[UUID | None] = mapped_column(index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped["Recommendation"] = relationship(back_populates="human_decisions")


class RepositoryDocument(UUIDTimestampMixin, Base):
    """A bounded, offline documentation excerpt for one repository (Milestone
    2.3). Reuses `Repository` as the sole ownership/tenant boundary rather
    than introducing a competing scope concept."""

    __tablename__ = "repository_documents"
    __table_args__ = (UniqueConstraint("repository_id", "path"),)

    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    license_note: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    repository: Mapped["Repository"] = relationship(back_populates="documents")
    retrieval_chunks: Mapped[list["RetrievalChunk"]] = relationship(back_populates="document")


class RetrievalChunk(UUIDTimestampMixin, Base):
    """One embedded, retrievable excerpt of either a resolved `Issue` or a
    `RepositoryDocument` (Milestone 2.3). `repository_id` is denormalized
    onto every row so the repository/tenant boundary is enforceable and
    indexable directly on this table, without joining through both possible
    parents first.

    Exactly one of `issue_id`/`document_id` is set; each has its own unique
    constraint with `chunk_index` so idempotent re-ingestion of either
    source type is enforced at the database level regardless of the other
    column being NULL (a single combined unique constraint across both
    nullable columns would not reliably catch duplicates, since SQL treats
    each NULL as distinct).
    """

    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        UniqueConstraint("issue_id", "chunk_index", name="uq_retrieval_chunks_issue_chunk"),
        UniqueConstraint("document_id", "chunk_index", name="uq_retrieval_chunks_document_chunk"),
        CheckConstraint(
            "(issue_id IS NOT NULL AND document_id IS NULL) OR "
            "(issue_id IS NULL AND document_id IS NOT NULL)",
            name="ck_retrieval_chunks_exactly_one_source",
        ),
    )

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    issue_id: Mapped[UUID | None] = mapped_column(ForeignKey("issues.id"))
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("repository_documents.id"))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    external_number: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False)
    issue: Mapped["Issue | None"] = relationship(back_populates="retrieval_chunks")
    document: Mapped["RepositoryDocument | None"] = relationship(back_populates="retrieval_chunks")


class AuditEvent(UUIDTimestampMixin, Base):
    __tablename__ = "audit_events"

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "repositories.id",
            name="fk_audit_events_repository_id_repositories",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    issue_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("issues.id", name="fk_audit_events_issue_id_issues", ondelete="RESTRICT"),
        index=True,
    )
    actor_id: Mapped[UUID | None] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB)
    repository: Mapped["Repository"] = relationship(back_populates="audit_events")
    issue: Mapped["Issue | None"] = relationship(back_populates="audit_events")
