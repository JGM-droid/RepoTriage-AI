from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDTimestampMixin

# Fixed to match the selected embedding model (BAAI/bge-small-en-v1.5, see
# ADR 0009); every write and every query asserts against this constant so a
# model/config mismatch fails clearly instead of corrupting the index.
EMBEDDING_DIMENSION = 384


class Repository(UUIDTimestampMixin, Base):
    __tablename__ = "repositories"

    tenant_id: Mapped[UUID | None] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
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
        ForeignKey("repositories.id"), nullable=False, index=True
    )
    issue_id: Mapped[UUID | None] = mapped_column(ForeignKey("issues.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_: Mapped[dict[str, object] | None] = mapped_column("metadata", JSONB)
    repository: Mapped["Repository"] = relationship(back_populates="audit_events")
    issue: Mapped["Issue | None"] = relationship(back_populates="audit_events")
