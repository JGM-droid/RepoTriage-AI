from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

APPROVED_ORIGIN = "http://localhost:5173"
UNAPPROVED_ORIGIN = "http://evil.example"


def test_preflight_allows_post_from_the_approved_frontend_origin() -> None:
    client = TestClient(app)

    response = client.options(
        f"/api/v1/issues/{uuid4()}/triage",
        headers={
            "Origin": APPROVED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == APPROVED_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]


def test_preflight_rejects_post_from_an_unapproved_origin() -> None:
    client = TestClient(app)

    response = client.options(
        f"/api/v1/issues/{uuid4()}/triage",
        headers={
            "Origin": UNAPPROVED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_preflight_still_allows_get_from_the_approved_frontend_origin() -> None:
    client = TestClient(app)

    response = client.options(
        "/api/v1/issues",
        headers={
            "Origin": APPROVED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == APPROVED_ORIGIN
