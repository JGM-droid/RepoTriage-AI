"""Database-enforced append-only audit-event behavior (Milestone 3.2)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.api.scoping import audit_events_query_for_tenant
from app.models.core import (
    DEFAULT_ORGANIZATION_ID,
    ISOLATION_DEMO_ORGANIZATION_ID,
    Actor,
    AuditEvent,
    Issue,
    Repository,
)
from app.triage.service import run_triage, status_history
from tests.db_maintenance import truncate_for_test

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, stage_attempts, "
    "analyses, issues, repositories CASCADE"
)
APPEND_ONLY_ERROR = "audit_events is append-only"


@pytest.fixture()
def engine():
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for append-only audit tests.")
    value = create_engine(database_url, connect_args={"connect_timeout": 3})
    with value.begin() as connection:
        truncate_for_test(connection, TRUNCATE_CORE_TABLES)
    try:
        yield value
    finally:
        value.dispose()


@pytest.fixture()
def seeded(engine):
    with Session(engine) as session:
        repository = Repository(
            tenant_id=DEFAULT_ORGANIZATION_ID,
            name=f"audit/test-{uuid4()}",
            source_url=f"https://example.test/audit/{uuid4()}",
        )
        session.add(repository)
        session.flush()
        issue = Issue(
            repository_id=repository.id,
            external_number=1,
            title="Append-only audit test",
            body="Traceback",
            state="open",
            source_url=f"{repository.source_url}/issues/1",
        )
        session.add(issue)
        session.flush()
        event = AuditEvent(
            repository_id=repository.id,
            issue_id=issue.id,
            event_type="test_event",
            metadata_={"stable": True, "value": 1},
        )
        session.add(event)
        session.commit()
        return repository.id, issue.id, event.id


def _snapshot(session: Session, event_id):
    event = session.get(AuditEvent, event_id)
    assert event is not None
    return (
        event.id,
        event.repository_id,
        event.issue_id,
        event.actor_id,
        event.event_type,
        event.metadata_,
        event.created_at,
        event.updated_at,
    )


def test_insert_read_and_deterministic_workflow_history_remain_allowed(engine) -> None:
    with Session(engine) as session:
        repository = Repository(
            tenant_id=DEFAULT_ORGANIZATION_ID,
            name="audit/allowed",
            source_url="https://example.test/audit/allowed",
        )
        session.add(repository)
        session.flush()
        issue = Issue(
            repository_id=repository.id,
            external_number=1,
            title="Crash on startup",
            body="Traceback",
            state="open",
            source_url="https://example.test/audit/allowed/issues/1",
        )
        session.add(issue)
        session.commit()

        analysis = run_triage(session, issue)
        assert [status for status, _ in status_history(session, issue.id, analysis.id)] == [
            "queued",
            "running",
            "completed",
        ]
        assert session.scalars(
            audit_events_query_for_tenant(DEFAULT_ORGANIZATION_ID).order_by(
                AuditEvent.created_at, AuditEvent.id
            )
        ).all()


def test_orm_update_is_rejected_and_rollback_preserves_the_row(engine, seeded) -> None:
    _repository_id, _issue_id, event_id = seeded
    with Session(engine) as session:
        before = _snapshot(session, event_id)
        event = session.get(AuditEvent, event_id)
        event.event_type = "rewritten"
        with pytest.raises(DBAPIError, match=APPEND_ONLY_ERROR):
            session.commit()
        session.rollback()
        assert _snapshot(session, event_id) == before
        session.add(
            AuditEvent(repository_id=before[1], issue_id=before[2], event_type="after_rollback")
        )
        session.commit()


def test_direct_sql_update_is_rejected(engine, seeded) -> None:
    _repository_id, _issue_id, event_id = seeded
    with engine.connect() as connection:
        with pytest.raises(DBAPIError, match=APPEND_ONLY_ERROR):
            with connection.begin():
                connection.execute(
                    text("UPDATE audit_events SET event_type = 'rewritten' WHERE id = :id"),
                    {"id": event_id},
                )


def test_orm_delete_is_rejected(engine, seeded) -> None:
    _repository_id, _issue_id, event_id = seeded
    with Session(engine) as session:
        session.delete(session.get(AuditEvent, event_id))
        with pytest.raises(DBAPIError, match=APPEND_ONLY_ERROR):
            session.commit()


def test_direct_sql_delete_is_rejected(engine, seeded) -> None:
    _repository_id, _issue_id, event_id = seeded
    with engine.connect() as connection:
        with pytest.raises(DBAPIError, match=APPEND_ONLY_ERROR):
            with connection.begin():
                connection.execute(
                    text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id}
                )


def test_bulk_update_and_delete_are_rejected(engine, seeded) -> None:
    with Session(engine) as session:
        with pytest.raises(DBAPIError, match=APPEND_ONLY_ERROR):
            session.execute(update(AuditEvent).values(event_type="rewritten"))
        session.rollback()
        with pytest.raises(DBAPIError, match=APPEND_ONLY_ERROR):
            session.execute(delete(AuditEvent))


def test_truncate_is_rejected(engine, seeded) -> None:
    with engine.connect() as connection:
        with pytest.raises(DBAPIError, match=APPEND_ONLY_ERROR):
            with connection.begin():
                connection.execute(text("TRUNCATE audit_events"))


def test_parent_deletion_cannot_remove_audit_history(engine, seeded) -> None:
    _repository_id, issue_id, event_id = seeded
    with engine.connect() as connection:
        with pytest.raises(IntegrityError, match="fk_audit_events_issue_id_issues"):
            with connection.begin():
                connection.execute(text("DELETE FROM issues WHERE id = :id"), {"id": issue_id})
    with Session(engine) as session:
        assert session.get(AuditEvent, event_id) is not None


def test_tenant_scoped_reads_and_existing_role_contract(engine, seeded) -> None:
    with Session(engine) as session:
        default_actors = session.scalars(
            select(Actor).where(Actor.organization_id == DEFAULT_ORGANIZATION_ID)
        ).all()
        assert {actor.role for actor in default_actors} == {
            "viewer",
            "reviewer",
            "administrator",
        }
        for actor in default_actors:
            assert (
                len(session.scalars(audit_events_query_for_tenant(actor.organization_id)).all())
                == 1
            )
        assert (
            session.scalars(audit_events_query_for_tenant(ISOLATION_DEMO_ORGANIZATION_ID)).all()
            == []
        )


def test_concurrent_inserts_are_independent_and_preserved(engine, seeded) -> None:
    repository_id, issue_id, _event_id = seeded

    def insert_event(sequence: int):
        with Session(engine) as session:
            event = AuditEvent(
                repository_id=repository_id,
                issue_id=issue_id,
                event_type="concurrent_insert",
                metadata_={"sequence": sequence},
            )
            session.add(event)
            session.commit()
            return event.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(insert_event, (1, 2)))
    assert ids[0] != ids[1]
    with Session(engine) as session:
        assert (
            session.scalar(
                select(text("count(*)")).select_from(AuditEvent).where(AuditEvent.id.in_(ids))
            )
            == 2
        )
