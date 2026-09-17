import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    AuditEvent,
    HumanDecision,
    Issue,
    Repository,
)
from tests.db_maintenance import truncate_for_test

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for human decision API integration tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            truncate_for_test(connection, TRUNCATE_CORE_TABLES)
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture()
def client(database_session: Session) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        yield database_session

    app.dependency_overrides[get_triage_session] = override_session
    try:
        # Reviewer role: this file starts triage and records decisions,
        # both of which require reviewer/administrator (Milestone 3.1
        # Slice 2; see ADR 0014).
        yield TestClient(app, headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)})
    finally:
        app.dependency_overrides.clear()


def add_issue(
    session: Session,
    *,
    title: str = "App crash on save",
    body: str = "Traceback included",
    state: str = "open",
    external_number: int = 1,
) -> Issue:
    repository = Repository(
        tenant_id=DEFAULT_ORGANIZATION_ID,
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
    return issue


def triage_then_decide(client: TestClient, issue_id, decision: str, rationale: str | None = None):
    client.post(f"/api/v1/issues/{issue_id}/triage", json={})
    body = {"decision": decision}
    if rationale is not None:
        body["rationale"] = rationale
    return client.post(f"/api/v1/issues/{issue_id}/triage/decision", json=body)


def test_approve_decision_is_recorded_and_returned(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = triage_then_decide(client, issue.id, "approve", rationale="Looks correct.")

    assert response.status_code == 200
    payload = response.json()
    assert payload["human_review"]["recommendation_status"] == "approved"
    assert payload["human_review"]["human_review_status"] == "decided"
    assert payload["human_review"]["decision"] == "approve"
    assert payload["human_review"]["rationale"] == "Looks correct."
    assert payload["human_review"]["decided_by"] is not None
    assert payload["human_review"]["decided_at"] is not None


def test_reject_decision_is_recorded_and_returned(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = triage_then_decide(client, issue.id, "reject")

    assert response.status_code == 200
    payload = response.json()
    assert payload["human_review"]["recommendation_status"] == "rejected"
    assert payload["human_review"]["decision"] == "reject"


def test_request_revision_decision_is_recorded_and_returned(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = triage_then_decide(client, issue.id, "request_revision")

    assert response.status_code == 200
    payload = response.json()
    assert payload["human_review"]["recommendation_status"] == "revision_requested"
    assert payload["human_review"]["decision"] == "request_revision"


def test_decision_rejects_an_invalid_decision_value(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)
    client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage/decision",
        json={"decision": "close"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_decision",
        "message": "The decision must be one of approve, reject, or request_revision.",
    }


def test_an_oversized_decision_value_is_rejected_safely(
    client: TestClient, database_session: Session
) -> None:
    """Milestone 2.6: TriageDecisionRequest.decision is now bounded --
    proves the bound is enforced (422) before it would even reach
    app.decisions.service's ALLOWED_DECISIONS check, and that the error
    body stays safe (no stack trace, no internal representation)."""
    issue = add_issue(database_session)
    client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage/decision",
        json={"decision": "x" * 33},
    )

    assert response.status_code == 422
    assert "Traceback" not in response.text
    assert "site-packages" not in response.text


def test_decision_returns_stable_not_found_for_missing_issue(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/issues/{uuid4()}/triage/decision",
        json={"decision": "approve"},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "issue_not_found", "message": "Issue not found."}


def test_decision_returns_stable_not_found_when_no_triage_has_run(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage/decision",
        json={"decision": "approve"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": "recommendation_not_found",
        "message": "No triage recommendation exists for this issue yet.",
    }


def test_a_second_decision_cannot_overwrite_the_first(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)
    first = triage_then_decide(client, issue.id, "approve")
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/issues/{issue.id}/triage/decision",
        json={"decision": "reject"},
    )

    assert second.status_code == 409
    assert second.json() == {
        "error": "recommendation_not_proposed",
        "message": "This recommendation already has a recorded human decision.",
    }

    follow_up = client.get(f"/api/v1/issues/{issue.id}/triage")
    assert follow_up.json()["human_review"]["decision"] == "approve"
    assert database_session.query(HumanDecision).count() == 1


def test_a_decision_on_one_issue_does_not_affect_another_issue(
    client: TestClient, database_session: Session
) -> None:
    issue_a = add_issue(database_session, title="Issue A", external_number=1)
    issue_b = add_issue(database_session, title="Issue B", external_number=2)
    client.post(f"/api/v1/issues/{issue_a.id}/triage", json={})
    client.post(f"/api/v1/issues/{issue_b.id}/triage", json={})

    client.post(f"/api/v1/issues/{issue_b.id}/triage/decision", json={"decision": "approve"})

    unaffected = client.get(f"/api/v1/issues/{issue_a.id}/triage")
    assert unaffected.json()["human_review"]["recommendation_status"] == "proposed"
    assert unaffected.json()["human_review"]["decision"] is None


def test_decision_creates_an_audit_event(client: TestClient, database_session: Session) -> None:
    issue = add_issue(database_session)

    triage_then_decide(client, issue.id, "approve")

    events = (
        database_session.query(AuditEvent)
        .filter_by(issue_id=issue.id, event_type="triage_human_decision")
        .all()
    )
    assert len(events) == 1


def test_retrieval_exposes_the_recorded_decision(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)
    triage_then_decide(client, issue.id, "reject", rationale="Not applicable.")

    response = client.get(f"/api/v1/issues/{issue.id}/triage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["human_review"]["decision"] == "reject"
    assert payload["human_review"]["rationale"] == "Not applicable."
    assert payload["human_review"]["recommendation_status"] == "rejected"
