# ADR 0013: Organization Tenant Data Model

**Status:** Accepted
**Date:** 2026-09-17

## Context

Milestone 3.1 (Multi-tenancy and roles) requires: at least two organizations supported by the
data model; viewer/reviewer/administrator roles with distinct backend-enforced permissions;
backend-enforced permissions; and cross-tenant negative tests covering records, vectors,
analyses, and traces (rubric §H; Critical Gate G4 -- "Automated negative tests prove cross-tenant
records and vector results are inaccessible").

ADR 0005 (Milestone 1.1) reserved exactly this moment: `repositories` carries a
"future-compatible `tenant_id` UUID without a tenant-table foreign key," explicitly so that
"future tenant and actor integration does not require destructive primary-key changes," with its
own revisit condition -- "when organization/user management, authentication/RBAC, or
cross-tenant enforcement is implemented" -- now triggered.

This ADR covers only the first slice: the durable organization/tenant-ownership **data model**.
It deliberately does not cover request identity, authentication, role enforcement, or API-level
tenant scoping -- those are separate, later slices of the same milestone, and Critical Gate G4 is
not satisfied by this slice alone.

## Decision

**1. A minimal `Organization` model.** New table `organizations`: `id` (UUID PK, matching every
other table's `UUIDTimestampMixin` convention), `slug` (unique, stable, machine-readable
identifier), `name` (human-readable), `created_at`/`updated_at`. No billing, plans, subscriptions,
domains, SSO, or settings fields -- nothing this milestone's actual slices need.

**2. `Repository.tenant_id` becomes a real, enforced foreign key.** Was a bare, nullable,
unenforced UUID since migration 0001; migration `20260917_0005` adds
`ForeignKey("organizations.id", ondelete="RESTRICT")`, sets it `NOT NULL`, and reuses the
existing `ix_repositories_tenant_id` index (from 0001) rather than creating a redundant one. The
column name is kept as `tenant_id` (not renamed to `organization_id`) to minimize the diff against
the one existing reference in `app/schemas/core.py` and to match ADR 0005's own vocabulary.

**3. Restrictive (`RESTRICT`), not cascading, deletion.** An organization can never be deleted
while it still owns any repository -- and therefore any issue, analysis, recommendation, human
decision, retrieval chunk, or audit event, since every one of those tables ultimately chains back
to `Repository` as the aggregate root (ADR 0005). A cascading delete would silently destroy
customer data as a side effect of an administrative action on an unrelated row; that is
unacceptable for a data-loss-sensitive boundary like this one. Deleting an organization, if ever
needed, must be an explicit, separate, audited operation on its owned repositories first --
out of scope for this slice.

**4. Two deterministic, well-known organization ids -- never randomly generated.**
`DEFAULT_ORGANIZATION_ID` (`...0101`) and `ISOLATION_DEMO_ORGANIZATION_ID` (`...0102`), defined
once in `app.models.core` and restated as literals inside the migration (migrations stay
self-contained and never import application code that could change independently later). The
default organization owns every repository that existed before this migration (backfilled) and
every repository created without an explicit organization since; the isolation-demo organization
deliberately owns no repository data -- its only purpose is to make a second, real tenant
provably present for cross-tenant negative tests, now and in later slices.

**5. `server_default` preserves single-tenant backward compatibility exactly -- temporarily.**
`Repository.tenant_id`'s server-side default is the default organization's id. Every existing
construction path -- the importer's `_get_or_create_repository` (a `pg_insert(...).values(name=...,
source_url=...)` that never mentions `tenant_id`) and every existing test's `Repository(...)`
construction -- continues to produce a valid row completely unchanged. This is why the migration
required zero changes to the importer and zero changes to any existing test fixture: the database
itself fills in the correct tenant for any caller that doesn't yet know to specify one.

This is explicitly **temporary compatibility behavior for Slice 1 only**, not a permanent design
choice. Once request-level tenancy exists, the same default becomes a tenant-isolation risk: a
repository-creation path that forgets to pass an explicit tenant would silently succeed into the
default organization instead of failing, which is the opposite of what a tenant boundary should
do. Concretely, and non-negotiably: Slice 2 must make every repository-creation path pass an
explicit tenant derived from request identity, the `server_default` **must be removed** before
Critical Gate G4 can be considered satisfied, and Slice 2's test suite must include a test proving
that a repository-creation attempt with no tenant context in scope fails closed (rejected) rather
than silently falling back to the default organization. See Revisit conditions.

**6. Downgrade is guarded: it refuses rather than silently destroying tenant-owned data.**
`downgrade()` inspects the `organizations` table *before making any schema change* and counts rows
other than the two deterministic built-ins. Downgrading drops the `organizations` table entirely;
because only the two deterministic ids are known in advance, a later `upgrade head` can only ever
recreate those two rows, never a custom organization created after this migration ran. If any such
organization exists -- empty or already owning a repository, either way -- `downgrade()` raises
`DowngradeWouldDestroyTenantDataError` with an actionable message and aborts before dropping the
foreign key, changing `tenant_id`'s nullability or default, or dropping the table. Alembic's
transactional DDL means this abort is clean: nothing below the check ever executes, and
`alembic_version` stays at `20260917_0005`. The migration never silently deletes a custom
organization's data and never silently reassigns its repositories to the default organization --
the only way past the guard is an explicit, manual operation (reassign or remove the
custom-organization-owned repositories and delete the organization row) performed outside this
migration. With only the two deterministic organizations present -- the only state Slice 1 itself
ever produces -- downgrade proceeds exactly as before: it restores the pre-0005 nullable,
unenforced schema, and a subsequent `upgrade head` recreates the two deterministic rows with their
fixed ids, making the already-populated `tenant_id` values valid again automatically. That
guarantee does not, and was never meant to, extend to organizations this migration didn't create.

## Alternatives considered

- **Cascading delete (`ON DELETE CASCADE`).** Rejected: would let deleting one `Organization` row
  silently delete every repository, issue, analysis, recommendation, decision, chunk, and audit
  event it owns -- the opposite of what a tenant-isolation boundary should protect against.
- **Randomly generated seed organization ids.** Rejected: the migration's backfill, the ORM's
  `server_default`, and every test that needs to reference "the default org" or "the isolation
  org" would otherwise have no way to agree on which row is which without an extra lookup: fixed,
  deterministic literals (matching the project's existing `LOCAL_REVIEWER_ID` precedent) make
  every one of those call sites trivially consistent.
- **Renaming `tenant_id` to `organization_id`.** Rejected for this slice: the column already
  exists, is already referenced in one API schema, and ADR 0005 already used "tenant" as its
  vocabulary; renaming is a larger, purely cosmetic diff for no behavioral benefit and can be
  revisited later if it ever causes real confusion.
- **Making `tenant_id` required (no server default) and updating every construction path
  explicitly.** Rejected as the wrong size for this slice: it would touch the importer and
  upwards of a dozen test files for no benefit yet, since no code today has any concept of "which
  organization is this request for" to supply explicitly -- that only becomes meaningful once
  request identity exists (a later slice). The server default is the smaller, fully reversible
  choice; nothing prevents removing it once real multi-tenant creation paths exist.

## Consequences

**Positive:** every table's ownership chain now terminates at a real, enforced tenant boundary;
the existing single-tenant demo continues to work with zero code changes outside the model and
migration themselves; a second, real organization exists for isolation testing without any
synthetic/fake test-only construct.

**Negative/residual:** the `server_default` that preserves backward compatibility today is also a
risk for later slices -- once real multi-tenant repository creation exists, a caller that forgets
to specify an explicit organization will silently succeed into the default org rather than failing
loudly. This is acceptable now (no such caller exists yet) but the default is temporary and its
removal is a hard prerequisite for Slice 2/G4, not an optional cleanup (see Decision 5 and Revisit
conditions). Separately, the downgrade guard (Decision 6) means this database can permanently lose
the ability to downgrade below revision `20260917_0005` the moment any custom organization is
created -- an accepted tradeoff, since the alternative (silently destroying or unowning tenant
data on downgrade) is worse.

**Operational/maintenance:** any future organization-scoped feature should join through
`Repository.tenant_id`, exactly like every existing repository-scoped feature already joins
through `Repository.id`.

## Security implications

This slice is a **data-model foundation only**. It does not implement, and must not be described
as implementing: request identity, authentication, role enforcement, or API/service-level tenant
scoping. No endpoint currently filters by organization. Critical Gate G4 ("Automated negative
tests prove cross-tenant records and vector results are inaccessible") remains open until a later
slice adds request-level enforcement and the full negative-test suite across records, vectors,
analyses, and traces. The tests added alongside this ADR prove the *data model* rejects invalid
ownership (an unknown organization, a null tenant, a delete that would orphan data) -- they do not
and must not claim to prove API-level isolation.

## Testing/evidence

- Model/constraint tests: two organizations can coexist; a repository requires exactly one valid
  organization; an unknown organization id is rejected by the foreign key; a null `tenant_id` is
  rejected; deleting an organization that owns a repository is rejected by `RESTRICT`; `slug`
  uniqueness is enforced.
- Migration/backfill tests: a disposable 0004-era database with a null-`tenant_id` repository
  upgrades cleanly to 0005, is backfilled to the default organization, and every existing
  downstream record (issues, analyses, recommendations, decisions, stage attempts, audit events,
  retrieval chunks) survives with unchanged ids and content; both deterministic organizations
  exist exactly once; with only the two deterministic organizations present, `downgrade -1`
  followed by `upgrade head` completes cleanly and restores valid ownership without re-running any
  special-case logic (a direct consequence of using deterministic ids).
- Downgrade-guard tests (Decision 6): `downgrade -1` is refused, raising
  `DowngradeWouldDestroyTenantDataError`, when an extra organization exists beyond the two
  deterministic built-ins -- proven both for an empty custom organization and for one that already
  owns a repository (with a full representative downstream record chain). Both cases assert that
  `alembic_version` remains at `20260917_0005` after the refusal and that every organization,
  repository, foreign key, non-null constraint, ownership value, and downstream record is left
  completely unchanged.
- An explicit, clearly named test documents that Slice 1 provides no request-level tenant
  isolation -- it must not be confused with a Critical Gate G4 pass.

## Revisit conditions

- **Required, not optional:** Slice 2 must make every repository-creation path pass an explicit
  tenant derived from request identity, and must remove `tenant_id`'s `server_default` so that a
  caller with no tenant context fails closed instead of silently defaulting. This removal is a
  hard prerequisite for Critical Gate G4 -- G4 cannot be considered satisfied while the default is
  still in place, since a forgotten-tenant bug would silently and invisibly assign data to the
  default organization rather than being rejected. Slice 2's test suite must include a test that
  proves a repository-creation attempt with no tenant context in scope is rejected, not defaulted.
- When roles/permissions are added, revisit whether `Organization` needs any additional field
  (e.g., a display-only settings blob) -- do not add one speculatively now.
- When an organization-deletion workflow is ever required, design it as an explicit,
  audited, multi-step operation that first disposes of owned repositories -- never as a change to
  this migration's `RESTRICT` policy.
- The downgrade guard (Decision 6) is final for Slice 1's data shape, not a placeholder: it is not
  expected to need revisiting unless a future migration changes what "the two deterministic
  organizations" means.
