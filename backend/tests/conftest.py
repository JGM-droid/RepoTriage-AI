"""Test-session configuration.

Celery runs in eager (synchronous, in-process) mode for all tests so the
durable workflow executes deterministically without a real broker/worker,
against whatever isolated PostgreSQL database DATABASE_URL points at.
Embeddings use the deterministic, dependency-free fake adapter — not the
real local model — so the default test suite stays fast and makes no
model/network calls (a few retrieval tests override this explicitly to
exercise `LocalEmbeddingAdapter`'s own code directly, without loading a
real model). Both must be set before any test module imports
`app.config`/`app.main`.
"""

import os

import pytest
import redis as _redis_module

os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("TRIAGE_RETRY_COUNTDOWN_SECONDS", "0")
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")


@pytest.fixture(scope="session")
def _rate_limit_redis_client():
    """One connection attempt per test *session*, not per test -- probing
    Redis freshly in every test's autouse fixture would pay a full
    connect-timeout penalty on every single test (not just rate-limit
    tests) whenever Redis is not running locally, turning a ~1s suite into
    a multi-minute one. `None` here means "Redis unreachable" and is
    checked cheaply by every test via `_reset_rate_limit_counters` below."""
    try:
        client = _redis_module.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        client.ping()
    except Exception:
        yield None
        return
    yield client
    client.close()


@pytest.fixture(autouse=True)
def _reset_rate_limit_counters(_rate_limit_redis_client) -> None:
    """The rate limiter (Milestone 2.6; see app.api.rate_limit) shares one
    Redis-backed counter per (route, client IP) across the whole test
    process, since Redis state is not reset by a per-test database
    TRUNCATE. Without this, unrelated tests calling the same
    rate-limited endpoint many times across a full suite run could trip
    each other's counters. Does nothing if Redis is unreachable -- checked
    once per session (see `_rate_limit_redis_client`), never per test --
    consistent with the limiter's own fail-open behavior, and this fixture
    must never make an unrelated test fail merely because Redis is not
    running locally."""
    if _rate_limit_redis_client is None:
        return
    try:
        for key in _rate_limit_redis_client.scan_iter("ratelimit:*"):
            _rate_limit_redis_client.delete(key)
    except Exception:
        pass
