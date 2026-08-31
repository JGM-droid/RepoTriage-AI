import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.v1 import health
from app.main import app

client = TestClient(app)


def test_readiness_reaches_configured_database() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("A configured PostgreSQL service is required for this integration check.")

    response = client.get("/api/v1/readiness")

    assert response.status_code == 200
    assert response.json() == {"service": "repotriage-api", "status": "ready"}


def test_readiness_succeeds_when_database_is_available(monkeypatch) -> None:
    monkeypatch.setattr(health, "verify_database", lambda: None)

    response = client.get("/api/v1/readiness")

    assert response.status_code == 200
    assert response.json() == {"service": "repotriage-api", "status": "ready"}


def test_readiness_fails_safely_when_database_is_unavailable(monkeypatch) -> None:
    connection_text = "postgresql://user:password@database:5432/repotriage"

    def unavailable() -> None:
        raise OperationalError("SELECT 1", {}, Exception(connection_text))

    monkeypatch.setattr(health, "verify_database", unavailable)

    response = client.get("/api/v1/readiness")

    assert response.status_code == 503
    assert response.json() == {"service": "repotriage-api", "status": "unavailable"}
    assert connection_text not in response.text
