import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.workflow.tasks as workflow_tasks
from app.api.v1.triage import get_triage_session
from app.main import app
from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    Analysis,
    Issue,
    Recommendation,
    Repository,
    StageAttempt,
)
from app.triage.service import status_history
from tests.db_maintenance import truncate_for_test

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
        # Reviewer role: this file starts triage and polls its status,
        # which requires reviewer/administrator to start (Milestone 3.1
        # Slice 2; see ADR 0014).
        yield TestClient(app, headers={"X-Demo-Actor-ID": str(DEFAULT_ORG_REVIEWER_ACTOR_ID)})
    finally:
        app.dependency_overrides.clear()


def add_issue(
    session: Session,
    *,
    title: str = "App crash on save",
    body: str = "Traceback included",
    state: str = "open",
    external_number: int = 1,
) -> Issue:
    repository = Repository(
        tenant_id=DEFAULT_ORGANIZATION_ID,
        name=f"pallets/flask-{external_number}",
        source_url=f"https://github.com/pallets/flask-{external_number}",
    )
    session.add(repository)
    session.flush()
    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title=title,
        body=body,
        state=state,
        source_url=f"https://github.com/pallets/flask/issues/{external_number}",
    )
    session.add(issue)
    session.commit()
    return issue


def test_start_triage_returns_202_without_waiting_for_completion(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    assert response.status_code == 202
    payload = response.json()
    assert payload["issue_id"] == str(issue.id)
    assert payload["status"] == "queued"
    assert payload["poll_url"] == f"/api/v1/issues/{issue.id}/triage"
    assert "analysis_id" in payload


def test_an_oversized_ruleset_version_is_rejected_safely(
    client: TestClient, database_session: Session
) -> None:
    """Milestone 2.6: TriageRequest.ruleset_version is now bounded --
    proves the bound is enforced (422) and the error body stays safe
    (no stack trace, no internal representation)."""
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={"ruleset_version": "9" * 21})

    assert response.status_code == 422
    assert "Traceback" not in response.text
    assert "site-packages" not in response.text


def test_polling_after_start_returns_the_completed_result_with_separated_sections(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)
    client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    collision_time = datetime(2026, 1, 1, tzinfo=UTC)
    updated = database_session.query(StageAttempt).update(
        {StageAttempt.created_at: collision_time}, synchronize_session=False
    )
    assert updated == len(workflow_tasks.STAGE_ORDER)
    database_session.commit()

    response = client.get(f"/api/v1/issues/{issue.id}/triage")

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
    assert [attempt["stage"] for attempt in payload["stage_attempts"]] == [
        "authorize",
        "classify",
        "retrieve_fixture_evidence",
        "assess",
        "propose",
        "authorize_before_retrieval",
        "retrieve_related_evidence",
        "authorize_before_ai_inference",
        "ai_inference",
        "human_review",
        "authorize_before_persistence",
    ]
    assert all(attempt["status"] == "succeeded" for attempt in payload["stage_attempts"])

    # No other issues exist in this isolated test database, so retrieval
    # explicitly finds nothing — a valid, non-error outcome, distinct from
    # both "not run yet" and a failure.
    assert payload["retrieved_evidence"]["status"] == "empty"
    assert payload["retrieved_evidence"]["items"] == []

    # The AI-generated narrative supplements, but never replaces, the
    # deterministic sections above; the default demo/test provider is the
    # zero-cost mock adapter.
    assert payload["ai_inference"]["provider"] == "mock"
    assert payload["ai_inference"]["status"] == "succeeded"
    assert payload["ai_inference"]["input_tokens"] == 0
    assert payload["ai_inference"]["estimated_cost_usd"] == 0.0
    assert payload["ai_inference"]["narrative"]
    assert payload["ai_inference"]["citations"] == []

    # Prompt-registry provenance (Milestone 2.4) is exposed through the API
    # for technical inspection, even though the frontend's normal UI only
    # surfaces prompt_id/prompt_version, not the hashes.
    assert payload["ai_inference"]["prompt_id"] == "triage_narrative"
    assert payload["ai_inference"]["prompt_version"] == "1.1.0"
    assert payload["ai_inference"]["prompt_status"] == "released"
    assert len(payload["ai_inference"]["prompt_template_hash"]) == 64  # sha256 hex digest
    assert len(payload["ai_inference"]["rendered_prompt_hash"]) == 64

    # Redaction-policy provenance (Milestone 2.6 correction) is exposed the
    # same way -- always present, even with zero events, so "nothing was
    # redacted" is attributable to a specific policy version via the API.
    assert payload["ai_inference"]["redaction_events"] == []
    assert payload["ai_inference"]["redaction_policy_id"] == "provider_input_redaction"
    assert payload["ai_inference"]["redaction_policy_version"] == "1.0.0"
    assert payload["ai_inference"]["redaction_policy_status"] == "released"
    assert len(payload["ai_inference"]["redaction_policy_hash"]) == 64


def test_get_triage_is_backward_compatible_with_pre_2_3_recommendations(
    client: TestClient, database_session: Session
) -> None:
    """A recommendation written before Milestone 2.3 has no
    "retrieved_evidence" key in its stored content at all — the API must
    treat that as absent, not crash."""
    issue = add_issue(database_session)
    analysis = Analysis(issue_id=issue.id, status="completed")
    database_session.add(analysis)
    database_session.commit()
    pre_2_3_content = {
        "ruleset_version": "1.0",
        "classification": {
            "label": "bug-crash",
            "matched_rule": "crash-keyword",
            "matched_keywords": ["crash"],
        },
        "evidence": [],
        "assessment": {"severity": "high", "rationale": "r"},
        "proposed_action": {"action": "a", "rationale": "r"},
        "human_review": {
            "recommendation_status": "proposed",
            "human_review_status": "awaiting_human_review",
            "decision": None,
        },
    }
    database_session.add(
        Recommendation(
            analysis_id=analysis.id, status="proposed", content=json.dumps(pre_2_3_content)
        )
    )
    database_session.commit()

    response = client.get(f"/api/v1/issues/{issue.id}/triage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["retrieved_evidence"] is None
    assert payload["ai_inference"] is None
    assert payload["classification"]["label"] == "bug-crash"  # deterministic sections unaffected


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


def test_a_permanent_stage_failure_exposes_the_full_queued_running_failed_lifecycle(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error(*args, **kwargs):
        raise ValueError("forced permanent failure")

    monkeypatch.setattr(workflow_tasks, "classify", raise_error)
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})
    assert response.status_code == 202

    analysis_id = database_session.query(Analysis).filter_by(issue_id=issue.id).one().id
    history = status_history(database_session, issue.id, analysis_id)
    assert [transition_status for transition_status, _ in history] == [
        "queued",
        "running",
        "retrying",
        "running",
        "retrying",
        "running",
        "failed",
    ]

    result = client.get(f"/api/v1/issues/{issue.id}/triage")
    assert result.json()["status"] == "failed"


def test_idempotency_key_returns_the_existing_workflow_for_the_same_issue(
    client: TestClient, database_session: Session
) -> None:
    issue = add_issue(database_session)

    first = client.post(
        f"/api/v1/issues/{issue.id}/triage",
        json={},
        headers={"Idempotency-Key": "same-key"},
    )
    second = client.post(
        f"/api/v1/issues/{issue.id}/triage",
        json={},
        headers={"Idempotency-Key": "same-key"},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["analysis_id"] == second.json()["analysis_id"]
    assert database_session.query(Analysis).filter_by(issue_id=issue.id).count() == 1


def test_idempotency_key_reuse_across_issues_is_rejected(
    client: TestClient, database_session: Session
) -> None:
    issue_a = add_issue(database_session, title="Issue A", external_number=1)
    issue_b = add_issue(database_session, title="Issue B", external_number=2)

    first = client.post(
        f"/api/v1/issues/{issue_a.id}/triage",
        json={},
        headers={"Idempotency-Key": "shared-key"},
    )
    assert first.status_code == 202

    conflict = client.post(
        f"/api/v1/issues/{issue_b.id}/triage",
        json={},
        headers={"Idempotency-Key": "shared-key"},
    )

    assert conflict.status_code == 409
    assert conflict.json() == {
        "error": "idempotency_key_conflict",
        "message": "This idempotency key is already associated with a different issue.",
    }
