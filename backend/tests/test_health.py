from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_succeeds_without_database() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"service": "repotriage-api", "status": "healthy"}


def test_health_contract_is_stable() -> None:
    response = client.get("/api/v1/health")

    assert set(response.json()) == {"service", "status"}
