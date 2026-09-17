"""Demo-actor-listing endpoint tests (Milestone 3.1 Slice 2 correction;
see ADR 0014).

`GET /api/v1/demo/actors` is registered on the FastAPI app only when
`Settings.demo_mode_enabled` is `True` at the moment `app.main.create_app`
is called (see that function's docstring) -- genuinely absent from the
route table otherwise, not merely erroring. These tests call
`create_app` directly with an explicit `Settings` instance and make real
requests through `TestClient`, exercising real application-startup
behavior -- the same function the module-level `app = create_app()`
singleton uses -- rather than relying on `importlib.reload` timing,
import order, or introspecting FastAPI's internal route representation
(which is not a stable public contract across versions).

Requires isolated PostgreSQL -- skips cleanly if unavailable, since even
proving the route is *absent* is more convincingly done by exercising
the same live app/database path the "present" case uses. Never runs
against the live demo database. Never touches the process's real
environment variables or the module-level `app.main.app` singleton other
test files depend on.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _require_database() -> None:
    if os.getenv("DATABASE_URL") is None:
        pytest.skip("PostgreSQL is required for demo-actor-endpoint tests.")


def test_demo_actors_endpoint_is_absent_when_demo_mode_is_disabled_before_creation() -> None:
    """Demo mode disabled before application creation -- the default,
    production-equivalent configuration -- must not expose the route."""
    app = create_app(Settings())

    response = TestClient(app).get("/api/v1/demo/actors")

    # Genuinely absent, not an application-level 403/disabled response --
    # FastAPI's own "no matching route" 404, distinguishable from this
    # app's own issue/triage 404 bodies (which always carry {"error":
    # ..., "message": ...}, never FastAPI's default {"detail": ...}).
    assert response.status_code == 404
    assert "error" not in response.json()


def test_demo_actors_endpoint_is_exposed_when_demo_mode_is_enabled_before_creation() -> None:
    """Demo mode enabled before application creation must expose the
    route and serve real data through it."""
    app = create_app(Settings(demo_mode_enabled=True))

    response = TestClient(app).get("/api/v1/demo/actors")

    assert response.status_code == 200
    assert len(response.json()["actors"]) == 5


def test_default_settings_leave_the_route_unavailable() -> None:
    """Production/default configuration (no `Settings(...)` override at
    all, matching what a real deployment gets unless it explicitly opts
    in) leaves the route unavailable."""
    assert Settings().demo_mode_enabled is False

    app = create_app()  # falls back to a fresh Settings() read

    response = TestClient(app).get("/api/v1/demo/actors")

    assert response.status_code == 404


def test_ordinary_call_order_cannot_accidentally_expose_the_route() -> None:
    """Constructing several apps back to back, mixing enabled and
    disabled settings in different orders, proves each call is an
    independent function of the settings it was given -- never a shared
    mutable snapshot that an earlier call's configuration could leak
    into a later one because of which module happened to import, or
    which app got built, first."""
    disabled_first = TestClient(create_app(Settings())).get("/api/v1/demo/actors")
    enabled_second = TestClient(create_app(Settings(demo_mode_enabled=True))).get(
        "/api/v1/demo/actors"
    )
    disabled_third = TestClient(create_app(Settings())).get("/api/v1/demo/actors")

    assert disabled_first.status_code == 404
    assert enabled_second.status_code == 200
    assert disabled_third.status_code == 404


def test_demo_actors_endpoint_lists_actors_when_enabled_and_database_reachable() -> None:
    app = create_app(Settings(demo_mode_enabled=True))

    response = TestClient(app).get("/api/v1/demo/actors")

    assert response.status_code == 200
    payload = response.json()
    assert "not production authentication" in payload["notice"]
    assert len(payload["actors"]) == 5
    for actor in payload["actors"]:
        assert set(actor.keys()) == {
            "id",
            "organization_id",
            "organization_name",
            "display_name",
            "role",
        }
        assert "slug" not in actor  # only the fields the selector needs
