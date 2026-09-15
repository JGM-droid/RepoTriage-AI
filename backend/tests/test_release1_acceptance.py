"""Release 1 exit-gate acceptance test (Milestone 1.6).

Proves that a fresh, freely reproducible local run completes the full
primary workflow — import -> browse -> analyze -> review -> decision —
using only deterministic, offline logic. No paid AI provider, network
call, or external service is used anywhere in this path.

This is the automated counterpart to the manual "fresh local run"
acceptance walkthrough documented in README.md.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.v1.issues import get_issue_session
from app.api.v1.triage import get_triage_session
from app.importer.service import import_fixture
from app.main import app

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for the Release 1 acceptance test.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture()
def client(database_session: Session) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        yield database_session

    app.dependency_overrides[get_issue_session] = override_session
    app.dependency_overrides[get_triage_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_release_1_workflow_completes_end_to_end_without_a_paid_provider(
    client: TestClient, database_session: Session
) -> None:
    # Import: offline, bounded, committed fixture; no network call.
    summary = import_fixture(database_session)
    assert summary.considered == 100
    assert summary.inserted == 100

    # Browse: list and detail.
    list_response = client.get("/api/v1/issues")
    assert list_response.status_code == 200
    issues = list_response.json()["issues"]
    assert len(issues) == 100

    issue_id = issues[0]["id"]
    detail_response = client.get(f"/api/v1/issues/{issue_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == issue_id

    # Analyze: deterministic classify -> retrieve_fixture_evidence -> assess
    # -> propose -> human_review, executed durably in a background worker
    # (Celery runs in eager/synchronous test mode; see conftest.py) with no
    # AI/LLM call. The start endpoint returns 202 immediately.
    start_response = client.post(f"/api/v1/issues/{issue_id}/triage", json={})
    assert start_response.status_code == 202

    # Review: poll the status endpoint for the completed, separated result.
    review_response = client.get(f"/api/v1/issues/{issue_id}/triage")
    assert review_response.status_code == 200
    triage_payload = review_response.json()
    assert triage_payload["status"] == "completed"
    assert triage_payload["classification"] is not None
    assert triage_payload["evidence"]
    assert triage_payload["assessment"] is not None
    assert triage_payload["proposed_action"] is not None
    assert triage_payload["human_review"]["recommendation_status"] == "proposed"
    assert triage_payload["human_review"]["decision"] is None

    # Decision: an explicit human decision, never inferred from model output.
    decision_response = client.post(
        f"/api/v1/issues/{issue_id}/triage/decision",
        json={"decision": "approve"},
    )
    assert decision_response.status_code == 200
    decision_payload = decision_response.json()
    assert decision_payload["human_review"]["decision"] == "approve"
    assert decision_payload["human_review"]["recommendation_status"] == "approved"
    assert decision_payload["human_review"]["decided_by"] is not None

    # The recorded decision is durably retrievable afterward.
    final_response = client.get(f"/api/v1/issues/{issue_id}/triage")
    assert final_response.json()["human_review"]["decision"] == "approve"
