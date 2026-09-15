from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDTimestampMixin


class Repository(UUIDTimestampMixin, Base):
    __tablename__ = "repositories"

    tenant_id: Mapped[UUID | None] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    issues: Mapped[list["Issue"]] = relationship(back_populates="repository")
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
