"""Deterministic triage workflow orchestration.

Stages run in-process and synchronously within a single request in this
milestone (no background worker or queue; see
docs/adr/0004-defer-async-workflow-technology-selection.md). Execution
remains synchronous: a single HTTP call still only returns the terminal
`completed` or `failed` status. The queued/running/completed/failed
lifecycle is nonetheless genuinely persisted, one committed AuditEvent per
transition, so the full history is retrievable after the request completes
via the issue's audit trail (see `status_history` below and in the triage
API). This reuses the existing AuditEvent model instead of adding a
migration, queue, or polling infrastructure.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.core import Analysis, AuditEvent, Issue, Recommendation
from app.triage.rules import (
    TRIAGE_RULESET_VERSION,
    Assessment,
    Classification,
    EvidenceItem,
    HumanReviewBoundary,
    ProposedAction,
    assess,
    classify,
    human_review,
    propose,
    retrieve_fixture_evidence,
)

TRIAGE_STATUS_EVENT_TYPE = "triage_status_transition"


class TriageStageError(RuntimeError):
    """Raised when a deterministic triage stage fails to execute."""


def _record_transition(
    session: Session, issue: Issue, analysis: Analysis, transition_status: str
) -> None:
    """Persist a truthful, independently retrievable status-transition record."""
    session.add(
        AuditEvent(
            repository_id=issue.repository_id,
            issue_id=issue.id,
            event_type=TRIAGE_STATUS_EVENT_TYPE,
            metadata_={"analysis_id": str(analysis.id), "status": transition_status},
        )
    )
    session.commit()


def status_history(session: Session, issue_id: UUID, analysis_id: UUID) -> list[tuple[str, object]]:
    """Return the persisted (status, created_at) transitions for one analysis, in order."""
    events = (
        session.query(AuditEvent)
        .filter(
            AuditEvent.issue_id == issue_id,
            AuditEvent.event_type == TRIAGE_STATUS_EVENT_TYPE,
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .all()
    )
    return [
        (event.metadata_["status"], event.created_at)
        for event in events
        if event.metadata_ and event.metadata_.get("analysis_id") == str(analysis_id)
    ]


def start_triage(session: Session, issue: Issue) -> Analysis:
    """Create the queued analysis record and record its transition. Stages have not run."""
    analysis = Analysis(issue_id=issue.id, status="queued")
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    _record_transition(session, issue, analysis, "queued")
    return analysis


def _serialize_content(
    classification: Classification,
    evidence: tuple[EvidenceItem, ...],
    assessment: Assessment,
    proposal: ProposedAction,
    review: HumanReviewBoundary,
) -> str:
    return json.dumps(
        {
            "ruleset_version": TRIAGE_RULESET_VERSION,
            "classification": asdict(classification),
            "evidence": [asdict(item) for item in evidence],
            "assessment": asdict(assessment),
            "proposed_action": asdict(proposal),
            "human_review": asdict(review),
        }
    )


def execute_triage(session: Session, analysis: Analysis, issue: Issue) -> Analysis:
    """Run classify -> retrieve_fixture_evidence -> assess -> propose -> human_review in order.

    A completed run only ever produces a proposal awaiting human review. It
    never creates, implies, or infers a human decision; that boundary is
    enforced by never writing a HumanDecision record here.
    """
    analysis.status = "running"
    session.commit()
    _record_transition(session, issue, analysis, "running")

    try:
        classification = classify(issue)
        evidence = retrieve_fixture_evidence(issue, classification)
        assessment = assess(issue, classification, evidence)
        proposal = propose(issue, classification, assessment)
        review = human_review(proposal)
    except Exception as exc:
        analysis.status = "failed"
        session.commit()
        _record_transition(session, issue, analysis, "failed")
        raise TriageStageError("Deterministic triage stage execution failed.") from exc

    recommendation = Recommendation(
        analysis_id=analysis.id,
        status=review.recommendation_status,
        content=_serialize_content(classification, evidence, assessment, proposal, review),
    )
    session.add(recommendation)
    session.commit()
    session.refresh(recommendation)
    if recommendation.status != review.recommendation_status:
        # Deterministically confirm the persisted recommendation remains proposed,
        # awaiting review; never approved, rejected, or revised by this workflow.
        analysis.status = "failed"
        session.commit()
        _record_transition(session, issue, analysis, "failed")
        raise TriageStageError("Recommendation status diverged from the human_review boundary.")

    analysis.status = "completed"
    session.commit()
    session.refresh(analysis)
    _record_transition(session, issue, analysis, "completed")
    return analysis


def run_triage(session: Session, issue: Issue) -> Analysis:
    """Start and execute deterministic triage for an imported issue."""
    analysis = start_triage(session, issue)
    return execute_triage(session, analysis, issue)
