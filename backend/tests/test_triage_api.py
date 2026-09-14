import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.api.v1.triage as triage_api
import app.triage.service as triage_service
from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import Analysis, Issue, Repository
from app.triage.service import TriageStageError, status_history

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for triage API integration tests.")

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

    app.dependency_overrides[get_triage_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def add_issue(
    session: Session,
    *,
    title: str = "App crash on save",
    body: str = "Traceback included",
    state: str = "open",
) -> Issue:
    repository = Repository(name="pallets/flask", source_url="https://github.com/pallets/flask")
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=1,
        title=title,
        body=body,
        state=state,
        source_url="https://github.com/pallets/flask/issues/1",
    )
    session.add(issue)
    session.commit()
    return issue


def test_start_triage_returns_completed_result_with_separated_sections(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["classification"]["label"] == "bug-crash"
    assert payload["evidence"]
    assert payload["assessment"]["severity"] == "high"
    assert payload["proposed_action"]["action"]
    assert payload["human_review"] == {
        "recommendation_status": "proposed",
        "human_review_status": "awaiting_human_review",
        "decision": None,
        "decided_by": None,
        "decided_at": None,
        "rationale": None,
    }
    assert [event["status"] for event in payload["status_history"]] == [
        "queued",
        "running",
        "completed",
    ]


def test_get_triage_returns_the_latest_result(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)
    client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    response = client.get(f"/api/v1/issues/{issue.id}/triage")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_get_triage_returns_stable_not_found_when_no_triage_has_run(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = client.get(f"/api/v1/issues/{issue.id}/triage")

    assert response.status_code == 404
    assert response.json() == {
        "error": "triage_not_found",
        "message": "No deterministic triage has been run for this issue yet.",
    }


def test_start_triage_returns_stable_not_found_for_missing_issue(client: TestClient) -> None:
    response = client.post(f"/api/v1/issues/{uuid4()}/triage", json={})

    assert response.status_code == 404
    assert response.json() == {"error": "issue_not_found", "message": "Issue not found."}


def test_get_triage_returns_stable_not_found_for_missing_issue(client: TestClient) -> None:
    response = client.get(f"/api/v1/issues/{uuid4()}/triage")

    assert response.status_code == 404
    assert response.json() == {"error": "issue_not_found", "message": "Issue not found."}


def test_start_triage_rejects_unsupported_ruleset_version(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage",
        json={"ruleset_version": "9.9"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_ruleset_version",
        "message": "The requested triage ruleset version is not supported.",
    }


def test_start_triage_returns_stable_error_on_forced_stage_failure(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error(*args, **kwargs):
        raise TriageStageError("forced failure")

    monkeypatch.setattr(triage_api, "run_triage", raise_error)
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    assert response.status_code == 500
    assert response.json() == {
        "error": "triage_failed",
        "message": "Deterministic triage failed to complete.",
    }


def test_a_forced_stage_failure_exposes_the_full_queued_running_failed_lifecycle(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error(*args, **kwargs):
        raise ValueError("forced failure")

    monkeypatch.setattr(triage_service, "classify", raise_error)
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    assert response.status_code == 500
    assert response.json() == {
        "error": "triage_failed",
        "message": "Deterministic triage failed to complete.",
    }

    analysis_id = database_session.query(Analysis).filter_by(issue_id=issue.id).one().id
    history = status_history(database_session, issue.id, analysis_id)
    assert [transition_status for transition_status, _ in history] == [
        "queued",
        "running",
        "failed",
    ]
