from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class IssueRepositorySummary(BaseModel):
    id: UUID
    name: str
    source_url: str


class IssueListItem(BaseModel):
    id: UUID
    external_number: int
    title: str
    state: str
    source_url: str
    created_at: datetime
    updated_at: datetime
    repository: IssueRepositorySummary


class IssueDetail(IssueListItem):
    body: str


class IssueListResponse(BaseModel):
    issues: list[IssueListItem]


class ErrorResponse(BaseModel):
    error: str
    message: str


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


class TriageRequest(BaseModel):
    ruleset_version: str = "1.0"


class TriageEvidenceItem(BaseModel):
    field: str
    excerpt: str
    source_url: str


class TriageClassification(BaseModel):
    label: str
    matched_rule: str
    matched_keywords: list[str] = Field(default_factory=list)


class TriageAssessment(BaseModel):
    severity: str
    rationale: str


class TriageProposedAction(BaseModel):
    action: str
    rationale: str


class TriageHumanReview(BaseModel):
    recommendation_status: str
    human_review_status: str
    decision: str | None


class TriageStatusEvent(BaseModel):
    status: str
    created_at: datetime


class TriageResult(BaseModel):
    analysis_id: UUID
    issue_id: UUID
    status: str
    classification: TriageClassification | None
    evidence: list[TriageEvidenceItem]
    assessment: TriageAssessment | None
    proposed_action: TriageProposedAction | None
    human_review: TriageHumanReview | None
    status_history: list[TriageStatusEvent]
    created_at: datetime
    updated_at: datetime
