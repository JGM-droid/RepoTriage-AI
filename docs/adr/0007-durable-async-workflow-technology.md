# ADR 0007: Durable Asynchronous Workflow Technology

**Status:** Accepted
**Date:** 2026-09-14

## Context

ADR 0004 deferred selecting an asynchronous workflow technology until Release 2, Milestone 2.1. Milestone 2.1 now requires durable stage boundaries, bounded retry and timeout, idempotent request handling, and recoverable/resumable execution for the deterministic triage workflow (`classify -> retrieve_fixture_evidence -> assess -> propose -> human_review`), executed in a background worker rather than inside the HTTP request.

## Decision

Use Celery as the background task worker and Redis as its broker/result backend. PostgreSQL remains the sole canonical, durable store of workflow state: the existing `analyses` table (extended with `idempotency_key`, `current_stage`, and `attempt_count`) represents one workflow run, and a new `stage_attempts` table records one row per stage execution attempt. Redis holds only transient task coordination data; a lost or flushed Redis instance never loses authoritative workflow state, only in-flight task delivery, and re-enqueuing a task safely resumes from the persisted PostgreSQL state.

## Alternatives considered

Continuing synchronous, in-request execution is rejected because it cannot satisfy required retry, timeout, or resumability behavior, and blocks the HTTP request for the duration of analysis. Temporal is rejected for this milestone because its operational footprint (a dedicated server, SDK-specific workflow-as-code model, and additional infrastructure to run and operate) is disproportionate to the smallest recognizable durable-workflow slice this milestone requires; it remains a candidate for a future release if durability requirements grow substantially. A custom PostgreSQL-only worker (e.g., `SKIP LOCKED` polling) is rejected because it would require building bespoke leasing, backoff, and delivery-acknowledgement logic that Celery already provides as a mature, widely used mechanism, without a corresponding reduction in dependencies (a queue-capable datastore is still required).

## Consequences

The stack gains a Redis dependency and a Celery worker process/container. Workflow status is genuinely asynchronous: the triage-start endpoint returns `202 Accepted` immediately, and clients poll a status endpoint for progress and the final result. All authoritative decisions (retry/failed/timed_out/completed, stage attempts, recommendations, human decisions) are read from and written to PostgreSQL; Celery/Redis are purely execution plumbing.

## Security implications

Redis is not published outside the Compose network and carries no secrets or durable business data. The worker runs the same trusted application code and database credentials as the API; it introduces no new external attack surface. The mandatory human-review boundary (ADR 0003) is unaffected: the worker never creates a `HumanDecision`.

## Testing/evidence

Automated tests run Celery in eager (synchronous, in-process) mode against an isolated PostgreSQL test database so retry, timeout, idempotency, and recovery paths are deterministic and fast. Required tests include: bounded retry and eventual failure, timeout producing `timed_out`, idempotency-key deduplication and conflict rejection, safe resumption of an interrupted run, and confirmation that the worker never writes a `HumanDecision`.

## Revisit conditions

Revisit if Release 3 multi-tenant scaling, stricter delivery guarantees, or cross-service workflow orchestration require capabilities Celery/Redis cannot reasonably provide.
