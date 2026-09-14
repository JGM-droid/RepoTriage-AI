import json
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.triage.service as triage_service
from app.models.core import AuditEvent, HumanDecision, Issue, Recommendation, Repository
from app.triage.service import (
    TriageStageError,
    execute_triage,
    run_triage,
    start_triage,
    status_history,
)

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for triage workflow tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def add_issue(
    session: Session,
    *,
    title: str = "App crash on startup",
    body: str = "Traceback attached below",
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


def test_start_triage_persists_a_queued_analysis(database_session: Session) -> None:
    issue = add_issue(database_session)

    analysis = start_triage(database_session, issue)

    assert analysis.status == "queued"
    assert analysis.issue_id == issue.id


def test_execute_triage_sets_running_before_stage_execution(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session)
    analysis = start_triage(database_session, issue)
    assert analysis.status == "queued"

    observed: dict[str, str] = {}
    original_classify = triage_service.classify

    def spy(issue_arg: Issue):
        observed["status_during_classify"] = analysis.status
        return original_classify(issue_arg)

    monkeypatch.setattr(triage_service, "classify", spy)

    result = execute_triage(database_session, analysis, issue)

    assert observed["status_during_classify"] == "running"
    assert result.status == "completed"


def test_run_triage_executes_stages_in_exact_order(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session)
    call_order: list[str] = []

    def record(name: str, original):
        def wrapper(*args, **kwargs):
            call_order.append(name)
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(triage_service, "classify", record("classify", triage_service.classify))
    monkeypatch.setattr(
        triage_service,
        "retrieve_fixture_evidence",
        record("retrieve_fixture_evidence", triage_service.retrieve_fixture_evidence),
    )
    monkeypatch.setattr(triage_service, "assess", record("assess", triage_service.assess))
    monkeypatch.setattr(triage_service, "propose", record("propose", triage_service.propose))
    monkeypatch.setattr(
        triage_service, "human_review", record("human_review", triage_service.human_review)
    )

    analysis = run_triage(database_session, issue)

    assert call_order == [
        "classify",
        "retrieve_fixture_evidence",
        "assess",
        "propose",
        "human_review",
    ]
    assert analysis.status == "completed"


def test_run_triage_is_deterministic_for_the_same_issue(database_session: Session) -> None:
    issue = add_issue(database_session)

    first = run_triage(database_session, issue)
    second = run_triage(database_session, issue)

    first_content = json.loads(
        database_session.query(Recommendation).filter_by(analysis_id=first.id).one().content
    )
    second_content = json.loads(
        database_session.query(Recommendation).filter_by(analysis_id=second.id).one().content
    )

    assert first_content == second_content


def test_execute_triage_marks_failed_on_forced_stage_error(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session)
    analysis = start_triage(database_session, issue)

    def raise_error(*args, **kwargs):
        raise ValueError("forced failure")

    monkeypatch.setattr(triage_service, "classify", raise_error)

    with pytest.raises(TriageStageError):
        execute_triage(database_session, analysis, issue)

    database_session.refresh(analysis)
    assert analysis.status == "failed"
    assert database_session.query(Recommendation).filter_by(analysis_id=analysis.id).count() == 0


def test_completed_triage_separates_evidence_inference_and_proposal(
    database_session: Session,
) -> None:
    issue = add_issue(
        database_session,
        title="Security vulnerability report",
        body="Potential CVE found in dependency",
    )

    analysis = run_triage(database_session, issue)

    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    content = json.loads(recommendation.content)

    assert content["classification"]["label"] == "security-concern"
    assert content["evidence"]
    assert content["assessment"]["severity"] == "high"
    assert content["proposed_action"]["action"]
    assert content["human_review"]["recommendation_status"] == "proposed"
    assert content["human_review"]["human_review_status"] == "awaiting_human_review"
    assert content["human_review"]["decision"] is None
    assert recommendation.status == "proposed"


def test_completed_triage_never_creates_a_human_decision(database_session: Session) -> None:
    issue = add_issue(database_session)

    run_triage(database_session, issue)

    assert database_session.query(HumanDecision).count() == 0


def test_status_history_exposes_the_full_lifecycle_for_a_successful_run(
    database_session: Session,
) -> None:
    issue = add_issue(database_session)

    analysis = run_triage(database_session, issue)

    history = status_history(database_session, issue.id, analysis.id)
    assert [transition_status for transition_status, _ in history] == [
        "queued",
        "running",
        "completed",
    ]


def test_status_history_exposes_the_full_lifecycle_for_a_forced_failure(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = add_issue(database_session)
    analysis = start_triage(database_session, issue)

    def raise_error(*args, **kwargs):
        raise ValueError("forced failure")

    monkeypatch.setattr(triage_service, "classify", raise_error)

    with pytest.raises(TriageStageError):
        execute_triage(database_session, analysis, issue)

    history = status_history(database_session, issue.id, analysis.id)
    assert [transition_status for transition_status, _ in history] == [
        "queued",
        "running",
        "failed",
    ]


def test_status_history_transitions_are_recorded_as_audit_events(
    database_session: Session,
) -> None:
    issue = add_issue(database_session)

    analysis = run_triage(database_session, issue)

    events = (
        database_session.query(AuditEvent)
        .filter_by(issue_id=issue.id, event_type="triage_status_transition")
        .all()
    )
    assert len(events) == 3
    assert all(event.metadata_["analysis_id"] == str(analysis.id) for event in events)
