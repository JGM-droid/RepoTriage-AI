"""Durable worker-side authorization tests (Milestone 3.1 Slice 2
correction; see ADR 0014).

The original Slice 2 implementation authorized once, at the API
boundary, before the `Analysis` row existed -- vulnerable to state
changing between enqueue and worker execution (an actor disabled or
moved to another organization in that window). This file proves the
correction: the worker's own `authorize` stage
(`app.workflow.tasks._authorize_workflow_run`) re-resolves the
initiating actor and the full ownership chain from PostgreSQL on every
attempt, using only `Analysis.initiating_actor_id` -- an immutable
reference recorded at creation time -- never anything a Celery payload
could carry.

Exercises the real `process_workflow_run`/`execute_triage_workflow`
functions and the real `POST /issues/{id}/triage` endpoint throughout --
never a bare helper function standing in for the workflow.

Requires isolated PostgreSQL and Redis (Celery eager mode; see
conftest.py) -- skips cleanly if unavailable. Never runs against the
live demo database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.workflow.tasks as workflow_tasks
from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    ISOLATION_DEMO_ORGANIZATION_ID,
    Analysis,
    HumanDecision,
    Issue,
    Recommendation,
    Repository,
    StageAttempt,
)
from app.workflow.tasks import (
    WorkflowAuthorizationError,
    _authorize_workflow_run,
    execute_triage_workflow,
    process_workflow_run,
)

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, stage_attempts, "
    "analyses, issues, repositories CASCADE"
)
_DETERMINISTIC_ACTOR_IDS = (
    "00000000-0000-0000-0000-000000000201",
    "00000000-0000-0000-0000-000000000202",
    "00000000-0000-0000-0000-000000000203",
    "00000000-0000-0000-0000-000000000204",
    "00000000-0000-0000-0000-000000000205",
)
_DELETE_CUSTOM_ACTORS = text(
    "DELETE FROM actors WHERE id NOT IN ('" + "', '".join(_DETERMINISTIC_ACTOR_IDS) + "')"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for workflow authorization tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
            # This file's `insert_actor` helper creates custom actors
            # beyond the five deterministic built-ins; clean up both
            # before and after so no test in this file (or any other
            # file sharing this disposable database) ever observes a
            # leaked custom actor row.
            connection.execute(_DELETE_CUSTOM_ACTORS)
        with Session(engine) as session:
            yield session
            # Same session, not a new connection: the test may have left
            # `session` mid-transaction (e.g. after a final read with no
            # explicit commit) -- TRUNCATE from a *different* connection
            # while that transaction is still open would block forever
            # waiting for a lock it will never release before this
            # `with` block exits. Using `session` itself avoids the
            # cross-connection lock wait entirely.
            session.rollback()
            session.execute(text(TRUNCATE_CORE_TABLES))
            session.execute(_DELETE_CUSTOM_ACTORS)
            session.commit()
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


def add_issue(session: Session, *, organization_id=DEFAULT_ORGANIZATION_ID, name: str) -> Issue:
    repository = Repository(
        tenant_id=organization_id, name=name, source_url=f"https://github.com/{name}"
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=1,
        title="Workflow authorization test issue",
        body="Traceback attached below",
        state="open",
        source_url=f"https://github.com/{name}/issues/1",
    )
    session.add(issue)
    session.commit()
    return issue


def insert_actor(
    session: Session, *, organization_id, role: str = "reviewer", is_enabled: bool = True
) -> UUID:
    actor_id = session.execute(
        text(
            "INSERT INTO actors (id, organization_id, slug, display_name, role, is_enabled) "
            "VALUES (gen_random_uuid(), :org_id, :slug, 'Test Actor', :role, :is_enabled) "
            "RETURNING id"
        ),
        {
            "org_id": organization_id,
            "slug": f"authz-test-{uuid4().hex[:8]}",
            "role": role,
            "is_enabled": is_enabled,
        },
    ).scalar_one()
    session.commit()
    return actor_id


def disable_actor(session: Session, actor_id: UUID) -> None:
    session.execute(text("UPDATE actors SET is_enabled = false WHERE id = :id"), {"id": actor_id})
    session.commit()


def move_actor_organization(session: Session, actor_id: UUID, organization_id) -> None:
    session.execute(
        text("UPDATE actors SET organization_id = :org_id WHERE id = :id"),
        {"id": actor_id, "org_id": organization_id},
    )
    session.commit()


def authorize_stage_attempts(session: Session, analysis_id) -> list[StageAttempt]:
    return session.query(StageAttempt).filter_by(analysis_id=analysis_id, stage="authorize").all()


# --- 1. authorized API request records the initiating actor reference ----------


def test_authorized_api_request_enqueues_the_analysis_with_the_initiating_actor_reference(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session, name="org-a/enqueue-reference")

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage",
        json={},
        headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)},
    )

    assert response.status_code == 202
    analysis_id = response.json()["analysis_id"]
    analysis = database_session.get(Analysis, UUID(analysis_id))
    assert analysis.initiating_actor_id == DEFAULT_ORG_REVIEWER_ACTOR_ID


# --- 2. the Celery payload carries no trusted tenant id or role -----------------


def test_the_celery_payload_carries_no_tenant_id_or_role() -> None:
    """No database needed -- this is a structural property of the task
    signature itself. The task accepts exactly one argument (the analysis
    id). Attempting
    to smuggle any second field -- a forged tenant id, an actor id, a
    role -- fails immediately as a plain Python argument-count error,
    before any code in the task body runs. There is nothing tenant- or
    role-shaped in the payload to forge in the first place; authorization
    comes entirely from `Analysis.initiating_actor_id`, resolved inside
    the worker from PostgreSQL, never from the task message."""
    with pytest.raises(TypeError):
        execute_triage_workflow.delay("some-analysis-id", "forged-tenant-id", "administrator")


# --- 3. actor disabled after enqueue, before worker execution -------------------


def test_actor_disabled_after_enqueue_blocks_retrieval_ai_inference_and_recommendation(
    database_session: Session,
) -> None:
    issue = add_issue(database_session, name="org-a/disabled-after-enqueue")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    # The state change happens strictly *after* enqueue -- simulating an
    # administrator disabling the actor while the task sits on the broker.
    disable_actor(database_session, actor_id)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0

    stage_names = {
        attempt.stage
        for attempt in database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    }
    # Only "authorize" ever ran -- retrieval and ai_inference never
    # started, because the whole stage chain is inside one try block and
    # authorize is first.
    assert stage_names == {"authorize"}

    authorize_attempts = authorize_stage_attempts(database_session, analysis.id)
    assert len(authorize_attempts) == 1
    assert authorize_attempts[0].status == "failed"
    # Bounded and observable, but never names the actor or organization.
    assert "disabled" in authorize_attempts[0].error
    assert str(actor_id) not in authorize_attempts[0].error


def test_actor_disabled_after_enqueue_eventually_reaches_terminal_failed_with_no_recommendation(
    database_session: Session,
) -> None:
    """Retries do not bypass the check -- a still-disabled actor keeps
    failing on every attempt, bounded exactly like any other stage
    failure, until the existing max-attempts machinery marks the
    analysis terminally `failed`."""
    issue = add_issue(database_session, name="org-a/disabled-exhausted")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()
    disable_actor(database_session, actor_id)

    for _ in range(workflow_tasks.settings.triage_max_attempts - 1):
        with pytest.raises(workflow_tasks._RetryableWorkflowError):
            process_workflow_run(database_session, analysis.id)
        database_session.refresh(analysis)
        assert analysis.status == "retrying"

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "failed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0


# --- 3b. actor disabled *between* two checkpoints, mid-attempt -----------------


def test_actor_disabled_between_authorize_and_retrieval_blocks_retrieval_and_everything_after(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authorization is dynamic state, not a cacheable stage result: the
    initial `authorize` checkpoint succeeds while the actor is still
    enabled, but a stage-controlled hook on the real `propose` function
    (the last deterministic stage before the `authorize_before_retrieval`
    checkpoint) disables the actor at the exact moment between them --
    demonstrating the state change at the real boundary, not by calling
    `_authorize_workflow_run` directly."""
    issue = add_issue(database_session, name="org-a/disabled-before-retrieval")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    original_propose = workflow_tasks.propose

    def propose_then_disable(*args, **kwargs):
        result = original_propose(*args, **kwargs)
        disable_actor(database_session, actor_id)
        return result

    monkeypatch.setattr(workflow_tasks, "propose", propose_then_disable)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0
    assert database_session.query(HumanDecision).count() == 0

    stage_names = {
        attempt.stage
        for attempt in database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    }
    # The deterministic stages up through "propose" ran normally; the
    # checkpoint right after them caught the disablement and nothing
    # tenant-sensitive after it ever started.
    assert {
        "authorize",
        "classify",
        "retrieve_fixture_evidence",
        "assess",
        "propose",
    } <= stage_names
    assert "authorize_before_retrieval" in stage_names
    failed_checkpoint = (
        database_session.query(StageAttempt)
        .filter_by(analysis_id=analysis.id, stage="authorize_before_retrieval")
        .one()
    )
    assert failed_checkpoint.status == "failed"
    assert "retrieve_related_evidence" not in stage_names
    assert "ai_inference" not in stage_names
    assert "human_review" not in stage_names
    assert "authorize_before_persistence" not in stage_names


# --- 4. actor moved to another organization after enqueue -----------------------


def test_actor_moved_to_another_organization_after_enqueue_fails_closed(
    database_session: Session,
) -> None:
    issue = add_issue(database_session, name="org-a/moved-after-enqueue")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    # Still enabled, still a reviewer -- but now belongs to a different
    # organization than the one that owns this analysis's repository.
    move_actor_organization(database_session, actor_id, ISOLATION_DEMO_ORGANIZATION_ID)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0
    attempts = authorize_stage_attempts(database_session, analysis.id)
    assert attempts[0].status == "failed"
    assert "no longer belongs" in attempts[0].error


# --- 4b. actor organization changes between retrieval and AI inference ---------


def test_actor_organization_changes_between_retrieval_and_ai_inference_blocks_inference(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieval succeeds legitimately (the actor was still correctly
    scoped when it ran), then a stage-controlled hook on the real
    retrieval stage function moves the actor to a different organization
    immediately after it returns -- at the exact boundary before
    `authorize_before_ai_inference` runs -- proving AI inference itself
    is blocked, not merely a later, unrelated check."""
    issue = add_issue(database_session, name="org-a/moved-before-inference")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    original_retrieval_stage = workflow_tasks.run_retrieve_related_evidence_stage

    def retrieval_then_move_organization(*args, **kwargs):
        result = original_retrieval_stage(*args, **kwargs)
        move_actor_organization(database_session, actor_id, ISOLATION_DEMO_ORGANIZATION_ID)
        return result

    monkeypatch.setattr(
        workflow_tasks, "run_retrieve_related_evidence_stage", retrieval_then_move_organization
    )

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0

    stage_names = {
        attempt.stage
        for attempt in database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    }
    assert "retrieve_related_evidence" in stage_names  # it genuinely ran, legitimately
    assert "authorize_before_ai_inference" in stage_names
    failed_checkpoint = (
        database_session.query(StageAttempt)
        .filter_by(analysis_id=analysis.id, stage="authorize_before_ai_inference")
        .one()
    )
    assert failed_checkpoint.status == "failed"
    assert "ai_inference" not in stage_names
    assert "authorize_before_persistence" not in stage_names


# --- 5. analysis/issue/repository ownership-chain inconsistency -----------------


def test_repository_reassigned_to_another_organization_after_enqueue_fails_closed(
    database_session: Session,
) -> None:
    """A distinct scenario from actor-moved: here the *repository* is the
    one reassigned (e.g. a later administrative operation), not the
    actor -- proving the same ownership-consistency invariant catches
    drift from either side of the chain."""
    issue = add_issue(database_session, name="org-a/repo-reassigned")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    database_session.execute(
        text("UPDATE repositories SET tenant_id = :org_id WHERE id = :repo_id"),
        {"org_id": str(ISOLATION_DEMO_ORGANIZATION_ID), "repo_id": issue.repository_id},
    )
    database_session.commit()

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0
    attempts = authorize_stage_attempts(database_session, analysis.id)
    assert attempts[0].status == "failed"


# --- 5b. permission changes after retrieval, before Recommendation persistence --


def test_actor_role_downgraded_after_ai_inference_blocks_recommendation_persistence(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieval and AI inference both succeed legitimately -- the actor
    was a valid reviewer throughout. A stage-controlled hook on the real
    `human_review` function (the last deterministic stage before the
    final `authorize_before_persistence` checkpoint) downgrades the
    actor's role to `viewer` immediately after it returns. No
    transactional protection wraps the external AI call itself (it
    cannot be wrapped in a database transaction) -- which is exactly why
    this final checkpoint exists: even a fully legitimate narrative
    already generated by the AI stage must still be blocked from ever
    becoming a persisted `Recommendation` once the initiating actor no
    longer has permission to create one."""
    issue = add_issue(database_session, name="org-a/downgraded-before-persistence")
    actor_id = insert_actor(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, role="reviewer"
    )
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    original_human_review = workflow_tasks.human_review

    def human_review_then_downgrade(*args, **kwargs):
        result = original_human_review(*args, **kwargs)
        database_session.execute(
            text("UPDATE actors SET role = 'viewer' WHERE id = :id"), {"id": actor_id}
        )
        database_session.commit()
        return result

    monkeypatch.setattr(workflow_tasks, "human_review", human_review_then_downgrade)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0
    assert database_session.query(HumanDecision).count() == 0

    stage_names = {
        attempt.stage
        for attempt in database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    }
    assert {"retrieve_related_evidence", "ai_inference", "human_review"} <= stage_names
    assert "authorize_before_persistence" in stage_names
    failed_checkpoint = (
        database_session.query(StageAttempt)
        .filter_by(analysis_id=analysis.id, stage="authorize_before_persistence")
        .one()
    )
    assert failed_checkpoint.status == "failed"


# --- 6. forged or missing initiating actor reference -----------------------------


def test_missing_initiating_actor_reference_cannot_execute_the_workflow(
    database_session: Session,
) -> None:
    issue = add_issue(database_session, name="org-a/missing-reference")
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=None)
    database_session.add(analysis)
    database_session.commit()

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0
    attempts = authorize_stage_attempts(database_session, analysis.id)
    assert "no recorded initiating actor" in attempts[0].error


def test_a_forged_initiating_actor_reference_cannot_pass_the_authorize_stage(
    database_session: Session,
) -> None:
    """A genuinely nonexistent actor id can never actually be *persisted*
    on a real `Analysis` row -- `initiating_actor_id`'s own foreign key
    to `actors.id` rejects it at INSERT time, independent of this
    check. That is a stronger guarantee than an application-level check
    alone. This test proves the second, independent layer: even given a
    (necessarily in-memory, unpersisted) reference to an id no `Actor`
    row will ever resolve to, `_authorize_workflow_run` itself still
    fails closed rather than assuming best-effort trust."""
    issue = add_issue(database_session, name="org-a/forged-reference")
    forged_actor_id = uuid4()
    unpersisted_analysis = Analysis(issue_id=issue.id, status="queued")
    unpersisted_analysis.initiating_actor_id = forged_actor_id  # never flushed to PostgreSQL

    with pytest.raises(WorkflowAuthorizationError, match="unknown or disabled"):
        _authorize_workflow_run(database_session, unpersisted_analysis, issue)


def test_the_foreign_key_itself_rejects_a_forged_initiating_actor_reference(
    database_session: Session,
) -> None:
    """Defense-in-depth, confirmed directly: attempting to actually
    persist an `Analysis` row referencing a nonexistent actor id is
    rejected by `analyses.initiating_actor_id`'s own foreign key before
    the row is ever visible to a worker at all."""
    issue = add_issue(database_session, name="org-a/fk-rejects-forgery")
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=uuid4())
    database_session.add(analysis)

    with pytest.raises(IntegrityError):
        database_session.commit()
    database_session.rollback()


# --- 7. a normal authorized workflow still completes exactly once ---------------


def test_a_normal_authorized_workflow_completes_exactly_once(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session, name="org-a/normal-completion")

    response = client.post(
        f"/api/v1/issues/{issue.id}/triage",
        json={},
        headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)},
    )
    assert response.status_code == 202
    analysis_id = UUID(response.json()["analysis_id"])

    analysis = database_session.get(Analysis, analysis_id)
    assert analysis.status == "completed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis_id).count() == 1

    authorize_attempts = authorize_stage_attempts(database_session, analysis_id)
    assert len(authorize_attempts) == 1
    assert authorize_attempts[0].status == "succeeded"

    # Re-invoking (a redelivered/duplicate task) remains a safe no-op --
    # the pre-existing idempotency guarantee, undisturbed by adding the
    # authorize stage.
    result = process_workflow_run(database_session, analysis_id)
    assert result.status == "completed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis_id).count() == 1


# --- 8. existing retry/resume/idempotency behavior remains intact ---------------


def test_a_retry_after_a_transient_failure_re_authorizes_and_still_succeeds(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-existing retry/resume guarantee (see test_workflow_tasks.py)
    still holds with `authorize` as the new first stage: a transient
    failure in a later stage still leads to a clean retry that
    re-authorizes (a fresh attempt_number has no cached "succeeded"
    authorize result) and completes normally once the transient issue
    clears."""
    issue = add_issue(database_session, name="org-a/retry-reauthorizes")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    call_count = {"value": 0}
    original_classify = workflow_tasks.classify

    def flaky_classify(issue_arg):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise ValueError("transient failure")
        return original_classify(issue_arg)

    monkeypatch.setattr(workflow_tasks, "classify", flaky_classify)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)
    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert analysis.attempt_count == 1

    result = process_workflow_run(database_session, analysis.id)

    assert result.status == "completed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 1
    # Two attempt_numbers' worth of "authorize" ran (1 for the failed
    # attempt, 1 for the succeeded retry) -- proving authorize really did
    # re-run on the second attempt rather than being skipped.
    authorize_attempts = authorize_stage_attempts(database_session, analysis.id)
    assert len(authorize_attempts) == 2
    assert all(attempt.status == "succeeded" for attempt in authorize_attempts)


def test_resume_after_a_successful_authorize_stage_revalidates_and_fails_closed(
    database_session: Session,
) -> None:
    """Closes the exact gap flagged in this correction's second pass:
    authorization is dynamic state, not a resumable cached stage result.
    A crash right after the *initial* `authorize` checkpoint succeeds
    (attempt_count never incremented, matching a genuine mid-attempt
    crash -- as opposed to the exception-handling path, which always
    increments it before returning) leaves that one checkpoint's
    "succeeded" `StageAttempt` cached. The actor is then disabled during
    the crash window. On resume, the deterministic stages (classify
    through propose) still run from wherever they left off, but the
    *next* checkpoint this attempt has not yet executed --
    `authorize_before_retrieval` -- has no cached result to reuse and
    re-verifies fresh, catching the disablement and failing closed before
    retrieval ever starts. A previously successful `authorize` never
    permits later tenant-sensitive work once state has changed."""
    issue = add_issue(database_session, name="org-a/resume-revalidates")
    actor_id = insert_actor(database_session, organization_id=DEFAULT_ORGANIZATION_ID)
    analysis = Analysis(issue_id=issue.id, status="queued", initiating_actor_id=actor_id)
    database_session.add(analysis)
    database_session.commit()

    attempt_number = analysis.attempt_count + 1
    workflow_tasks._run_stage(
        database_session,
        analysis,
        "authorize",
        attempt_number,
        _authorize_workflow_run,
        database_session,
        analysis,
        issue,
    )
    authorize_attempts_before = authorize_stage_attempts(database_session, analysis.id)
    assert len(authorize_attempts_before) == 1
    assert authorize_attempts_before[0].status == "succeeded"

    # The actor is disabled *after* the initial authorize checkpoint
    # already succeeded for this same attempt -- simulating the crash
    # window.
    disable_actor(database_session, actor_id)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0

    # The cached "authorize" success was reused (still exactly one row),
    # but the next, not-yet-run checkpoint caught the disablement fresh.
    assert len(authorize_stage_attempts(database_session, analysis.id)) == 1
    stage_names = {
        attempt.stage
        for attempt in database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    }
    assert "authorize_before_retrieval" in stage_names
    failed_checkpoint = (
        database_session.query(StageAttempt)
        .filter_by(analysis_id=analysis.id, stage="authorize_before_retrieval")
        .one()
    )
    assert failed_checkpoint.status == "failed"
    assert "retrieve_related_evidence" not in stage_names
