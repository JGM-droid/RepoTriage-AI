# ADR 0001: Initial Technology Stack

**Status:** Accepted
**Date:** 2026-08-31

## Context

The roadmap defines a production-oriented, containerized web application with typed contracts, a relational data model, future retrieval, and CI. Release 0.1 must establish direction without scaffolding application code.

## Decision

Use React and TypeScript for the frontend; FastAPI and Pydantic for the backend; PostgreSQL with SQLAlchemy and Alembic for persistence and migrations; pgvector when RAG is introduced; Docker Compose for the local stack; and GitHub Actions for CI. Application scaffolding has not started.

## Alternatives considered

Other frontend, backend, database, migration, vector, container, and CI tools were not selected because the approved roadmap already establishes this initial direction. Reconsider only through an ADR if implementation evidence reveals a material issue.

## Consequences

Future implementation must use typed API contracts, relational migrations, and documented container and CI workflows. Dependencies are intentionally deferred until Milestone 0.2.

## Security implications

Secrets must remain outside source control, dependencies must be scanned in CI, and later application boundaries must enforce authentication, authorization, input limits, and safe errors.

## Testing/evidence

Milestone 0.2 must demonstrate a fresh containerized start, health/readiness checks, formatting, linting, tests, and CI evidence.

## Revisit conditions

Revisit if a selected technology cannot meet a scored requirement or Critical Gate, or if an accepted ADR supersedes this decision.