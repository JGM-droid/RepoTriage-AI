"""Organization/tenant data-model tests (Milestone 3.1 Slice 1; see ADR
0013).

Proves the *data model* foundation: two organizations can coexist, a
repository requires exactly one valid organization, an organization that
owns a repository cannot be deleted, and slug uniqueness is enforced.

This file also contains an explicit, honestly-named boundary test: Slice 1
adds no request-level tenant scoping at all. That test calls the real,
unmodified `GET /api/v1/issues` endpoint -- it does not filter by tenant
itself and must not be read as a Critical Gate G4 pass. Enforcement is
Slice 2's job.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.issues import get_issue_session
from app.main import app
from app.models.core import (
    DEFAULT_ORGANIZATION_ID,
    ISOLATION_DEMO_ORGANIZATION_ID,
    Issue,
    Organization,
    Repository,
)

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for organization data-model tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
            session.rollback()
    finally:
        engine.dispose()


def add_repository(
    session: Session, *, name: str, tenant_id=None, external_number: int = 1
) -> Repository:
    repository = Repository(
        name=name,
        source_url=f"https://github.com/{name}",
        **({"tenant_id": tenant_id} if tenant_id is not None else {}),
    )
    session.add(repository)
    session.flush()
    return repository


def add_issue(session: Session, repository: Repository, *, external_number: int = 1) -> Issue:
    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title="Test issue",
        body="Test body",
        state="open",
        source_url=f"{repository.source_url}/issues/{external_number}",
    )
    session.add(issue)
    session.flush()
    return issue


# --- migration-seeded deterministic organizations ----------------------------


def test_both_deterministic_organizations_exist_exactly_once(
    database_session: Session,
) -> None:
    """Seeded by migration 20260917_0005, never by application code --
    proves the migration's own seed data is present, not a fixture
    artifact of this test."""
    orgs = (
        database_session.query(Organization)
        .filter(Organization.id.in_([DEFAULT_ORGANIZATION_ID, ISOLATION_DEMO_ORGANIZATION_ID]))
        .all()
    )
    assert {o.id for o in orgs} == {DEFAULT_ORGANIZATION_ID, ISOLATION_DEMO_ORGANIZATION_ID}
    assert {o.slug for o in orgs} == {"default-demo", "isolation-demo"}


def test_isolation_demo_organization_owns_no_repository_by_default(
    database_session: Session,
) -> None:
    """It exists solely to make a second, real tenant provably present --
    not to carry demo data itself."""
    isolation_org = database_session.get(Organization, ISOLATION_DEMO_ORGANIZATION_ID)
    assert isolation_org.repositories == []


# --- model/constraint behavior -----------------------------------------------


def test_two_organizations_can_coexist(database_session: Session) -> None:
    org_a = Organization(slug=f"org-a-{uuid4().hex[:8]}", name="Org A")
    org_b = Organization(slug=f"org-b-{uuid4().hex[:8]}", name="Org B")
    database_session.add_all([org_a, org_b])
    database_session.commit()

    assert org_a.id != org_b.id
    assert database_session.get(Organization, org_a.id) is not None
    assert database_session.get(Organization, org_b.id) is not None


def test_a_repository_belongs_to_exactly_one_organization(database_session: Session) -> None:
    repository = add_repository(
        database_session, name="pallets/flask-single-org", tenant_id=DEFAULT_ORGANIZATION_ID
    )
    database_session.commit()

    fetched = database_session.get(Repository, repository.id)
    assert fetched.tenant_id == DEFAULT_ORGANIZATION_ID
    assert fetched.organization.id == DEFAULT_ORGANIZATION_ID


def test_a_repository_defaults_to_the_default_organization_when_omitted(
    database_session: Session,
) -> None:
    """The server-side default (see ADR 0013's backward-compatibility
    decision) -- proves the existing importer/test construction path
    (which never specifies tenant_id) still produces a valid row."""
    repository = add_repository(database_session, name="pallets/flask-omitted-tenant")
    database_session.commit()

    fetched = database_session.get(Repository, repository.id)
    assert fetched.tenant_id == DEFAULT_ORGANIZATION_ID


def test_a_repository_cannot_reference_an_unknown_organization(
    database_session: Session,
) -> None:
    with pytest.raises(IntegrityError):
        add_repository(database_session, name="pallets/flask-bad-org", tenant_id=uuid4())
        database_session.commit()


def test_a_repository_cannot_have_a_null_organization(database_session: Session) -> None:
    with pytest.raises(IntegrityError):
        database_session.execute(
            text(
                "INSERT INTO repositories (id, tenant_id, name, source_url) "
                "VALUES (:id, NULL, 'null-tenant-repo', 'https://example.test/null-tenant-repo')"
            ),
            {"id": uuid4()},
        )
        database_session.commit()


def test_deleting_an_organization_that_owns_a_repository_is_rejected(
    database_session: Session,
) -> None:
    """RESTRICT, not CASCADE (ADR 0013) -- a delete that would orphan a
    repository (and therefore every downstream issue/analysis/
    recommendation/decision/audit record) must fail, never silently
    cascade."""
    org = Organization(slug=f"restrict-test-{uuid4().hex[:8]}", name="Restrict Test Org")
    database_session.add(org)
    database_session.flush()
    add_repository(database_session, name="pallets/flask-restrict-test", tenant_id=org.id)
    database_session.commit()

    with pytest.raises(IntegrityError):
        database_session.delete(org)
        database_session.commit()


def test_deleting_an_empty_organization_succeeds(database_session: Session) -> None:
    """The restrictive policy blocks deletion only when data is actually
    owned -- proving the rejection above is about ownership, not merely
    "organizations can never be deleted"."""
    org = Organization(slug=f"empty-delete-{uuid4().hex[:8]}", name="Empty Org")
    database_session.add(org)
    database_session.commit()
    org_id = org.id

    database_session.delete(org)
    database_session.commit()

    assert database_session.get(Organization, org_id) is None


def test_organization_slug_uniqueness_is_enforced(database_session: Session) -> None:
    shared_slug = f"duplicate-slug-{uuid4().hex[:8]}"
    database_session.add(Organization(slug=shared_slug, name="First"))
    database_session.commit()

    with pytest.raises(IntegrityError):
        database_session.add(Organization(slug=shared_slug, name="Second"))
        database_session.commit()


# --- honest boundary: no request-level tenant isolation yet ------------------


@pytest.fixture()
def client(database_session: Session) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        yield database_session

    app.dependency_overrides[get_issue_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_slice_1_provides_no_request_level_tenant_isolation_yet(
    client: TestClient, database_session: Session
) -> None:
    """Honest boundary test (Milestone 3.1 Slice 1; see ADR 0013): calls
    the real, completely unmodified `GET /api/v1/issues` endpoint -- this
    test does not filter by tenant itself, so it proves the API's actual
    current behavior, not a manually-scoped stand-in for it. With one
    issue in the default organization's repository and one in the
    isolation-demo organization's repository, both are visible through the
    same unscoped call. This is expected, current behavior for Slice 1 --
    NOT a Critical Gate G4 pass. Gate G4 requires automated negative tests
    proving cross-tenant records are *inaccessible*, which is Slice 2's
    job (request identity + backend-enforced scoping), not this one's."""
    default_org_repository = add_repository(
        database_session, name="default-org/repo", tenant_id=DEFAULT_ORGANIZATION_ID
    )
    isolation_org_repository = add_repository(
        database_session, name="isolation-org/repo", tenant_id=ISOLATION_DEMO_ORGANIZATION_ID
    )
    default_org_issue = add_issue(database_session, default_org_repository)
    isolation_org_issue = add_issue(database_session, isolation_org_repository)
    database_session.commit()

    response = client.get("/api/v1/issues")

    assert response.status_code == 200
    returned_ids = {issue["id"] for issue in response.json()["issues"]}
    # Both organizations' issues are visible -- proving no isolation exists
    # yet, exactly as Slice 1 is scoped to leave it.
    assert str(default_org_issue.id) in returned_ids
    assert str(isolation_org_issue.id) in returned_ids
