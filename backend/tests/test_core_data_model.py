import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)
INSERT_ISSUE = (
    "INSERT INTO issues "
    "(id, repository_id, external_number, title, body, state, source_url) "
    "VALUES (:id, :repository_id, :external_number, :title, :body, :state, :source_url)"
)


@pytest.fixture()
def database_session() -> Session:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for core data-model tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
            session.rollback()
    finally:
        engine.dispose()


def insert_repository(session: Session) -> str:
    repository_id = uuid4()
    session.execute(
        text("INSERT INTO repositories (id, name, source_url) VALUES (:id, :name, :url)"),
        {
            "id": repository_id,
            "name": "example",
            "url": f"https://github.com/example/{repository_id}",
        },
    )
    session.commit()
    return str(repository_id)


def test_issue_requires_a_valid_repository(database_session: Session) -> None:
    with pytest.raises(IntegrityError):
        database_session.execute(
            text(
                "INSERT INTO issues "
                "(id, repository_id, external_number, title, body, state, source_url) "
                "VALUES (:id, :repository_id, 1, 'Title', 'Body', 'open', "
                "'https://example.test/issues/1')"
            ),
            {"id": uuid4(), "repository_id": uuid4()},
        )
        database_session.commit()


def test_issue_number_is_unique_within_its_repository(database_session: Session) -> None:
    repository_id = insert_repository(database_session)
    values = {
        "repository_id": repository_id,
        "external_number": 1,
        "title": "Title",
        "body": "Body",
        "state": "open",
        "source_url": "https://example.test/issues/1",
    }
    database_session.execute(
        text(INSERT_ISSUE),
        {**values, "id": uuid4()},
    )
    database_session.commit()

    with pytest.raises(IntegrityError):
        database_session.execute(
            text(INSERT_ISSUE),
            {**values, "id": uuid4(), "source_url": "https://example.test/issues/duplicate"},
        )
        database_session.commit()


def test_issue_core_fields_are_required(database_session: Session) -> None:
    repository_id = insert_repository(database_session)

    with pytest.raises(IntegrityError):
        database_session.execute(
            text(
                "INSERT INTO issues "
                "(id, repository_id, external_number, title, body, state, source_url) "
                "VALUES (:id, :repository_id, 1, NULL, 'Body', 'open', "
                "'https://example.test/issues/1')"
            ),
            {"id": uuid4(), "repository_id": repository_id},
        )
        database_session.commit()
