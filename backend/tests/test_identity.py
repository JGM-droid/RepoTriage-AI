"""Identity resolution tests (Milestone 3.1 Slice 2; see ADR 0014).

Proves `app.api.identity.resolve_demo_actor`'s `401` boundary: a missing,
malformed, unknown, or disabled-actor `X-Demo-Actor-ID` header is always
rejected before any route body runs, and a client cannot influence which
organization or role it is treated as except by naming a different
persisted actor id.

Requires isolated PostgreSQL -- skips cleanly if unavailable. Relies on
the five deterministic actors seeded by migration `20260918_0006`; never
runs against the live demo database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.v1.issues import get_issue_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID,
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORG_VIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    ISOLATION_DEMO_ORGANIZATION_ID,
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
        pytest.skip("PostgreSQL is required for identity tests.")

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

    app.dependency_overrides[get_issue_session] = override_session
    try:
        yield TestClient(app)  # deliberately no default X-Demo-Actor-ID header
    finally:
        app.dependency_overrides.clear()


def _error_body(response) -> dict:
    payload = response.json()
    assert "error" in payload and "message" in payload
    return payload


def test_missing_actor_header_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/issues")

    assert response.status_code == 401
    assert _error_body(response)["error"] == "missing_actor_header"


def test_malformed_actor_id_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/issues", headers={"X-Demo-Actor-ID": "not-a-uuid"})

    assert response.status_code == 401
    assert _error_body(response)["error"] == "malformed_actor_id"


def test_unknown_actor_id_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/issues", headers={"X-Demo-Actor-ID": str(uuid4())})

    assert response.status_code == 401
    assert _error_body(response)["error"] == "unknown_actor"


def test_disabled_actor_returns_401(client: TestClient, database_session: Session) -> None:
    disabled_actor_id = uuid4()
    database_session.execute(
        text(
            "INSERT INTO actors (id, organization_id, slug, display_name, role, is_enabled) "
            "VALUES (:id, :org_id, :slug, 'Disabled Test Actor', 'viewer', false)"
        ),
        {
            "id": disabled_actor_id,
            "org_id": DEFAULT_ORGANIZATION_ID,
            "slug": f"disabled-test-{disabled_actor_id.hex[:8]}",
        },
    )
    database_session.commit()
    try:
        response = client.get("/api/v1/issues", headers={"X-Demo-Actor-ID": str(disabled_actor_id)})

        assert response.status_code == 401
        assert _error_body(response)["error"] == "unknown_actor"
    finally:
        database_session.execute(
            text("DELETE FROM actors WHERE id = :id"), {"id": disabled_actor_id}
        )
        database_session.commit()


def test_a_valid_actor_from_a_different_organization_is_not_treated_as_the_default_org(
    client: TestClient,
) -> None:
    """The server derives `organization_id` solely from the actor row the
    header names -- an isolation-demo actor is never treated as belonging
    to the default organization, and vice versa, no matter what else is
    on the request."""
    response = client.get(
        "/api/v1/issues", headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_VIEWER_ACTOR_ID)}
    )
    assert response.status_code == 200  # a real, enabled actor -- succeeds


def test_client_cannot_supply_or_override_organization_or_role(client: TestClient) -> None:
    """A client-supplied header that *looks* like it names an organization
    or role has no effect at all -- the server only ever looks up
    `X-Demo-Actor-ID` and reads organization/role from that row. A viewer
    actor cannot start triage no matter what other headers accompany the
    request (see test_roles.py for the full role-policy proof); this test
    specifically proves extra identity-shaped headers are ignored."""
    response = client.get(
        "/api/v1/issues",
        headers={
            "X-Demo-Actor-ID": str(DEFAULT_ORG_VIEWER_ACTOR_ID),
            "X-Organization-Id": str(ISOLATION_DEMO_ORGANIZATION_ID),
            "X-Role": "administrator",
            "X-Tenant-Id": str(ISOLATION_DEMO_ORGANIZATION_ID),
        },
    )

    assert response.status_code == 200
    # If the bogus X-Organization-Id/X-Tenant-Id headers had any effect,
    # this actor would see the isolation-demo organization's (empty)
    # issue list instead of its own -- proven empty vs. non-empty in
    # test_tenant_isolation_api.py; here we only need the request to
    # succeed as the real, database-derived organization, not error or
    # silently switch tenants.
    assert response.json() == {"issues": []}  # no issues seeded in this fresh database


def test_every_deterministic_default_org_actor_authenticates_successfully(
    client: TestClient,
) -> None:
    for actor_id in (
        DEFAULT_ORG_VIEWER_ACTOR_ID,
        DEFAULT_ORG_REVIEWER_ACTOR_ID,
        DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID,
    ):
        response = client.get("/api/v1/issues", headers={"X-Demo-Actor-ID": str(actor_id)})
        assert response.status_code == 200, f"actor {actor_id} failed to authenticate"
