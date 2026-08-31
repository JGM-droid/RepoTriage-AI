# ADR 0004: Defer Async Workflow Technology Selection

**Status:** Deferred
**Date:** 2026-08-31

## Context

Later releases require asynchronous execution with retry, timeout, idempotency, recoverability, and visible state. Selecting a workflow technology before its milestone would add unvalidated complexity and dependencies.

## Decision

Async execution is required later, but no technology is selected now. Temporal, a task queue, and other implementations will be evaluated at the relevant milestone. Do not add an async dependency during Milestone 0.1.

## Alternatives considered

Temporal and task-queue approaches remain candidates. Synchronous-only implementation is insufficient for the required durable workflow behavior.

## Consequences

Release 0.1 has no worker, queue, or async dependency. The future decision must be captured in a new Accepted ADR before implementation.

## Security implications

The selected mechanism must preserve tenant boundaries, authorization context, safe retry behavior, secrets handling, traceability, and failure containment.

## Testing/evidence

The later ADR must define tests for retry, timeout, idempotency, recovery, forced failure, and observable workflow state.

## Revisit conditions

Revisit at Release 2, Milestone 2.1. Evaluate retry, timeout, idempotency, recoverability, implementation complexity, cost, and portfolio evidence before acceptance.