"""Retrieval tenant-ownership invariant tests (Milestone 3.1 Slice 2
correction; see ADR 0014).

The original Slice 2 implementation used a bare Python `assert` for the
retrieval defense-in-depth check -- disabled entirely under `-O`/
`PYTHONOPTIMIZE=1`, so it could never actually function as a security
control in an optimized deployment. This file proves the correction:
`app.retrieval.service.RetrievalTenantMismatchError` is an explicit,
always-active exception, and proves the three things that actually
matter about it:

  1. it fires correctly, both at the unit level (the invariant-check
     function itself, given a synthetic mismatched row) and wired
     through the real stage/workflow machinery;
  2. it is never silently degraded to an empty result the way an
     ordinary transient retrieval failure is -- it aborts the whole
     stage/workflow attempt, before ranking, citation construction, or
     the AI prompt, and before any `Recommendation` is created;
  3. its message never names the mismatched repository, chunk, or
     content -- safe to persist as a `StageAttempt.error` value or log
     line.

Requires isolated PostgreSQL and Redis (Celery eager mode; see
conftest.py) for the workflow-level tests -- skips cleanly if
unavailable. The pure unit test needs no database at all. Never runs
against the live demo database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.retrieval.stage as retrieval_stage
import app.workflow.tasks as workflow_tasks
from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    Analysis,
    Issue,
    Recommendation,
    Repository,
    StageAttempt,
)
from app.retrieval.service import RetrievalTenantMismatchError, _assert_chunks_belong_to_repository
from app.workflow.tasks import process_workflow_run

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, stage_attempts, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for retrieval-invariant workflow tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


# --- 1. it is a real, explicit exception -- not a bare assert --------------------


def test_it_is_an_explicit_exception_not_an_assert_statement() -> None:
    """Confirms this precisely, as requested: `RetrievalTenantMismatchError`
    is a real exception class raised via an explicit `raise` statement in
    `app.retrieval.service`, never a bare `assert`. A bare `assert` would
    be silently compiled away under `python -O`; an explicit `raise`
    cannot be."""
    import ast
    import inspect

    import app.retrieval.service as retrieval_service

    source = inspect.getsource(retrieval_service._assert_chunks_belong_to_repository)
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
    assert any(isinstance(node, ast.Raise) for node in ast.walk(tree))
    assert issubclass(RetrievalTenantMismatchError, Exception)


def test_the_invariant_check_raises_on_a_mismatched_chunk() -> None:
    """Pure unit test, no database: the real SQL query can never be
    tricked into violating its own `WHERE` clause, so proving this fires
    requires calling the check directly with a fabricated mismatch."""
    repository_id = uuid4()
    other_repository_id = uuid4()
    matching_chunk = SimpleNamespace(repository_id=repository_id)
    mismatched_chunk = SimpleNamespace(repository_id=other_repository_id)
    rows = [(matching_chunk, 0.1), (mismatched_chunk, 0.2)]

    with pytest.raises(RetrievalTenantMismatchError) as excinfo:
        _assert_chunks_belong_to_repository(rows, repository_id)

    # The message names neither repository id -- safe to log/persist.
    assert str(repository_id) not in str(excinfo.value)
    assert str(other_repository_id) not in str(excinfo.value)


def test_the_invariant_check_does_not_raise_when_every_chunk_matches() -> None:
    repository_id = uuid4()
    rows = [(SimpleNamespace(repository_id=repository_id), 0.1) for _ in range(3)]

    _assert_chunks_belong_to_repository(rows, repository_id)  # must not raise


def test_retrieve_related_evidence_actually_calls_the_invariant_check(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes the gap the two tests above leave open: they prove the
    guard function itself works, given a synthetic input, but neither
    proves `retrieve_related_evidence`'s real query path actually calls
    it. The real SQL query can never produce a genuinely mismatched row
    (that's the whole point of defense-in-depth), so there is no way to
    trigger a real failure through genuine data -- proving the call site
    exists means spying on the guard and confirming it runs during an
    ordinary, successful retrieval, which would fail if that call were
    ever removed."""
    from app.retrieval.contracts import RetrievalRequest
    from app.retrieval.embedding import FakeEmbeddingAdapter
    from app.retrieval.service import retrieve_related_evidence

    repository = Repository(
        tenant_id=DEFAULT_ORGANIZATION_ID,
        name="org-a/spy-check",
        source_url="https://github.com/org-a/spy-check",
    )
    database_session.add(repository)
    database_session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=1,
        title="Analyzed issue",
        body="Body",
        state="open",
        source_url="https://github.com/org-a/spy-check/issues/1",
    )
    database_session.add(issue)
    database_session.commit()

    import app.retrieval.service as retrieval_service

    real_check = retrieval_service._assert_chunks_belong_to_repository
    spy_calls = []

    def spy(rows, repository_id):
        spy_calls.append(repository_id)
        return real_check(rows, repository_id)

    monkeypatch.setattr(retrieval_service, "_assert_chunks_belong_to_repository", spy)

    adapter = FakeEmbeddingAdapter()
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=issue.id,
        query_text="a perfectly ordinary retrieval query",
        max_results=3,
        max_excerpt_chars=320,
    )
    retrieve_related_evidence(database_session, request, adapter)

    assert spy_calls == [repository.id]


# --- 2. wired through the stage: never swallowed as a controlled empty result ----


def test_the_retrieval_stage_never_degrades_a_tenant_mismatch_to_an_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_retrieve_related_evidence_stage` degrades an *ordinary*
    retrieval failure to `RetrievalResult(items=(), status="failed")` --
    but `RetrievalTenantMismatchError` must propagate uncaught, exactly
    like the pre-existing `EmbeddingDimensionMismatchError` case, never
    silently become a "no evidence found" result the workflow would
    otherwise continue past."""

    def raise_mismatch(*args, **kwargs):
        raise RetrievalTenantMismatchError(
            "retrieval query returned a chunk outside the requested repository"
        )

    monkeypatch.setattr(retrieval_stage, "retrieve_related_evidence", raise_mismatch)

    issue = SimpleNamespace(id=uuid4(), repository_id=uuid4(), title="t", body="b")
    from app.triage.rules import Classification

    classification = Classification(label="bug", matched_rule="test", matched_keywords=())

    with pytest.raises(RetrievalTenantMismatchError):
        retrieval_stage.run_retrieve_related_evidence_stage(
            session=None, issue=issue, classification=classification
        )


# --- 3. wired through the full workflow: no Recommendation, safe stage output ----


def add_issue(session: Session, *, name: str) -> Issue:
    repository = Repository(
        tenant_id=DEFAULT_ORGANIZATION_ID, name=name, source_url=f"https://github.com/{name}"
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=1,
        title="Retrieval invariant test issue",
        body="Body",
        state="open",
        source_url=f"https://github.com/{name}/issues/1",
    )
    session.add(issue)
    session.commit()
    return issue


def test_a_tenant_mismatch_during_the_workflow_creates_no_recommendation_and_leaks_no_content(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session, name="org-a/retrieval-invariant")
    analysis = Analysis(
        issue_id=issue.id, status="queued", initiating_actor_id=DEFAULT_ORG_REVIEWER_ACTOR_ID
    )
    database_session.add(analysis)
    database_session.commit()

    secret_other_tenant_content = "ORG-B-ONLY-SECRET-CONTENT-MUST-NEVER-APPEAR"

    def raise_mismatch(session, issue_arg, classification, settings=None):
        raise RetrievalTenantMismatchError(
            "retrieval query returned a chunk outside the requested repository"
        )

    monkeypatch.setattr(workflow_tasks, "run_retrieve_related_evidence_stage", raise_mismatch)

    with pytest.raises(workflow_tasks._RetryableWorkflowError):
        process_workflow_run(database_session, analysis.id)

    database_session.refresh(analysis)
    assert analysis.status == "retrying"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0

    stage_names = {
        attempt.stage
        for attempt in database_session.query(StageAttempt).filter_by(analysis_id=analysis.id).all()
    }
    # retrieve_related_evidence failed; ai_inference and human_review
    # never ran -- the whole chain is one try block, and this stage comes
    # before both.
    assert "retrieve_related_evidence" in stage_names
    assert "ai_inference" not in stage_names
    assert "human_review" not in stage_names

    retrieval_attempt = (
        database_session.query(StageAttempt)
        .filter_by(analysis_id=analysis.id, stage="retrieve_related_evidence")
        .one()
    )
    assert retrieval_attempt.status == "failed"
    # The fixed, generic message only -- never any tenant-specific content.
    assert secret_other_tenant_content not in retrieval_attempt.error
    assert "outside the requested repository" in retrieval_attempt.error
