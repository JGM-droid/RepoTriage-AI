"""API rate-limiting tests (Milestone 2.6; see ADR 0012).

Requires a real PostgreSQL (for the underlying triage/decision endpoints)
and a real Redis (for the rate limiter itself) -- skips cleanly if either
is unavailable, matching the existing integration-test convention in
tests/test_triage_api.py and tests/test_decisions_api.py.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.rate_limit import _RATE_LIMIT_KEY_PREFIX
from app.api.v1.triage import get_triage_session
from app.config import Settings, get_settings
from app.main import app
from app.models.core import Analysis, Issue, Repository

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for rate-limit integration tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.fixture()
def redis_client() -> Iterator[redis.Redis]:
    settings = Settings()
    try:
        client = redis.from_url(settings.redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)
        client.ping()
    except redis.RedisError:
        pytest.skip("Redis is required for rate-limit integration tests.")
    yield client
    for key in client.scan_iter(f"{_RATE_LIMIT_KEY_PREFIX}:*"):
        client.delete(key)
    client.close()


def _override_settings(**rate_limit_overrides) -> Settings:
    return Settings(**rate_limit_overrides)


@pytest.fixture()
def client(database_session: Session, redis_client: redis.Redis) -> Iterator[TestClient]:
    del redis_client  # ensures Redis is up and its keys are cleaned; not used directly here

    def override_session() -> Iterator[Session]:
        yield database_session

    app.dependency_overrides[get_triage_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def add_issue(
    session: Session,
    *,
    title: str = "App crash on save",
    body: str = "Traceback included",
    state: str = "open",
    external_number: int = 1,
) -> Issue:
    repository = Repository(
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


def _with_tight_start_triage_limit(max_requests: int = 2, window_seconds: int = 60) -> None:
    app.dependency_overrides[get_settings] = lambda: _override_settings(
        rate_limit_triage_start_max_requests=max_requests,
        rate_limit_triage_start_window_seconds=window_seconds,
        rate_limit_decision_max_requests=max_requests,
        rate_limit_decision_window_seconds=window_seconds,
    )


# --- start-triage endpoint ---------------------------------------------------


def test_requests_under_the_limit_succeed(client: TestClient, database_session: Session) -> None:
    _with_tight_start_triage_limit(max_requests=3)
    issues = [add_issue(database_session, external_number=n) for n in range(1, 4)]

    for issue in issues:
        response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})
        assert response.status_code == 202


def test_a_request_over_the_limit_returns_429_with_a_safe_error_body(
    client: TestClient, database_session: Session
) -> None:
    _with_tight_start_triage_limit(max_requests=2)
    issues = [add_issue(database_session, external_number=n) for n in range(1, 4)]

    for issue in issues[:2]:
        response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})
        assert response.status_code == 202

    over_limit_response = client.post(f"/api/v1/issues/{issues[2].id}/triage", json={})

    assert over_limit_response.status_code == 429
    payload = over_limit_response.json()
    assert set(payload.keys()) == {"error", "message"}
    assert payload["error"] == "rate_limited"
    assert "Retry-After" in over_limit_response.headers
    # Safe error: never leaks internals.
    assert "traceback" not in over_limit_response.text.lower()
    assert "redis" not in over_limit_response.text.lower()


def test_a_rejected_request_creates_no_analysis_row(
    client: TestClient, database_session: Session
) -> None:
    _with_tight_start_triage_limit(max_requests=1)
    issue_a = add_issue(database_session, external_number=1)
    issue_b = add_issue(database_session, external_number=2)

    first = client.post(f"/api/v1/issues/{issue_a.id}/triage", json={})
    assert first.status_code == 202

    rejected = client.post(f"/api/v1/issues/{issue_b.id}/triage", json={})
    assert rejected.status_code == 429

    analyses_for_issue_b = (
        database_session.query(Analysis).filter(Analysis.issue_id == issue_b.id).all()
    )
    assert analyses_for_issue_b == []


def test_independent_routes_have_independent_counters(
    client: TestClient, database_session: Session
) -> None:
    """Exhausting the start-triage counter must never affect the
    record-decision counter (or vice versa) for the same client."""
    app.dependency_overrides[get_settings] = lambda: _override_settings(
        rate_limit_triage_start_max_requests=1,
        rate_limit_triage_start_window_seconds=60,
        rate_limit_decision_max_requests=5,
        rate_limit_decision_window_seconds=60,
    )
    issue_a = add_issue(database_session, external_number=1)
    issue_b = add_issue(database_session, external_number=2)

    first = client.post(f"/api/v1/issues/{issue_a.id}/triage", json={})
    assert first.status_code == 202
    exhausted = client.post(f"/api/v1/issues/{issue_b.id}/triage", json={})
    assert exhausted.status_code == 429

    # The separately-counted decision endpoint must still work normally.
    decision_response = client.post(
        f"/api/v1/issues/{issue_a.id}/triage/decision", json={"decision": "approve"}
    )
    assert decision_response.status_code == 200


def test_get_polling_endpoints_are_never_rate_limited(
    client: TestClient, database_session: Session
) -> None:
    _with_tight_start_triage_limit(max_requests=1)
    issue = add_issue(database_session)
    client.post(f"/api/v1/issues/{issue.id}/triage", json={})  # consumes the one allowed slot

    # GET polling must be completely unaffected, however many times it runs.
    for _ in range(10):
        response = client.get(f"/api/v1/issues/{issue.id}/triage")
        assert response.status_code == 200

    for _ in range(10):
        response = client.get("/api/v1/issues")
        assert response.status_code == 200

    for _ in range(10):
        response = client.get("/api/v1/health")
        assert response.status_code == 200


def test_the_window_resets_after_it_expires(
    client: TestClient, database_session: Session, redis_client: redis.Redis
) -> None:
    _with_tight_start_triage_limit(max_requests=1, window_seconds=1)
    issue_a = add_issue(database_session, external_number=1)
    issue_b = add_issue(database_session, external_number=2)

    first = client.post(f"/api/v1/issues/{issue_a.id}/triage", json={})
    assert first.status_code == 202
    exhausted = client.post(f"/api/v1/issues/{issue_b.id}/triage", json={})
    assert exhausted.status_code == 429

    import time

    time.sleep(1.2)

    reset_response = client.post(f"/api/v1/issues/{issue_b.id}/triage", json={})
    assert reset_response.status_code == 202


# --- atomicity (Milestone 2.6 correction) ------------------------------------


def test_first_request_creates_the_counter_with_a_positive_ttl(
    client: TestClient, database_session: Session, redis_client: redis.Redis
) -> None:
    _with_tight_start_triage_limit(max_requests=5, window_seconds=60)
    issue = add_issue(database_session)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})
    assert response.status_code == 202

    keys = list(redis_client.scan_iter(f"{_RATE_LIMIT_KEY_PREFIX}:start_triage:*"))
    assert len(keys) == 1
    assert redis_client.get(keys[0]) == b"1"
    ttl = redis_client.ttl(keys[0])
    assert ttl > 0
    assert ttl <= 60


def test_subsequent_increments_preserve_the_fixed_window_without_resetting_it(
    client: TestClient, database_session: Session, redis_client: redis.Redis
) -> None:
    """A *fixed* window must not silently become a sliding one: the TTL set
    on the first increment must not be pushed back out to the full window
    on every later increment within that same window."""
    _with_tight_start_triage_limit(max_requests=5, window_seconds=60)
    issues = [add_issue(database_session, external_number=n) for n in range(1, 4)]

    client.post(f"/api/v1/issues/{issues[0].id}/triage", json={})
    keys = list(redis_client.scan_iter(f"{_RATE_LIMIT_KEY_PREFIX}:start_triage:*"))
    assert len(keys) == 1
    first_ttl = redis_client.ttl(keys[0])

    import time

    time.sleep(1.1)

    client.post(f"/api/v1/issues/{issues[1].id}/triage", json={})
    client.post(f"/api/v1/issues/{issues[2].id}/triage", json={})
    second_ttl = redis_client.ttl(keys[0])

    assert redis_client.get(keys[0]) == b"3"
    # The TTL only ever counts down toward the original window's end; two
    # more increments after the sleep must not have reset it back to ~60.
    assert second_ttl <= first_ttl


def test_the_key_cannot_remain_with_ttl_negative_one(
    client: TestClient, database_session: Session, redis_client: redis.Redis
) -> None:
    """Simulates the exact failure this correction closes: a counter key
    that somehow exists with no TTL (e.g. a crash between a hypothetical
    unprotected INCR and EXPIRE in the old implementation). The atomic
    script must self-heal it on the very next call, not leave it stuck
    without an expiry forever."""
    _with_tight_start_triage_limit(max_requests=5, window_seconds=30)
    issue = add_issue(database_session)
    redis_key = f"{_RATE_LIMIT_KEY_PREFIX}:start_triage:testclient"
    redis_client.set(redis_key, 1)  # deliberately no expiry
    assert redis_client.ttl(redis_key) == -1

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    assert response.status_code == 202
    assert redis_client.ttl(redis_key) > 0


def test_the_atomic_script_helper_sets_count_and_ttl_in_one_call(
    redis_client: redis.Redis,
) -> None:
    """Unit-level proof that `_incr_and_ensure_ttl` is the single Redis
    operation the limiter relies on -- not two separate, interruptible
    `INCR`/`EXPIRE` calls."""
    from app.api.rate_limit import _incr_and_ensure_ttl

    key = f"{_RATE_LIMIT_KEY_PREFIX}:unit_test:atomicity"
    redis_client.delete(key)

    count, ttl = _incr_and_ensure_ttl(redis_client, key, 45)

    assert count == 1
    assert 0 < ttl <= 45
    assert redis_client.ttl(key) == ttl

    count2, ttl2 = _incr_and_ensure_ttl(redis_client, key, 45)
    assert count2 == 2
    assert ttl2 > 0


def test_redis_being_unavailable_fails_open(
    client: TestClient, database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberate, documented fail-open behavior (see app.api.rate_limit's
    module docstring and ADR 0012): the request must still succeed if
    Redis cannot be reached, rather than taking the whole endpoint down
    because an auxiliary counter service is unavailable."""
    import app.api.rate_limit as rate_limit_module

    _with_tight_start_triage_limit(max_requests=1)
    issue = add_issue(database_session)

    def _raise_connection_error(*args, **kwargs):
        raise redis.ConnectionError("simulated Redis outage")

    monkeypatch.setattr(rate_limit_module, "_get_redis_client", _raise_connection_error)

    response = client.post(f"/api/v1/issues/{issue.id}/triage", json={})

    assert response.status_code == 202
