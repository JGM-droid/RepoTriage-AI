"""Human decision recording for a proposed deterministic triage recommendation.

Enforces the mandatory human-review boundary (see
docs/adr/0003-mandatory-human-review-boundary.md): a recommendation stays
`proposed` until an explicit local reviewer decision is recorded here.
Nothing in the triage workflow may call this module to auto-approve.

This milestone has no authentication or RBAC (see ADR 0005, deferred to a
later release). The single local reviewer is represented by a fixed,
well-known identifier rather than a real user account.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.core import AuditEvent, HumanDecision, Issue, Recommendation

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
    rationale: str | None = None,
) -> HumanDecision:
    """Record an explicit human decision for a still-proposed recommendation.

    Raises DecisionValidationError for an unsupported decision value or a
    recommendation that is no longer proposed (already decided), so a
    conflicting request can never silently overwrite the first decision.
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

    human_decision = HumanDecision(
        recommendation_id=recommendation.id,
        actor_id=LOCAL_REVIEWER_ID,
        decision=decision,
        rationale=rationale,
    )
    session.add(human_decision)

    recommendation.status = RECOMMENDATION_STATUS_BY_DECISION[decision]

    session.add(
        AuditEvent(
            repository_id=issue.repository_id,
            issue_id=issue.id,
            actor_id=LOCAL_REVIEWER_ID,
            event_type=DECISION_AUDIT_EVENT_TYPE,
            metadata_={
                "recommendation_id": str(recommendation.id),
                "analysis_id": str(recommendation.analysis_id),
                "decision": decision,
            },
        )
    )

    session.commit()
    session.refresh(human_decision)
    return human_decision
