# ADR 0002: Public Fixture-Data Policy

**Status:** Accepted
**Date:** 2026-08-31

## Context

The product needs repeatable issue-triage demonstrations without special access or private data.

## Decision

Use public GitHub data only. Select a bounded fixture of approximately 100–300 issues, cache it for offline demonstration, and preserve provenance. Imports must be sanitized, reproducible, and idempotent. Do not ingest sensitive or private repository data.

## Alternatives considered

Live-only imports and private repository data are rejected because they undermine repeatability, safe sharing, and the project boundary.

## Consequences

The project can demonstrate import and triage offline but must document fixture origin and limitations. Public retrospective evaluation cannot be presented as customer outcomes.

## Security implications

Imported issue text is untrusted data. Sanitize stored content, limit ingestion, avoid secrets and private data, and test hostile or malformed inputs.

## Testing/evidence

Provide bounded-import, malformed-content, duplicate-import, sanitization, provenance, and offline demonstration tests or repeatable checks.

## Revisit conditions

Revisit only if an approved milestone requires a different public-data policy or a new ADR supersedes this one.