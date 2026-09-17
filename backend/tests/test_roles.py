"""Role-policy tests (Milestone 3.1 Slice 2; see ADR 0014).

Proves the role table in ADR 0014 is actually enforced by the backend,
not merely documented: viewer can read its own organization but cannot
start triage or submit a decision (`403`); reviewer and administrator can
do both. Every check here is same-tenant -- see test_tenant_isolation_api.py
for the separate cross-tenant (`404`) boundary.

Requires isolated PostgreSQL -- skips cleanly if unavailable. Never runs
against the live demo database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID,
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORG_VIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
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
        pytest.skip("PostgreSQL is required for role-policy tests.")

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
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def add_issue(
    session: Session,
    *,
    title: str = "App crash on startup",
    external_number: int = 1,
) -> Issue:
    repository = Repository(
        tenant_id=DEFAULT_ORGANIZATION_ID,
        name=f"pallets/flask-roles-{external_number}",
        source_url=f"https://github.com/pallets/flask-roles-{external_number}",
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title=title,
        body="Traceback attached below",
        state="open",
        source_url=f"https://github.com/pallets/flask-roles-{external_number}/issues/1",
    )
    session.add(issue)
    session.commit()
    return issue


def _headers(actor_id) -> dict:
    return {"X-Demo-Actor-ID": str(actor_id)}


# --- read access: every role can read its own organization -------------------


@pytest.mark.parametrize(
    "actor_id",
    [
        DEFAULT_ORG_VIEWER_ACTOR_ID,
        DEFAULT_ORG_REVIEWER_ACTOR_ID,
        DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID,
    ],
)
def test_every_role_can_read_its_own_organization(
    client: TestClient, database_session: Session, actor_id
) -> None:
    issue = add_issue(database_session)

    list_response = client.get("/api/v1/issues", headers=_headers(actor_id))
    detail_response = client.get(f"/api/v1/issues/{issue.id}", headers=_headers(actor_id))

    assert list_response.status_code == 200
    assert len(list_response.json()["issues"]) == 1
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == str(issue.id)


# --- viewer cannot start triage or submit a decision --------------------------


def test_viewer_cannot_start_triage(client: TestClient, database_session: Session) -> None:
    issue = add_issue(database_session)

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage", json={}, headers=_headers(DEFAULT_ORG_VIEWER_ACTOR_ID)
    )

    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_role"


def test_viewer_cannot_submit_a_decision(client: TestClient, database_session: Session) -> None:
    issue = add_issue(database_session)
    # A reviewer starts triage first so a recommendation exists to decide on.
    client.post(
        f"/api/v1/issues/{issue.id}/triage",
        json={},
        headers=_headers(DEFAULT_ORG_REVIEWER_ACTOR_ID),
    )

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage/decision",
        json={"decision": "approve"},
        headers=_headers(DEFAULT_ORG_VIEWER_ACTOR_ID),
    )

    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_role"
    # No decision was ever recorded by the rejected viewer request.
    result = client.get(
        f"/api/v1/issues/{issue.id}/triage", headers=_headers(DEFAULT_ORG_VIEWER_ACTOR_ID)
    )
    assert result.json()["human_review"]["decision"] is None


# --- reviewer and administrator can do both ------------------------------------


@pytest.mark.parametrize(
    "actor_id", [DEFAULT_ORG_REVIEWER_ACTOR_ID, DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID]
)
def test_reviewer_and_administrator_can_start_triage(
    client: TestClient, database_session: Session, actor_id
) -> None:
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={}, headers=_headers(actor_id))

    assert response.status_code == 202


@pytest.mark.parametrize(
    "actor_id", [DEFAULT_ORG_REVIEWER_ACTOR_ID, DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID]
)
def test_reviewer_and_administrator_can_submit_a_decision(
    client: TestClient, database_session: Session, actor_id
) -> None:
    issue = add_issue(database_session)
    client.post(f"/api/v1/issues/{issue.id}/triage", json={}, headers=_headers(actor_id))

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage/decision",
        json={"decision": "approve"},
        headers=_headers(actor_id),
    )

    assert response.status_code == 200
    assert response.json()["human_review"]["decision"] == "approve"
    assert response.json()["human_review"]["decided_by"] == str(actor_id)
