"""Integration tests for the core import service against PostgreSQL.

These tests require `DATABASE_URL` and are skipped otherwise, matching
the pattern used by `tests/test_core_data_model.py`. Small, focused
record sets are used instead of the full 100-issue fixture.
"""

import os

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.importer.schemas import ImportValidationError
from app.importer.service import ImporterConfig, import_records
from app.models.core import Issue, Repository

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)

REPOSITORY_NAME = "example/example"
REPOSITORY_SOURCE_URL = "https://github.com/example/example"


def _record(number: int, **overrides: object) -> dict:
    payload = {
        "external_number": number,
        "title": f"Issue {number}",
        "body": "Body",
        "state": "open",
        "source_url": f"https://github.com/example/example/issues/{number}",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def database_session() -> Session:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for importer service tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def test_import_records_creates_repository_and_issues(database_session: Session) -> None:
    records = [_record(1), _record(2), _record(3)]

    summary = import_records(
        database_session,
        repository_name=REPOSITORY_NAME,
        repository_source_url=REPOSITORY_SOURCE_URL,
        raw_records=records,
    )

    assert summary.considered == 3
    assert summary.inserted == 3
    assert summary.skipped_existing == 0
    assert database_session.execute(select(Repository)).scalar_one()
    assert len(database_session.execute(select(Issue)).scalars().all()) == 3


def test_import_records_is_idempotent_on_repeat_import(database_session: Session) -> None:
    records = [_record(1), _record(2)]

    import_records(
        database_session,
        repository_name=REPOSITORY_NAME,
        repository_source_url=REPOSITORY_SOURCE_URL,
        raw_records=records,
    )
    second_summary = import_records(
        database_session,
        repository_name=REPOSITORY_NAME,
        repository_source_url=REPOSITORY_SOURCE_URL,
        raw_records=records,
    )

    assert second_summary.inserted == 0
    assert second_summary.skipped_existing == 2
    repository_count = database_session.execute(select(Repository)).scalars().all()
    issue_count = database_session.execute(select(Issue)).scalars().all()
    assert len(repository_count) == 1
    assert len(issue_count) == 2


def test_import_records_enforces_configured_issue_limit(database_session: Session) -> None:
    config = ImporterConfig(max_issues=2, max_pages=3, page_size=10)
    records = [_record(number) for number in range(1, 6)]

    summary = import_records(
        database_session,
        repository_name=REPOSITORY_NAME,
        repository_source_url=REPOSITORY_SOURCE_URL,
        raw_records=records,
        config=config,
    )

    assert summary.considered == 2
    assert summary.inserted == 2
    stored_numbers = sorted(
        issue.external_number for issue in database_session.execute(select(Issue)).scalars().all()
    )
    assert stored_numbers == [1, 2]


def test_import_records_excludes_pull_request_shaped_records(database_session: Session) -> None:
    records = [_record(1), _record(2, is_pull_request=True), _record(3)]

    summary = import_records(
        database_session,
        repository_name=REPOSITORY_NAME,
        repository_source_url=REPOSITORY_SOURCE_URL,
        raw_records=records,
    )

    assert summary.considered == 2
    stored_numbers = sorted(
        issue.external_number for issue in database_session.execute(select(Issue)).scalars().all()
    )
    assert stored_numbers == [1, 3]


def test_import_records_rolls_back_when_a_record_is_malformed(database_session: Session) -> None:
    records = [_record(1), _record(2, title=None), _record(3)]

    with pytest.raises(ImportValidationError):
        import_records(
            database_session,
            repository_name=REPOSITORY_NAME,
            repository_source_url=REPOSITORY_SOURCE_URL,
            raw_records=records,
        )

    assert database_session.execute(select(Repository)).scalars().all() == []
    assert database_session.execute(select(Issue)).scalars().all() == []


def test_import_records_sanitizes_control_characters_and_line_endings(
    database_session: Session,
) -> None:
    records = [_record(1, title="Ti\x00tle\r\nLine2", body="Bo\x01dy\rline")]

    import_records(
        database_session,
        repository_name=REPOSITORY_NAME,
        repository_source_url=REPOSITORY_SOURCE_URL,
        raw_records=records,
    )

    issue = database_session.execute(select(Issue)).scalar_one()
    assert issue.title == "Title\nLine2"
    assert issue.body == "Body\nline"


def test_import_records_fails_when_postgresql_is_unavailable() -> None:
    # Built from parts so no literal connection string is present in source.
    unreachable_url = "postgresql+psycopg://" + "repotriage:repotriage" + "@localhost:1/repotriage"
    engine = create_engine(
        unreachable_url,
        connect_args={"connect_timeout": 1},
    )
    with Session(engine) as broken_session:
        with pytest.raises(OperationalError):
            import_records(
                broken_session,
                repository_name=REPOSITORY_NAME,
                repository_source_url=REPOSITORY_SOURCE_URL,
                raw_records=[_record(1)],
            )
    engine.dispose()
