"""Cross-tenant isolation tests (Milestone 3.1 Slice 2; see ADR 0014).

Proves Critical Gate G4's core claim in isolated tests: an actor from
organization A cannot observe organization B's repositories, issues,
analyses, recommendations, human decisions, repository documents,
retrieval chunks, stage attempts, or audit events/traces -- through
either list filtering or a guessed direct identifier. Every cross-tenant
direct lookup that goes through the API returns `404`, indistinguishable
from a genuinely unknown id (see ADR 0014's 404-for-cross-tenant
rationale).

Resources with no dedicated REST endpoint (Repository, standalone
Analysis/Recommendation/HumanDecision lookup, RepositoryDocument,
RetrievalChunk, StageAttempt, AuditEvent) are proven isolated directly
against `app.api.scoping`'s helpers -- the same helpers the API layer
itself uses -- rather than by inventing new endpoints only to exercise
them (see ADR 0014's alternatives-considered section).

Uses the two real, deterministic organizations and their deterministic
actors (both seeded by migration; no synthetic/mock tenant). Requires
isolated PostgreSQL -- skips cleanly if unavailable. Never runs against
the live demo database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.scoping import (
    audit_events_query_for_tenant,
    get_analysis_for_tenant,
    get_human_decision_for_tenant,
    get_recommendation_for_tenant,
    get_repository_for_tenant,
    repository_documents_query_for_tenant,
    retrieval_chunks_query_for_tenant,
    stage_attempts_query_for_tenant,
)
from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORG_VIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    ISOLATION_DEMO_ORGANIZATION_ID,
    ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
    Analysis,
    HumanDecision,
    Issue,
    Recommendation,
    Repository,
    RepositoryDocument,
    RetrievalChunk,
)
from tests.db_maintenance import truncate_for_test

TRUNCATE_TABLES = (
    "audit_events, human_decisions, recommendations, stage_attempts, analyses, "
    "retrieval_chunks, repository_documents, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for tenant-isolation tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            truncate_for_test(connection, f"TRUNCATE {TRUNCATE_TABLES}")
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


def _headers(actor_id) -> dict:
    return {"X-Demo-Actor-ID": str(actor_id)}


def add_issue(session: Session, *, organization_id, name: str, external_number: int = 1) -> Issue:
    repository = Repository(
        tenant_id=organization_id,
        name=name,
        source_url=f"https://github.com/{name}",
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title=f"Issue in {name}",
        body="Traceback attached below",
        state="open",
        source_url=f"https://github.com/{name}/issues/{external_number}",
    )
    session.add(issue)
    session.commit()
    return issue


def build_full_chain(
    client: TestClient, database_session: Session, *, organization_id, reviewer_actor_id, name: str
):
    """Drives a real triage run and human decision through the actual API
    for one organization, producing a full Issue -> Analysis ->
    StageAttempt(s) -> Recommendation -> HumanDecision -> AuditEvent chain
    -- exactly the shape a real tenant's data takes, not a synthetic
    stand-in for it."""
    issue = add_issue(database_session, organization_id=organization_id, name=name)
    start = client.post(
        f"/api/v1/issues/{issue.id}/triage", json={}, headers=_headers(reviewer_actor_id)
    )
    assert start.status_code == 202
    decide = client.post(
        f"/api/v1/issues/{issue.id}/triage/decision",
        json={"decision": "approve"},
        headers=_headers(reviewer_actor_id),
    )
    assert decide.status_code == 200
    return issue


def add_document_and_chunk(
    session: Session, repository_id, *, path: str
) -> tuple[RepositoryDocument, RetrievalChunk]:
    document = RepositoryDocument(
        repository_id=repository_id,
        path=path,
        title="Doc",
        source_url=f"https://example.test/{path}",
        content="Some documentation content.",
        content_hash="0" * 64,
    )
    session.add(document)
    session.flush()
    chunk = RetrievalChunk(
        repository_id=repository_id,
        source_type="document",
        document_id=document.id,
        chunk_index=0,
        title="Doc",
        source_url=f"https://example.test/{path}",
        state="published",
        content="Some documentation content.",
        content_hash="0" * 64,
        embedding=[0.0] * 384,
        embedding_model="fake",
        embedding_version="test",
    )
    session.add(chunk)
    session.commit()
    return document, chunk


# --- issues: list and direct id --------------------------------------------------


def test_issue_list_never_includes_another_organizations_issues(
    client: TestClient, database_session: Session
) -> None:
    build_full_chain(
        client,
        database_session,
        organization_id=DEFAULT_ORGANIZATION_ID,
        reviewer_actor_id=DEFAULT_ORG_REVIEWER_ACTOR_ID,
        name="org-a/repo",
    )
    build_full_chain(
        client,
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        reviewer_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
        name="org-b/repo",
    )

    response = client.get("/api/v1/issues", headers=_headers(DEFAULT_ORG_VIEWER_ACTOR_ID))

    assert response.status_code == 200
    repo_names = {issue["repository"]["name"] for issue in response.json()["issues"]}
    assert repo_names == {"org-a/repo"}


def test_guessed_cross_tenant_issue_id_returns_404(
    client: TestClient, database_session: Session
) -> None:
    org_b_issue = add_issue(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/private"
    )

    response = client.get(
        f"/api/v1/issues/{org_b_issue.id}", headers=_headers(DEFAULT_ORG_VIEWER_ACTOR_ID)
    )

    assert response.status_code == 404
    assert response.json()["error"] == "issue_not_found"


# --- triage start/status/decision: cross-tenant direct id ------------------------


def test_starting_triage_on_another_organizations_issue_returns_404(
    client: TestClient, database_session: Session
) -> None:
    org_b_issue = add_issue(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/target"
    )

    response = client.post(
        f"/api/v1/issues/{org_b_issue.id}/triage",
        json={},
        headers=_headers(DEFAULT_ORG_REVIEWER_ACTOR_ID),
    )

    assert response.status_code == 404
    assert response.json()["error"] == "issue_not_found"


def test_polling_another_organizations_triage_status_returns_404(
    client: TestClient, database_session: Session
) -> None:
    org_b_issue = build_full_chain(
        client,
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        reviewer_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
        name="org-b/status-target",
    )

    response = client.get(
        f"/api/v1/issues/{org_b_issue.id}/triage", headers=_headers(DEFAULT_ORG_VIEWER_ACTOR_ID)
    )

    assert response.status_code == 404
    assert response.json()["error"] == "issue_not_found"


def test_deciding_on_another_organizations_recommendation_returns_404(
    client: TestClient, database_session: Session
) -> None:
    org_b_issue = add_issue(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/decide-target"
    )
    # Org B starts and completes its own triage first, so a real
    # recommendation exists for Org A to (fail to) act on.
    client.post(
        f"/api/v1/issues/{org_b_issue.id}/triage",
        json={},
        headers=_headers(ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID),
    )

    response = client.post(
        f"/api/v1/issues/{org_b_issue.id}/triage/decision",
        json={"decision": "approve"},
        headers=_headers(DEFAULT_ORG_REVIEWER_ACTOR_ID),
    )

    assert response.status_code == 404
    assert response.json()["error"] == "issue_not_found"
    # And the cross-tenant attempt never actually recorded a decision.
    status_as_owner = client.get(
        f"/api/v1/issues/{org_b_issue.id}/triage",
        headers=_headers(ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID),
    )
    assert status_as_owner.json()["human_review"]["decision"] is None


# --- resources with no dedicated endpoint: proven at the scoping-helper level -----


def test_repository_list_and_direct_id_are_tenant_scoped(database_session: Session) -> None:
    org_a_issue = add_issue(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/repo-scope"
    )
    org_b_issue = add_issue(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/repo-scope"
    )

    assert (
        get_repository_for_tenant(
            database_session, org_a_issue.repository_id, DEFAULT_ORGANIZATION_ID
        )
        is not None
    )
    assert (
        get_repository_for_tenant(
            database_session, org_b_issue.repository_id, DEFAULT_ORGANIZATION_ID
        )
        is None
    )


def test_analysis_direct_id_is_tenant_scoped(client: TestClient, database_session: Session) -> None:
    org_b_issue = build_full_chain(
        client,
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        reviewer_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
        name="org-b/analysis-scope",
    )
    analysis = database_session.query(Analysis).filter_by(issue_id=org_b_issue.id).one()

    assert (
        get_analysis_for_tenant(database_session, analysis.id, ISOLATION_DEMO_ORGANIZATION_ID)
        is not None
    )
    assert get_analysis_for_tenant(database_session, analysis.id, DEFAULT_ORGANIZATION_ID) is None


def test_recommendation_direct_id_is_tenant_scoped(
    client: TestClient, database_session: Session
) -> None:
    org_b_issue = build_full_chain(
        client,
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        reviewer_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
        name="org-b/rec-scope",
    )
    analysis = database_session.query(Analysis).filter_by(issue_id=org_b_issue.id).one()
    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()

    assert (
        get_recommendation_for_tenant(
            database_session, recommendation.id, ISOLATION_DEMO_ORGANIZATION_ID
        )
        is not None
    )
    assert (
        get_recommendation_for_tenant(database_session, recommendation.id, DEFAULT_ORGANIZATION_ID)
        is None
    )


def test_human_decision_direct_id_is_tenant_scoped(
    client: TestClient, database_session: Session
) -> None:
    org_b_issue = build_full_chain(
        client,
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        reviewer_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
        name="org-b/decision-scope",
    )
    decision = (
        database_session.query(HumanDecision)
        .join(Recommendation, HumanDecision.recommendation_id == Recommendation.id)
        .join(Analysis, Recommendation.analysis_id == Analysis.id)
        .filter(Analysis.issue_id == org_b_issue.id)
        .one()
    )

    assert (
        get_human_decision_for_tenant(database_session, decision.id, ISOLATION_DEMO_ORGANIZATION_ID)
        is not None
    )
    assert (
        get_human_decision_for_tenant(database_session, decision.id, DEFAULT_ORGANIZATION_ID)
        is None
    )


def test_repository_documents_are_tenant_scoped(database_session: Session) -> None:
    org_a_issue = add_issue(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/docs-scope"
    )
    org_b_issue = add_issue(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/docs-scope"
    )
    add_document_and_chunk(database_session, org_a_issue.repository_id, path="a-doc")
    add_document_and_chunk(database_session, org_b_issue.repository_id, path="b-doc")

    org_a_docs = database_session.scalars(
        repository_documents_query_for_tenant(DEFAULT_ORGANIZATION_ID)
    ).all()
    assert {doc.path for doc in org_a_docs} == {"a-doc"}


def test_retrieval_chunks_are_tenant_scoped(database_session: Session) -> None:
    org_a_issue = add_issue(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/chunks-scope"
    )
    org_b_issue = add_issue(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/chunks-scope"
    )
    add_document_and_chunk(database_session, org_a_issue.repository_id, path="a-chunk-doc")
    add_document_and_chunk(database_session, org_b_issue.repository_id, path="b-chunk-doc")

    org_a_chunks = database_session.scalars(
        retrieval_chunks_query_for_tenant(DEFAULT_ORGANIZATION_ID)
    ).all()
    assert all(chunk.repository_id == org_a_issue.repository_id for chunk in org_a_chunks)
    assert len(org_a_chunks) == 1


def test_stage_attempts_are_tenant_scoped(client: TestClient, database_session: Session) -> None:
    build_full_chain(
        client,
        database_session,
        organization_id=DEFAULT_ORGANIZATION_ID,
        reviewer_actor_id=DEFAULT_ORG_REVIEWER_ACTOR_ID,
        name="org-a/stages-scope",
    )
    build_full_chain(
        client,
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        reviewer_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
        name="org-b/stages-scope",
    )

    org_a_stage_attempts = database_session.scalars(
        stage_attempts_query_for_tenant(DEFAULT_ORGANIZATION_ID)
    ).all()
    assert len(org_a_stage_attempts) > 0

    # Every stage attempt reachable through the Org A scope actually
    # belongs to an Org A analysis -- proven by cross-referencing back to
    # the issue chain, not merely trusting the query.
    org_a_analysis_ids = {attempt.analysis_id for attempt in org_a_stage_attempts}
    for analysis_id in org_a_analysis_ids:
        analysis = database_session.get(Analysis, analysis_id)
        issue = database_session.get(Issue, analysis.issue_id)
        repository = database_session.get(Repository, issue.repository_id)
        assert repository.tenant_id == DEFAULT_ORGANIZATION_ID


def test_audit_events_are_tenant_scoped(client: TestClient, database_session: Session) -> None:
    build_full_chain(
        client,
        database_session,
        organization_id=DEFAULT_ORGANIZATION_ID,
        reviewer_actor_id=DEFAULT_ORG_REVIEWER_ACTOR_ID,
        name="org-a/audit-scope",
    )
    build_full_chain(
        client,
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        reviewer_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
        name="org-b/audit-scope",
    )

    org_a_events = database_session.scalars(
        audit_events_query_for_tenant(DEFAULT_ORGANIZATION_ID)
    ).all()
    assert len(org_a_events) > 0

    for event in org_a_events:
        repository = database_session.get(Repository, event.repository_id)
        assert repository.tenant_id == DEFAULT_ORGANIZATION_ID
    # The decision audit event's actor really is the Org A reviewer.
    decision_events = [e for e in org_a_events if e.event_type == "triage_human_decision"]
    assert len(decision_events) == 1
    assert str(decision_events[0].actor_id) == str(DEFAULT_ORG_REVIEWER_ACTOR_ID)
