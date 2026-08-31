from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RepositoryRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    name: str
    source_url: str
    created_at: datetime
    updated_at: datetime


class IssueRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository_id: UUID
    external_number: int
    title: str
    body: str
    state: str
    source_url: str
    created_at: datetime
    updated_at: datetime


class AnalysisRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    issue_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime


class RecommendationRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    analysis_id: UUID
    status: str
    content: str
    created_at: datetime
    updated_at: datetime


class HumanDecisionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    recommendation_id: UUID
    actor_id: UUID | None
    decision: str
    rationale: str | None
    created_at: datetime
    updated_at: datetime


class AuditEventRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repository_id: UUID
    issue_id: UUID | None
    actor_id: UUID | None
    event_type: str
    metadata_: dict[str, object] | None
    created_at: datetime
    updated_at: datetime
