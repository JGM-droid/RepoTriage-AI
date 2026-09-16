"""Fixed-window, Redis-backed rate limiting for state-changing endpoints
(Milestone 2.6; see ADR 0012).

Scope: only the two endpoints that create durable state -- starting a
triage workflow and recording a human decision (see `app.api.v1.triage`).
Every read-only GET endpoint (health, issue list/detail, triage-status
polling) is deliberately never rate-limited here, so normal UI polling is
never throttled.

Mechanism: a fixed window keyed by `(route_name, client_ip)` -- independent
routes and independent clients never share a counter. There is no
authentication in this milestone, so client IP is the only available
identity; a shared-IP client (e.g. behind one NAT) shares one counter,
which is an accepted limitation for a demo-scale application with no auth,
not a claim of per-user fairness.

Atomicity (Milestone 2.6 correction): the increment and its expiry are set
by one Lua script (`_INCR_AND_ENSURE_TTL_SCRIPT`), executed atomically by
Redis -- never as separate `INCR`/`EXPIRE` calls. Redis runs a script to
completion, uninterrupted by any other command, before any client
(including this one) can observe a partial result; if the process or
connection dies mid-request, either the whole script already committed on
the Redis server (count *and* TTL both set) or nothing did (the key is
untouched) -- there is no window in which a counter key can exist without
an expiry. The script additionally self-heals: on every call, if the key
somehow already exists with no TTL (`-1`), it assigns one immediately,
rather than only ever setting expiry on the first increment.

Redis-unavailable behavior is a deliberate, documented choice: FAIL OPEN.
The request is allowed through if Redis cannot be reached within a short
timeout. Rationale (see ADR 0012 for the full writeup): both rate-limited
endpoints already depend on Redis to do anything useful downstream
(Celery's broker for starting a workflow), so a Redis outage already
degrades the system; rejecting requests here in addition would not protect
anything Redis's own unavailability doesn't already block, and would take
an otherwise-partially-working API fully offline for a rate limiter whose
purpose is catching accidental rapid duplicate submissions, not defending
a high-value target against a determined attacker.
"""

from __future__ import annotations

from collections.abc import Callable

import redis
from fastapi import Depends, Request

from app.config import Settings, get_settings

_RATE_LIMIT_KEY_PREFIX = "ratelimit"


class RateLimitExceeded(Exception):
    """Raised by a rate-limiter dependency when a client exceeds its
    window. Handled by a dedicated FastAPI exception handler (see
    `app.main`) that returns the same flat `ErrorResponse` shape as every
    other error in this API, plus a `Retry-After` header -- never FastAPI's
    default `{"detail": ...}` envelope, which would be the only
    inconsistent error shape in the API."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


# Atomically: increment the counter; if it has no TTL for any reason (a
# fresh key on its first increment, or -- defensively -- an orphaned key
# that somehow lost its TTL), assign one now. Redis executes the whole
# script as a single, uninterruptible operation, so no other client (or a
# crashed-and-retried version of this one) can ever observe the key
# between the INCR and the EXPIRE.
_INCR_AND_ENSURE_TTL_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl == -1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""

_redis_client: redis.Redis | None = None
_redis_client_url: str | None = None


def _get_redis_client(settings: Settings) -> redis.Redis:
    """Reuses one connection (pool) per `redis_url`, matching how a real
    deployment would share a client across requests -- never a new
    connection per call."""
    global _redis_client, _redis_client_url
    if _redis_client is None or _redis_client_url != settings.redis_url:
        _redis_client = redis.from_url(
            settings.redis_url, socket_connect_timeout=1.0, socket_timeout=1.0
        )
        _redis_client_url = settings.redis_url
    return _redis_client


def _incr_and_ensure_ttl(client: redis.Redis, key: str, window_seconds: int) -> tuple[int, int]:
    """Atomically increments `key` and guarantees it carries a TTL,
    returning `(count, ttl)`. Uses `EVAL` directly (not `register_script`'s
    `EVALSHA`-with-fallback) -- this script is tiny and called on every
    rate-limited request, so the extra round-trip `EVALSHA` would save is
    not worth the added script-cache-management complexity."""
    count, ttl = client.eval(_INCR_AND_ENSURE_TTL_SCRIPT, 1, key, window_seconds)
    return int(count), int(ttl)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limiter(
    route_name: str, *, max_requests_attr: str, window_seconds_attr: str
) -> Callable[..., None]:
    """Returns a FastAPI dependency enforcing a fixed-window limit read
    fresh from `Settings` on every call (via `max_requests_attr`/
    `window_seconds_attr`, attribute names on `Settings` -- not literal
    values captured at import time), so tests can override limits through
    the ordinary `app.dependency_overrides[get_settings]` mechanism without
    needing a second code path."""

    def _dependency(request: Request, settings: Settings = Depends(get_settings)) -> None:
        max_requests = getattr(settings, max_requests_attr)
        window_seconds = getattr(settings, window_seconds_attr)
        redis_key = f"{_RATE_LIMIT_KEY_PREFIX}:{route_name}:{_client_key(request)}"

        try:
            client = _get_redis_client(settings)
            count, ttl = _incr_and_ensure_ttl(client, redis_key, window_seconds)
        except redis.RedisError:
            # Fail open -- see module docstring.
            return

        if count > max_requests:
            retry_after = ttl if ttl and ttl > 0 else window_seconds
            raise RateLimitExceeded(retry_after)

    return _dependency
