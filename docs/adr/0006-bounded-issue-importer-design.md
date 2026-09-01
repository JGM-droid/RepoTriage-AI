# ADR 0006: Bounded Issue Importer Design

**Status:** Accepted
**Date:** 2026-08-31

## Context

Milestone 1.2 (R1-02) needs a repository-agnostic importer that turns public GitHub issues into `Repository`/`Issue` rows without exceeding the fixture-data policy set by ADR 0002, and without introducing GitHub write access, comments import, or workers.

## Decision

Add `backend/app/importer/` as the import boundary:

- `schemas.py` structurally validates every raw record (`RawIssueRecord`) before anything else runs; missing required fields or disallowed `state` values raise `ImportValidationError` and never invent a placeholder value.
- `sanitize.py` normalizes line endings, strips NUL and other unsafe control characters while preserving tabs and newlines, and truncates title (500 characters, matching the `issues.title` column) and body (20,000 characters) to a documented limit instead of rejecting oversized content.
- `service.py` bounds every import to `ImporterConfig` (`max_issues=100`, `max_pages=3`, `page_size=100`), excludes pull-request-shaped records, and writes through a single explicit transaction (commit only after every row succeeds, rollback on any failure) so a malformed record or a database failure never leaves a partial import. Idempotency reuses the existing unique constraints: repository upsert keys on `repositories.source_url`, issue upsert keys on `(issues.repository_id, issues.external_number)`, both via `INSERT ... ON CONFLICT DO NOTHING`.
- `github_client.py` performs bounded, read-only capture from the public GitHub REST issues endpoint for future re-capture only; it is never called by the default import path and never comments on, labels, or closes anything.
- The committed fixture at `backend/fixtures/pallets_flask/` (`issues.json`, `manifest.json`) is the default, offline import path (`python -m app.importer`). The manifest records source provider, source repository, capture timestamp, selection/filter rules, issue count, fixture format version, and a SHA-256 checksum of `issues.json`; `load_fixture` rejects a fixture whose checksum, issue count, or format version does not match the manifest.

No new persisted fields or migration were needed: the fixture reuses `external_number`, `title`, `body`, `state`, and `source_url`, all already present from Milestone 1.1.

## Alternatives considered

An ORM-level `INSERT OR IGNORE` emulation via a pre-query existence check was rejected in favor of `ON CONFLICT DO NOTHING`, which is atomic and avoids a race between the existence check and the insert. Rejecting oversized titles/bodies outright (instead of truncating) was considered but rejected because GitHub issue text length is not itself untrusted-structure information; truncation preserves more of the legitimate content while still bounding storage.

## Consequences

Fixture and live-capture code share the same validation, sanitization, and persistence path, so the demo behaves identically whether or not the (unused-by-default) live path is ever exercised. Adding a second approved source repository only requires a new fixture directory and manifest, not a schema change.

## Security implications

Imported title and body are treated as untrusted data end-to-end: they are never evaluated, rendered as HTML, or used to construct commands/URLs that are followed. `github_client.py` only performs read-only HTTP GET requests and cannot mutate the source repository. Errors raised by the importer (`ImporterError` and subclasses) carry only a fixed, non-sensitive message and never include database connection details.

## Testing/evidence

`backend/tests/test_importer_sanitize.py`, `test_importer_schemas.py`, `test_importer_github_client.py` (network-free), `test_importer_service.py`, and `test_importer_fixture.py` cover: exact fixture count, sorted/deduplicated fixture ordering, PR exclusion, page/issue limits, malformed-record rollback, sanitization, oversized-content truncation, manifest/checksum tampering, no-network-call for the offline path, and a PostgreSQL-unavailable failure propagating as an error.

## Revisit conditions

Revisit if a second source repository, a persisted provenance field, or comments/attachments import is approved in a later milestone.
