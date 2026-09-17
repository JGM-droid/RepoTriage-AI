"""Workflow/Celery ownership-revalidation tests (Milestone 3.1 Slice 2; see
ADR 0014).

`execute_triage_workflow.delay(str(analysis.id))` has only ever passed an
analysis id (unchanged since Milestone 2.1) -- there is no tenant id,
actor id, or role in the Celery task payload to forge in the first place.
These tests prove that structurally (the task signature accepts nothing
else) and behaviorally (the worker's every read comes from the database,
keyed only by that id, never from anything a caller could have supplied),
and prove the two things that actually gate a cross-tenant or disabled
actor from ever causing work to happen: the API-layer check before an
`Analysis` row is created, and the worker's own re-fetch of `Issue`/
`Repository` from PostgreSQL on every run.

Requires isolated PostgreSQL and Redis (Celery eager mode; see
conftest.py) -- skips cleanly if unavailable. Never runs against the live
demo database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    ISOLATION_DEMO_ORGANIZATION_ID,
    ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
    Analysis,
    HumanDecision,
    Issue,
    Recommendation,
    Repository,
    StageAttempt,
)
from app.workflow.tasks import execute_triage_workflow, process_workflow_run
from tests.db_maintenance import truncate_for_test

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, stage_attempts, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for workflow tenant-isolation tests.")

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


def add_issue(session: Session, *, organization_id, name: str) -> Issue:
    repository = Repository(
        tenant_id=organization_id, name=name, source_url=f"https://github.com/{name}"
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=1,
        title="Workflow isolation test issue",
        body="Traceback attached below",
        state="open",
        source_url=f"https://github.com/{name}/issues/1",
    )
    session.add(issue)
    session.commit()
    return issue


def test_the_celery_task_rejects_any_extra_payload_field(database_session: Session) -> None:
    """Structural proof: there is nothing tenant/actor-shaped to forge in
    the first place. The task accepts exactly one argument (the analysis
    id); attempting to smuggle any second field -- a forged tenant id, an
    actor id, a role -- fails immediately as a plain Python argument-count
    error, before any code in the task body runs."""
    with pytest.raises(TypeError):
        execute_triage_workflow.delay("some-analysis-id", "forged-tenant-id")


def test_worker_rederives_ownership_from_the_database_for_a_direct_task_invocation(
    database_session: Session,
) -> None:
    """Simulates the most adversarial legitimate scenario: something with
    raw access to enqueue a Celery task (bypassing the API's role/tenant
    check entirely) invokes the task directly for an analysis that
    already exists. Even then, the worker only ever reads the analysis's
    real, persisted issue/repository -- there is no forged payload field
    that could redirect it to another organization's data, because none
    exists in the payload."""
    org_b_issue = add_issue(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/direct-invoke"
    )
    analysis = Analysis(
        issue_id=org_b_issue.id,
        status="queued",
        initiating_actor_id=ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
    )
    database_session.add(analysis)
    database_session.commit()

    # Eager mode (see conftest.py) executes this synchronously in-process
    # -- exactly the mechanism a real worker would use, minus the network
    # hop -- entirely bypassing the API/HTTP layer and its role/tenant
    # check, which is the point: even so, nothing crosses the boundary.
    execute_triage_workflow.delay(str(analysis.id))

    database_session.refresh(analysis)
    assert analysis.status == "completed"
    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    content = json.loads(recommendation.content)
    # Nothing in the resulting recommendation references any repository
    # other than org B's own -- there was never another org's data for it
    # to reach, since the workflow's every read is keyed by this
    # analysis's real issue_id.
    assert content["retrieved_evidence"]["items"] == []  # no other org-B content exists yet


def test_disabled_actor_cannot_cause_an_analysis_to_be_created(
    client: TestClient, database_session: Session
) -> None:
    org_a_issue = add_issue(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/disabled-actor"
    )
    disabled_actor_id = database_session.execute(
        text(
            "INSERT INTO actors (id, organization_id, slug, display_name, role, is_enabled) "
            "VALUES (gen_random_uuid(), :org_id, :slug, 'Disabled Reviewer', 'reviewer', false) "
            "RETURNING id"
        ),
        {"org_id": DEFAULT_ORGANIZATION_ID, "slug": "disabled-reviewer-workflow-test"},
    ).scalar_one()
    database_session.commit()

    try:
        response = client.post(
            f"/api/v1/issues/{org_a_issue.id}/triage",
            json={},
            headers={"X-Demo-Actor-ID": str(disabled_actor_id)},
        )

        assert response.status_code == 401
        assert database_session.query(Analysis).count() == 0
    finally:
        database_session.execute(
            text("DELETE FROM actors WHERE id = :id"), {"id": disabled_actor_id}
        )
        database_session.commit()


def test_cross_tenant_actor_cannot_cause_an_analysis_or_recommendation_to_be_created(
    client: TestClient, database_session: Session
) -> None:
    org_b_issue = add_issue(
        database_session,
        organization_id=ISOLATION_DEMO_ORGANIZATION_ID,
        name="org-b/cross-tenant-start",
    )

    response = client.post(
        f"/api/v1/issues/{org_b_issue.id}/triage",
        json={},
        headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)},
    )

    assert response.status_code == 404
    assert database_session.query(Analysis).count() == 0
    assert database_session.query(Recommendation).count() == 0


def test_exactly_one_recommendation_and_stage_chain_for_an_authorized_request(
    client: TestClient, database_session: Session
) -> None:
    org_a_issue = add_issue(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/exactly-one"
    )

    response = client.post(
        f"/api/v1/issues/{org_a_issue.id}/triage",
        json={},
        headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)},
    )
    assert response.status_code == 202
    analysis_id = UUID(response.json()["analysis_id"])

    # Idempotent re-invocation (matches the pre-existing Milestone 2.1
    # resume guarantee) must still produce exactly one recommendation --
    # tenant enforcement must not have broken that invariant.
    process_workflow_run(database_session, analysis_id)

    assert database_session.query(Recommendation).filter_by(analysis_id=analysis_id).count() == 1
    stage_attempts = database_session.query(StageAttempt).filter_by(analysis_id=analysis_id).all()
    stages_seen = [attempt.stage for attempt in stage_attempts]
    assert len(stages_seen) == len(set(stages_seen))  # no stage attempted twice for this attempt


def test_ai_inference_never_creates_a_human_decision(
    client: TestClient, database_session: Session
) -> None:
    org_a_issue = add_issue(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/no-auto-decision"
    )

    response = client.post(
        f"/api/v1/issues/{org_a_issue.id}/triage",
        json={},
        headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)},
    )
    assert response.status_code == 202

    assert database_session.query(HumanDecision).count() == 0
    result = client.get(
        f"/api/v1/issues/{org_a_issue.id}/triage",
        headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)},
    )
    payload = result.json()
    assert payload["status"] == "completed"
    assert payload["human_review"]["decision"] is None
    assert payload["human_review"]["recommendation_status"] == "proposed"
