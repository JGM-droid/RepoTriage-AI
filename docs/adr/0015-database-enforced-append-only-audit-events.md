# ADR 0015: Database-Enforced Append-Only Audit Events

**Status:** Accepted
**Date:** 2026-09-19

## Context

Milestone 3.2 and rubric §H require append-only audit evidence. `AuditEvent` already records
analysis/workflow transitions, retrieval evidence, AI routing provenance, and human decisions,
but “append-only” was only an application convention from ADR 0005. The PostgreSQL owner/runtime
role could update, delete, bulk-delete, or truncate rows through either SQLAlchemy or raw SQL.
Imports also had no audit event.

## Decision

Migration `20260919_0007` creates one stable trigger function,
`repotriage_reject_audit_event_mutation`, a row trigger for `UPDATE OR DELETE`, and a statement
trigger for `TRUNCATE`. Each raises SQLSTATE `55000` with the deterministic message
`audit_events is append-only: UPDATE, DELETE, and TRUNCATE are prohibited`. Inserts and reads are
unchanged. The anonymous issue/repository foreign keys from migration 0001 are replaced with
stable named `ON DELETE RESTRICT` constraints, preventing parent deletion from silently removing
history. Existing rows and identifiers are untouched.

Downgrade first counts audit rows and refuses before any schema change if even one exists. An
empty table may downgrade: triggers/function are removed and the pre-0007 `NO ACTION` foreign-key
shape is restored. This makes removal of protection explicit rather than silently exposing real
history to mutation.

Disposable tests clean tables through `tests.db_maintenance.truncate_for_test`. That helper uses
table-owner DDL to disable the two named triggers, truncates isolated test state, and restores the
triggers in the same transaction. It is test-only code and is not reachable from a production
route, header, setting, or application service.

`import_records` writes one `issues_imported` event in the same transaction whenever at least one
issue is newly inserted. Its metadata records considered, inserted, and skipped-existing counts.
An idempotent repeat that inserts zero issues writes no duplicate event; a later import that adds
new issues writes one new event.

Tenant reads remain centralized through `audit_events_query_for_tenant`, which joins each event's
repository to `Repository.tenant_id`. There is intentionally no new audit API. Viewer, reviewer,
and administrator retain the existing same-tenant read contract; a different tenant sees no row.
Status history retains deterministic `(created_at, id)` ordering.

## Threat boundary

This is database-enforced append-only behavior for ordinary application/runtime DML, including
ORM operations, direct SQL, bulk DML, cascaded/parent deletion, and truncate. It is not
cryptographic or universally permanent immutability. PostgreSQL superusers and table owners with
DDL rights can disable/drop triggers, alter constraints, or drop the table. In the current local
Compose demo, the `repotriage` API/worker credential owns `audit_events` and is a PostgreSQL
superuser, so compromise of that runtime credential can remove the protection with DDL. The
triggers protect ordinary application DML, not a database owner or superuser. A deployment-ready
architecture must separate a migration-owner role from a restricted application-runtime role
before claiming protection against a compromised runtime credential. That privilege/deployment
redesign remains outside this slice.

## Coverage

Current production creation paths are:

- importer: `issues_imported` for each transaction that inserts one or more issues;
- synchronous and durable workflow: queued/running/retrying/timed-out/failed/completed transition
  events, including analysis identity/status provenance;
- retrieval: selected evidence identifiers and retrieval outcome;
- AI routing: provider/model/status/fallback, prompt/redaction, usage/cost, and routing provenance;
- human decision: approve, reject, or request-revision with actor and analysis/recommendation
  provenance.

The application currently has no issue-edit endpoint, actor/role administration endpoint, or
other administrative mutation feature. Therefore no edit/administrative event exists yet, and
this ADR does not claim complete future event coverage. Those actions must add an audit insert in
their own state-changing transaction when such production capabilities are introduced.

## Alternatives considered

Application hooks or route-only guards were rejected because raw SQL and other writers could
bypass them. Revoking DML privileges from the current role was insufficient because PostgreSQL
does not provide a simple privilege split that allows `INSERT` but rejects all owner DDL, and the
same role owns the table. Hash chaining was rejected because the roadmap does not require
cryptographic tamper evidence and a correct concurrent append/replay design would materially
expand scope. Separate immutable storage and a distinct runtime role remain valid future designs.

## Consequences and verification

Existing insert/read and retry/idempotency behavior remains unchanged. Trigger enforcement is
covered across ORM and SQL update/delete, bulk DML, truncate, rollback recovery, parent deletion,
tenant reads, workflow history, concurrent insertion, and migration preservation/guard behavior.
A failure-proof check disables the triggers only in a disposable test database, proves a named
mutation test fails because mutation becomes possible, restores protection, and proves it passes.

## Revisit conditions

Revisit when production authentication/role separation creates a dedicated runtime database role;
when an edit or administrative capability is added; or when requirements demand independently
verifiable tamper evidence, cross-system retention, write-once storage, cryptographic chaining, or
a separately administered audit service.
