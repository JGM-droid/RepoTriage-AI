import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.decisions.service import (
    LOCAL_REVIEWER_ID,
    DecisionValidationError,
    latest_decision_for_recommendation,
    record_decision,
)
from app.models.core import AuditEvent, HumanDecision, Issue, Recommendation, Repository
from app.triage.service import run_triage

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for human decision tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def add_triaged_issue(
    session: Session,
    *,
    title: str = "App crash on startup",
    body: str = "Traceback attached below",
    state: str = "open",
    external_number: int = 1,
) -> tuple[Issue, Recommendation]:
    repository = Repository(
        name=f"pallets/flask-{external_number}",
        source_url=f"https://github.com/pallets/flask-{external_number}",
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title=title,
        body=body,
        state=state,
        source_url=f"https://github.com/pallets/flask/issues/{external_number}",
    )
    session.add(issue)
    session.commit()

    analysis = run_triage(session, issue)
    recommendation = session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    return issue, recommendation


def test_completed_triage_leaves_recommendation_proposed_with_no_decision(
    database_session: Session,
) -> None:
    _issue, recommendation = add_triaged_issue(database_session)

    assert recommendation.status == "proposed"
    assert database_session.query(HumanDecision).count() == 0


def test_record_decision_approves_and_persists_a_human_decision(
    database_session: Session,
) -> None:
    issue, recommendation = add_triaged_issue(database_session)

    decision = record_decision(database_session, issue, recommendation, "approve")

    assert decision.decision == "approve"
    assert decision.actor_id == LOCAL_REVIEWER_ID
    assert recommendation.status == "approved"
    assert database_session.query(HumanDecision).count() == 1


def test_record_decision_rejects_and_persists_a_human_decision(
    database_session: Session,
) -> None:
    issue, recommendation = add_triaged_issue(database_session)

    decision = record_decision(database_session, issue, recommendation, "reject")

    assert decision.decision == "reject"
    assert recommendation.status == "rejected"


def test_record_decision_request_revision_and_persists_a_human_decision(
    database_session: Session,
) -> None:
    issue, recommendation = add_triaged_issue(database_session)

    decision = record_decision(database_session, issue, recommendation, "request_revision")

    assert decision.decision == "request_revision"
    assert recommendation.status == "revision_requested"


def test_record_decision_rejects_an_invalid_decision_value(database_session: Session) -> None:
    issue, recommendation = add_triaged_issue(database_session)

    with pytest.raises(DecisionValidationError) as excinfo:
        record_decision(database_session, issue, recommendation, "close")

    assert excinfo.value.error == "invalid_decision"
    assert database_session.query(HumanDecision).count() == 0


def test_record_decision_cannot_overwrite_an_existing_decision(
    database_session: Session,
) -> None:
    issue, recommendation = add_triaged_issue(database_session)
    record_decision(database_session, issue, recommendation, "approve")

    with pytest.raises(DecisionValidationError) as excinfo:
        record_decision(database_session, issue, recommendation, "reject")

    assert excinfo.value.error == "recommendation_not_proposed"
    assert recommendation.status == "approved"
    assert database_session.query(HumanDecision).count() == 1
    assert database_session.query(HumanDecision).one().decision == "approve"


def test_record_decision_creates_an_audit_event(database_session: Session) -> None:
    issue, recommendation = add_triaged_issue(database_session)

    record_decision(database_session, issue, recommendation, "approve")

    events = (
        database_session.query(AuditEvent)
        .filter_by(issue_id=issue.id, event_type="triage_human_decision")
        .all()
    )
    assert len(events) == 1
    assert events[0].metadata_["decision"] == "approve"
    assert events[0].metadata_["recommendation_id"] == str(recommendation.id)
    assert events[0].actor_id == LOCAL_REVIEWER_ID


def test_latest_decision_for_recommendation_returns_the_recorded_decision(
    database_session: Session,
) -> None:
    issue, recommendation = add_triaged_issue(database_session)
    record_decision(database_session, issue, recommendation, "approve")

    decision = latest_decision_for_recommendation(database_session, recommendation.id)

    assert decision is not None
    assert decision.decision == "approve"


def test_decision_on_one_issue_never_affects_an_unrelated_recommendation(
    database_session: Session,
) -> None:
    _issue_a, recommendation_a = add_triaged_issue(
        database_session, title="Issue A", external_number=1
    )
    issue_b, recommendation_b = add_triaged_issue(
        database_session, title="Issue B", external_number=2
    )

    record_decision(database_session, issue_b, recommendation_b, "approve")

    assert recommendation_a.status == "proposed"
    assert latest_decision_for_recommendation(database_session, recommendation_a.id) is None
