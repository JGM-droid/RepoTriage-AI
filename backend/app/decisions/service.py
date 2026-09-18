"""Human decision recording for a proposed deterministic triage recommendation.

Enforces the mandatory human-review boundary (see
docs/adr/0003-mandatory-human-review-boundary.md): a recommendation stays
`proposed` until an explicit human reviewer decision is recorded here.
Nothing in the triage workflow may call this module to auto-approve.

`actor_id` is required, not defaulted: the caller (the API layer, see
`app.api.v1.triage`) must supply the real, backend-resolved actor id from
`app.api.identity` -- Milestone 3.1 Slice 2 added synthetic demo
identity/RBAC (see ADR 0014), so this module no longer silently attributes
every decision to one fixed placeholder reviewer. `LOCAL_REVIEWER_ID` is
kept only as the pre-Slice-2 fixed identifier (ADR 0005's original
single-local-reviewer placeholder), still usable as an explicit actor id
by tooling/tests with no real actor context of their own.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.core import AuditEvent, HumanDecision, Issue, Recommendation
from app.observability import record_human_decision, span

LOCAL_REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000001")

ALLOWED_DECISIONS = ("approve", "reject", "request_revision")

RECOMMENDATION_STATUS_BY_DECISION = {
    "approve": "approved",
    "reject": "rejected",
    "request_revision": "revision_requested",
}

DECISION_AUDIT_EVENT_TYPE = "triage_human_decision"


class DecisionValidationError(ValueError):
    """Raised when a decision request cannot be satisfied. Message is safe to expose."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


def latest_decision_for_recommendation(
    session: Session, recommendation_id: UUID
) -> HumanDecision | None:
    """Return the recorded decision for a recommendation, if any."""
    return (
        session.query(HumanDecision)
        .filter(HumanDecision.recommendation_id == recommendation_id)
        .order_by(HumanDecision.created_at, HumanDecision.id)
        .first()
    )


def record_decision(
    session: Session,
    issue: Issue,
    recommendation: Recommendation,
    decision: str,
    actor_id: UUID,
    rationale: str | None = None,
) -> HumanDecision:
    """Record an explicit human decision for a still-proposed recommendation.

    Raises DecisionValidationError for an unsupported decision value or a
    recommendation that is no longer proposed (already decided), so a
    conflicting request can never silently overwrite the first decision.
    `actor_id` is the real, backend-resolved actor recording this decision
    (see `app.api.identity`) -- never a value the caller merely claims.
    """
    if decision not in ALLOWED_DECISIONS:
        raise DecisionValidationError(
            "invalid_decision",
            "The decision must be one of approve, reject, or request_revision.",
        )

    if recommendation.status != "proposed":
        raise DecisionValidationError(
            "recommendation_not_proposed",
            "This recommendation already has a recorded human decision.",
        )

    with span("human_decision.persist", attributes={"decision": decision}):
        human_decision = HumanDecision(
            recommendation_id=recommendation.id,
            actor_id=actor_id,
            decision=decision,
            rationale=rationale,
        )
        session.add(human_decision)

        recommendation.status = RECOMMENDATION_STATUS_BY_DECISION[decision]

        session.add(
            AuditEvent(
                repository_id=issue.repository_id,
                issue_id=issue.id,
                actor_id=actor_id,
                event_type=DECISION_AUDIT_EVENT_TYPE,
                metadata_={
                    "recommendation_id": str(recommendation.id),
                    "analysis_id": str(recommendation.analysis_id),
                    "correlation_id": recommendation.analysis.correlation_id,
                    "decision": decision,
                },
            )
        )

        session.commit()
        session.refresh(human_decision)
        record_human_decision(decision)
        return human_decision
