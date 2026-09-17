from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RepositoryRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID  # Milestone 3.1 Slice 1: enforced non-null organization FK
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


class DemoActorSummary(BaseModel):
    """Only the fields a demo identity selector needs to display and
    submit -- never a credential, since a demo actor has none (see ADR
    0014). `id` is the exact value a client sends back as the
    `X-Demo-Actor-ID` header, not a secret."""

    id: UUID
    organization_id: UUID
    organization_name: str
    display_name: str
    role: str


class DemoActorListResponse(BaseModel):
    actors: list[DemoActorSummary]
    notice: str = (
        "These are synthetic demo identities for a portfolio walkthrough, "
        "not real user accounts. X-Demo-Actor-ID is not production "
        "authentication."
    )


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
    # Milestone 2.6: bounded even though the endpoint immediately rejects
    # any value other than the exact current TRIAGE_RULESET_VERSION -- an
    # unbounded string would otherwise be fully parsed and held in memory
    # before that comparison ever runs. 20 characters is generous headroom
    # for any realistic version string (the real value is "1.0").
    ruleset_version: str = Field(default="1.0", max_length=20)


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


class TriageAIInference(BaseModel):
    """AI-generated narrative supplementing the deterministic recommendation.

    Never a substitute for `classification`/`assessment`/`proposed_action`
    (all deterministic) or `human_review` (an explicit human decision).
    Extra stored fields such as latency are intentionally not exposed here.

    `prompt_id`/`prompt_version`/`prompt_status`/`prompt_template_hash`/
    `rendered_prompt_hash` are prompt-registry provenance (ADR 0010) —
    included for technical/audit inspection via the API; the frontend's
    normal UI surfaces only `prompt_id`/`prompt_version`, not the hashes
    or the rendered prompt text itself (never returned by this API).

    `redaction_events`/`redaction_policy_id`/`redaction_policy_version`/
    `redaction_policy_status`/`redaction_policy_hash` are redaction-policy
    provenance (Milestone 2.6, ADR 0012) — pattern *names* only, never a
    matched secret value; always present, even when `redaction_events` is
    empty, so "nothing was redacted" is attributable to a specific policy
    version. Included for technical/audit inspection only; no frontend
    display is required or expected.
    """

    narrative: str
    status: str
    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    prompt_status: str
    prompt_template_hash: str
    rendered_prompt_hash: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    fallback_reason: str | None = None
    citations: list[str] = Field(default_factory=list)
    redaction_events: list[str] = Field(default_factory=list)
    redaction_policy_id: str = ""
    redaction_policy_version: str = ""
    redaction_policy_status: str = ""
    redaction_policy_hash: str = ""


class TriageRetrievedRecord(BaseModel):
    identifier: str
    source_type: str
    external_number: int | None
    title: str
    excerpt: str
    source_url: str
    similarity_score: float
    relevance_explanation: str


class TriageRetrievedEvidence(BaseModel):
    """Repository-grounded evidence retrieved for this analysis (Milestone
    2.3) — structurally separate from the selected issue's own `evidence`
    and from the AI-generated narrative; it only ever enriches the latter's
    prompt, never the deterministic sections."""

    items: list[TriageRetrievedRecord]
    query_summary: str
    candidates_considered: int
    status: str
    failure_reason: str | None = None


class TriageHumanReview(BaseModel):
    recommendation_status: str
    human_review_status: str
    decision: str | None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    rationale: str | None = None


class TriageDecisionRequest(BaseModel):
    # Milestone 2.6: bounded even though app.decisions.service.record_decision
    # rejects any value outside ALLOWED_DECISIONS ("approve"/"reject"/
    # "request_revision", longest 17 characters) -- an unbounded string
    # would otherwise be fully parsed and held in memory before that
    # service-layer check ever runs. 32 characters is generous headroom.
    decision: str = Field(max_length=32)
    rationale: str | None = Field(default=None, max_length=1000)


class TriageStatusEvent(BaseModel):
    status: str
    created_at: datetime


class TriageStageAttempt(BaseModel):
    stage: str
    attempt_number: int
    status: str
    error: str | None
    created_at: datetime


class TriageResult(BaseModel):
    analysis_id: UUID
    issue_id: UUID
    status: str
    current_stage: str | None
    attempt_count: int
    classification: TriageClassification | None
    evidence: list[TriageEvidenceItem]
    assessment: TriageAssessment | None
    proposed_action: TriageProposedAction | None
    retrieved_evidence: TriageRetrievedEvidence | None = None
    ai_inference: TriageAIInference | None = None
    human_review: TriageHumanReview | None
    status_history: list[TriageStatusEvent]
    stage_attempts: list[TriageStageAttempt]
    created_at: datetime
    updated_at: datetime


class TriageStartedResponse(BaseModel):
    analysis_id: UUID
    issue_id: UUID
    status: str
    poll_url: str
