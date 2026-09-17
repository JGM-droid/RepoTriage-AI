# ADR 0014: Synthetic Demo Identity, Role Authorization, and Backend-Enforced Tenant Isolation

**Status:** Accepted
**Date:** 2026-09-18

## Context

Milestone 3.1 Slice 1 (ADR 0013) built the tenant *data model*: two organizations, a real,
enforced `Repository.tenant_id` foreign key, and an explicit statement that Critical Gate G4
("Automated negative tests prove cross-tenant records and vector results are inaccessible")
remained open. Nothing in Slice 1 read a caller's identity, enforced a role, or scoped a single
API query by tenant.

This ADR covers Slice 2: request identity, role-based authorization, and backend-enforced tenant
scoping across every protected resource, retrieval path, and workflow stage. It is still not a
production authentication system -- see the security boundary section below, which is the most
important part of this document.

## Decision

**1. Synthetic demo identity, not authentication.** A new `Actor` table (migration `20260918_0006`)
holds a fixed, deterministic set of demo identities: three in the default organization (viewer,
reviewer, administrator) and two in the isolation-demo organization (viewer, administrator). A
request names one via the `X-Demo-Actor-ID` header; the server looks it up and, if found and
enabled, trusts its `organization_id`/`role` -- **and only those, never anything the client also
sends**. There is no password, session, token, expiry, or signature anywhere in this mechanism.
Anyone who knows or guesses an actor id can act as that actor. This is real enough to prove
backend-enforced authorization *given* a known identity; it is not real enough to establish that
identity in the first place. A production deployment would replace `resolve_demo_actor` (see
`app.api.identity`) with verification of a signed credential (OAuth/OIDC or equivalent) issued by
an identity provider the server can independently verify. Every place this mechanism is
implemented, tested, or described says so explicitly, including this ADR, the module's own
docstring, the API's demo-actor-listing response body (`notice` field), and the frontend selector.

**2. A minimal, closed role set: `viewer` / `reviewer` / `administrator`.** Enforced by a database
`CHECK` constraint (so an invalid value can never be stored) and by `app.api.identity.require_role`.
Role policy for this slice:

| Role | Read own tenant | Start triage | Submit human decision |
|---|---:|---:|---:|
| Viewer | Yes | No | No |
| Reviewer | Yes | Yes | Yes |
| Administrator | Yes | Yes | Yes |

Administrator carries no capability beyond reviewer in this slice, because no meaningful
administrator-only action exists yet in this application (there is no user management, billing, or
configuration surface to gate). Inventing one to showcase the role would be scope padding, not a
real security boundary; the role exists in the data model and is exercised by tests, ready for a
real administrator-only capability whenever one is added.

**3. `401` / `403` / `404` mean three different things, on purpose.** `401` (`app.api.identity`):
the server does not know who is asking -- missing header, malformed id, unknown id, or a disabled
actor, deliberately collapsed into the same status code (though not the same message) so a caller
cannot use the response to enumerate valid actor ids by trial and error. `403`
(`require_role`): the server knows exactly who is asking, and that actor's role does not permit
this action within *their own* organization. `404` (every tenant-scoped lookup in
`app.api.scoping`): a resource id that does not resolve within the caller's organization -- whether
because it belongs to another organization or because it never existed at all. These two cases are
made indistinguishable on purpose: if a wrong-tenant id produced a `403` instead, a valid actor
could probe arbitrary ids and learn which ones exist in another organization before ever being
told "no." A resource that isn't yours must look exactly like a resource that doesn't exist.

**4. Centralized tenant scoping via existing foreign-key joins, not a redundant `tenant_id` column
on every table.** `app.api.scoping` provides one query/lookup helper per resource type, each
joining back to `Repository.tenant_id` (ADR 0013's boundary) rather than denormalizing a tenant
column onto `Issue`, `Analysis`, `Recommendation`, `HumanDecision`, or `StageAttempt`.
`RepositoryDocument`, `RetrievalChunk`, and `AuditEvent` already denormalize `repository_id`
directly (a pre-existing Milestone 2.3/1.1 design choice, for query/index locality, not for tenant
scoping) and are scoped the same way, one join shorter. `Issue` is the single verified entry point
for the deepest chain (`Analysis` -> `Recommendation` -> `HumanDecision`,
`Analysis` -> `StageAttempt`): once an issue is confirmed to belong to the caller's organization,
every row reached only by following foreign keys from it is safe to read, because nothing
downstream can reference a different issue than the one already checked. This keeps the
enforcement surface small and auditable -- one join pattern per resource, defined once, instead of
tenant logic scattered through every route.

**5. Retrieval isolation happens inside the query, before ranking.** `app.retrieval.service.
retrieve_related_evidence` already filtered every candidate to one repository
(`RetrievalChunk.repository_id == request.repository_id`) since Milestone 2.3 -- this was a
per-repository scope for evidence relevance, not originally framed as a tenant boundary, but
because every repository belongs to exactly one organization (ADR 0013), it already *was* one. No
cross-tenant chunk was ever fetched from the database, ranked, or handed to the AI stage; there was
no "filter the response after the fact" step to remove, because none ever existed. Slice 2 adds a
defense-in-depth assertion immediately after the query executes, asserting every returned row's
`repository_id` matches the request -- structurally unreachable given the `WHERE` clause above, but
it fails loudly and immediately, before any chunk reaches ranking, citation construction, or the
rendered prompt, if a future edit to that query ever weakens the filter.

**6. The worker re-derives ownership from the database at every tenant-sensitive stage boundary,
not just once at start; nothing tenant-shaped is ever in the Celery payload to forge.**
`execute_triage_workflow.delay(str(analysis.id))` passes only an analysis id (unchanged since
Milestone 2.1) -- no tenant id, actor id, or role ever crosses into the task message.
`process_workflow_run` re-fetches `Analysis` and `Issue` from PostgreSQL by id on every run/resume;
`retrieve_related_evidence`'s `repository_id` is read from that freshly-fetched `Issue`, never from
anything a caller supplied. Authorization for *starting* a workflow (role + tenant match) is
checked once up front by `start_issue_triage` (`app.api.v1.triage`), before the `Analysis` row is
even created -- but authorization is dynamic state, not a fact established once and then trusted
for the rest of a potentially long-running, retried, and resumable background job. The initiating
actor could be disabled, moved to another organization, or downgraded in role after the workflow
starts but before it finishes; a durable workflow must not treat an earlier success as still true.
The single canonical guard function, `_authorize_workflow_run`, is therefore called again at three
further points, each recorded as its own named `StageAttempt` so `_run_stage`'s idempotent-resume
logic -- which only ever skips a stage that already succeeded under its own exact name and attempt
number -- can never let one earlier authorization success stand in for a later one:
`authorize` (before any work begins) -> `authorize_before_retrieval` (immediately before
`retrieve_related_evidence`, which reads another tenant's data if it were ever wrongly allowed to)
-> `authorize_before_ai_inference` (immediately before the external AI provider call) ->
`authorize_before_persistence` (immediately after `human_review`, immediately before the
`Recommendation`/stage-chain state it produced is treated as final). There is no transactional
protection across the external AI provider call itself -- a real network call cannot be wrapped in
a database transaction -- which is exactly why authorization is checked again immediately before
that call, and a fourth time immediately before persisting its result, rather than assumed to still
hold from an earlier check. A crash and resume mid-attempt cannot skip any of this: because each
checkpoint is its own stage name, resuming an attempt always re-runs whichever checkpoint has not
yet succeeded, so a resumed workflow re-validates current actor and ownership state before its next
tenant-sensitive action even when an earlier checkpoint in the same attempt already succeeded
before the crash. A disabled or cross-tenant actor caught at the very first check is still rejected
by the original `401`/`404` request-time check before any `Analysis`/`StageAttempt`/`Recommendation`
row is ever written; everything past that point is this four-checkpoint worker-side re-validation.

**7. `Repository.tenant_id`'s Slice 1 `server_default` is removed.** Migration `20260918_0006`
drops it. Every repository-creation path (`app.importer.service._get_or_create_repository`, and
therefore `import_records`/`import_fixture`) now requires an explicit `tenant_id` keyword argument
-- there is no default to silently fall back to. A caller with no tenant context fails closed: a
missing `tenant_id` is a Python `TypeError` before any database call, and an unrecognized
organization id fails at the database's foreign-key constraint. This closes the residual risk ADR
0013 flagged: a forgotten-tenant repository-creation bug can no longer silently land in the default
organization.

**8. The demo-actor-listing endpoint exists only when explicitly enabled.** `GET
/api/v1/demo/actors` (read-only, no credential of its own) lets a frontend selector discover the
deterministic actors without hardcoding their ids in client source. It is registered on the FastAPI
app only when `Settings.demo_mode_enabled` is `True` (default `False` everywhere, including the
live demo) -- not merely gated behind a runtime check, but genuinely absent from the app's route
table otherwise, so a deployment that never opts in never exposes it at all.

**9. The isolation-demo organization's synthetic data is seeded by an idempotent function, not a
migration.** `app.demo.isolation_seed.seed_isolation_demo_organization` reuses the existing
importer/ingestion code paths against a small, entirely synthetic, hand-authored fixture
(`backend/fixtures/isolation_demo/`: a fictional repository, three closed issues, two short
documentation excerpts) -- no real customer or repository data. Migrations must never carry mutable
demo/seed *content* (only the fixed organizational/actor scaffolding itself, per Decision 1); this
keeps that principle intact while still making retrieval isolation demonstrable end to end. Tests
call this function directly against disposable databases. It has not been run against the live
demo database as part of this slice -- that remains a separate, later, explicitly-approved step.

## Alternatives considered

- **A `tenant_id` column denormalized onto every table.** Rejected: every table in this schema
  already reaches `Repository` through an existing foreign key (directly or transitively), so a
  redundant column would duplicate data that can drift from its source of truth, for no query
  capability the join-based helpers in `app.api.scoping` don't already provide.
- **Returning `403` for a cross-tenant resource id.** Rejected: it would let a valid actor
  distinguish "exists in another org" from "doesn't exist anywhere," which is itself a disclosure
  Milestone 3.1's own rubric item (G4) treats as a failure, not merely an inconvenience.
  Cross-tenant and genuinely-missing must be indistinguishable.
- **Gating the demo-actor endpoint with a runtime `if not settings.demo_mode_enabled: raise 404`
  check inside the route instead of conditionally registering the router.** Rejected: a disabled
  endpoint should be genuinely absent from the app's route table, not merely return an error on
  every call -- the latter still reveals the route exists and responds, which is a smaller but
  needless disclosure for a mechanism that should default to fully off.
- **A separate identity-resolution database session from each route's own session.** Rejected
  during implementation: it would silently break the existing single-dependency-override test
  pattern (`app.dependency_overrides[get_issue_session]`) and, worse, mean identity resolution and
  the route body could observe different transactions. `app.database.get_session` is now the one
  shared session dependency every router's `get_*_session` aliases and `app.api.identity` both
  depend on.
- **Adding new REST endpoints for `Repository`/`Analysis`/`AuditEvent`, etc., just to demonstrate
  isolation for each resource type.** Rejected: several of these resources (repositories,
  standalone analysis lookup, audit events) have no existing API consumer, and the task governing
  this slice explicitly warns against inventing an endpoint merely to exercise a capability.
  Isolation for these is proven directly against the `app.api.scoping` helpers and the retrieval
  service in tests, and indirectly through the existing endpoints that do expose nested data
  (`GET /issues/{id}/triage` surfaces analysis/recommendation/human-review/stage-attempt state, all
  gated by the same tenant-scoped issue lookup).

## Consequences

**Positive:** every protected read/write path now derives organization and role from a persisted,
backend-controlled record, never from anything a client asserts; retrieval isolation was already
structurally sound and now has both tests and a defense-in-depth assertion proving it; the
`tenant_id` server-default risk ADR 0013 flagged is closed.

**Negative/residual:** `X-Demo-Actor-ID` remains, by design, not a security boundary against
*impersonation* -- only against a *legitimately-identified* actor exceeding their own role or
tenant. Anyone who can reach the API at all can act as any actor whose id they know. This is
acceptable and disclosed for a demo-identity milestone; it must not persist into a production
deployment. Rate limiting (ADR 0012) is still IP-keyed, not actor-keyed -- unchanged in this slice.

**Operational/maintenance:** any new tenant-owned table should join back to `Repository.tenant_id`
exactly like every table added in this slice does, and any new state-changing endpoint should use
`require_role` plus the appropriate `app.api.scoping` helper rather than inventing its own check.

## Security implications

This slice proves *backend-enforced* authorization and tenant isolation for every path audited in
its test suite (see Testing/evidence) -- given a known, enabled actor id, the server never lets
that actor exceed their role or read another organization's data, including through retrieval,
vector search, and the background workflow. Authorization is treated as dynamic state throughout
the workflow, never as a fact established once and cached for the rest of a run: the background
worker independently re-validates the initiating actor and ownership immediately before each
tenant-sensitive action -- retrieval, the external AI inference call, and persisting that call's
result -- via four checkpoints (Decision 6), so a crash-and-resume mid-attempt, a retry, or an actor
whose role/organization/enabled state changes partway through a run can never ride on an earlier
checkpoint's success. This does not, and cannot, extend to wrapping the external AI provider call
itself in a database transaction -- no real network call can be -- which is precisely why that call
is bracketed by its own checkpoints immediately before and immediately after it, rather than relying
on a transactional guarantee that does not exist. It does **not** prove, and must never be described
as proving: that only legitimate users can obtain a valid actor id in the first place (there is no
authentication), that a request cannot be replayed or forged by anyone who can reach the API network
boundary at all, or that rate limiting is tenant-aware. Critical Gate G4 is proven **in isolated
automated tests** by this slice; it is not yet confirmed against the live demo database, which
still requires a separate, explicit, Jesse-approved live ownership walkthrough (see the roadmap).

## Testing/evidence

- Identity: missing/malformed/unknown/disabled-actor header all produce `401`; a client cannot
  supply or override organization/role independently of the persisted actor record.
- Roles: viewer can read, cannot start triage or submit a decision (`403`); reviewer and
  administrator can do both; a same-tenant insufficient role is `403`.
- Cross-tenant isolation: two real organizations, real persisted actors, proving actor-from-A
  cannot reach organization B's repositories, issues, analyses, recommendations, human decisions,
  repository documents, retrieval chunks, stage attempts, or audit events -- through both list
  filtering and guessed direct ids, every cross-tenant direct lookup returning `404`.
- Retrieval: organization A's analysis cannot retrieve organization B's chunks even when B's
  content is deliberately near-identical (maximal embedding similarity under the deterministic fake
  adapter); tenant filtering is exercised at the real `retrieve_related_evidence` query, not only
  by inspecting the API response shape.
- Workflow: the worker re-derives ownership from the database at four independent checkpoints
  (`authorize`, `authorize_before_retrieval`, `authorize_before_ai_inference`,
  `authorize_before_persistence`), not only once at the start; a disabled or cross-tenant actor, or
  one downgraded below reviewer, is caught at whichever checkpoint comes after the state change --
  proven with tests that flip actor/organization/role state at the exact boundary between two real
  stages (via monkeypatched hooks on the real stage functions, not by calling the guard directly) --
  and everything from that checkpoint onward never runs, with zero `Recommendation` or
  `HumanDecision` created; a crash and resume immediately after a successful `authorize` checkpoint
  re-validates at `authorize_before_retrieval` rather than trusting the earlier success, and fails
  closed if state changed in between; exactly one recommendation/stage chain is created for an
  authorized request whose state never changed mid-run, and existing retry/resume/idempotency
  behavior is unaffected; the AI narrative never creates a `HumanDecision`.
- Explicit ownership: after migration `20260918_0006`, a repository-creation call with no
  `tenant_id` fails, an unknown organization id fails, a valid explicit organization succeeds, and
  no server default remains.
- Demo mode: the actor-listing endpoint exists only when `demo_mode_enabled` is `True` and is
  genuinely absent (not merely erroring) otherwise.
- Failure-proof demonstration: a temporarily removed tenant predicate makes a named cross-tenant
  test fail with visible cross-organization data leakage; a temporarily removed role guard makes a
  named viewer-authorization test fail; a temporarily removed `authorize_before_ai_inference`
  re-validation call site makes its corresponding boundary test fail by letting the workflow
  complete using a stale, no-longer-valid authorization instead of blocking; all three were restored
  and the full suite re-verified green afterward (see the roadmap/traceability entries for this
  slice's exact run).

## Revisit conditions

- When a real identity provider is integrated, `app.api.identity.resolve_demo_actor` is the one
  function to replace; `AuthenticatedActor`, `require_role`, and every `app.api.scoping` helper
  downstream of it should need no change, since they already only depend on an
  `(organization_id, role)` pair, not on how that pair was established.
- When a genuine administrator-only capability is added, gate it with
  `require_role(ROLE_ADMINISTRATOR)` rather than reintroducing a bespoke check.
- When rate limiting is revisited, consider keying it by actor id in addition to (or instead of)
  client IP now that a real, backend-verified identity concept exists.
- When Jesse approves a live ownership walkthrough for this slice, that walkthrough -- not this
  ADR -- is what closes Critical Gate G4 against the live demo database.
