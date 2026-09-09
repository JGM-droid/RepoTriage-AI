import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.v1.issues import get_issue_session
from app.main import app
from app.models.core import Issue, Repository

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for issue API integration tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
            session.rollback()
    finally:
        engine.dispose()


@pytest.fixture()
def client(database_session: Session) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        yield database_session

    app.dependency_overrides[get_issue_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def add_issue(
    session: Session,
    *,
    external_number: int,
    title: str,
    repository: Repository | None = None,
) -> Issue:
    if repository is None:
        repository = Repository(
            name="pallets/flask",
            source_url="https://github.com/pallets/flask",
        )
        session.add(repository)
        session.flush()

    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title=title,
        body=f"Body for {title}",
        state="open",
        source_url=f"https://github.com/pallets/flask/issues/{external_number}",
    )
    session.add(issue)
    session.commit()
    return issue


def test_list_issues_returns_imported_issues_in_deterministic_order(
    client: TestClient,
    database_session: Session,
) -> None:
    repository = Repository(
        name="pallets/flask",
        source_url="https://github.com/pallets/flask",
    )
    database_session.add(repository)
    database_session.flush()
    add_issue(database_session, external_number=2, title="Second", repository=repository)
    add_issue(database_session, external_number=1, title="First", repository=repository)

    response = client.get("/api/v1/issues")

    assert response.status_code == 200
    payload = response.json()
    assert [issue["external_number"] for issue in payload["issues"]] == [1, 2]
    assert payload["issues"][0]["repository"]["name"] == "pallets/flask"
    assert payload["issues"][0]["repository"]["source_url"] == "https://github.com/pallets/flask"
    assert "body" not in payload["issues"][0]


def test_list_issues_returns_empty_database(client: TestClient) -> None:
    response = client.get("/api/v1/issues")

    assert response.status_code == 200
    assert response.json() == {"issues": []}


def test_get_issue_returns_detail(client: TestClient, database_session: Session) -> None:
    issue = add_issue(database_session, external_number=7, title="Inspect me")

    response = client.get(f"/api/v1/issues/{issue.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(issue.id)
    assert payload["external_number"] == 7
    assert payload["title"] == "Inspect me"
    assert payload["body"] == "Body for Inspect me"
    assert payload["repository"]["name"] == "pallets/flask"


def test_get_issue_returns_stable_not_found_response(client: TestClient) -> None:
    response = client.get(f"/api/v1/issues/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"error": "issue_not_found", "message": "Issue not found."}


def test_list_issues_returns_stable_database_error() -> None:
    class BrokenSession:
        def execute(self, _statement: object) -> object:
            raise SQLAlchemyError("database unavailable")

    def override_session() -> Iterator[BrokenSession]:
        yield BrokenSession()

    app.dependency_overrides[get_issue_session] = override_session
    try:
        response = TestClient(app).get("/api/v1/issues")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json() == {
        "error": "issue_list_unavailable",
        "message": "Imported issues are unavailable.",
    }
