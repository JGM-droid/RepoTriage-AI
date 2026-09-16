# ADR 0012: AI Guardrails and Adversarial Evaluation

**Status:** Accepted
**Date:** 2026-09-15 (corrected same-day: redaction-policy versioning, rate-limiter
atomicity, and two additional bounded input fields -- see Decisions 5, 8-9, and 4a below)

## Context

Milestone 2.6 requires: treating issue and retrieved text as untrusted data; prompt-injection
fixtures; structured-output validation and fail-closed behavior; a tool/action allowlist; and
API limits, safe errors, rate limits, and secrets scanning (rubric §G; Critical Gate G5 --
"Prompt-injection fixtures fail safely; no committed secrets; security checks pass").

A readiness review (see the Milestone 2.6 readiness report, not a standalone document) found
that most of the underlying mechanisms already existed from Milestones 2.2-2.5: citation
allowlisting, deterministic fallback, provider-neutral routing, a hash-pinned prompt registry,
bounded/sanitized issue import, and a mandatory human-review boundary (ADR 0003). The one
concrete gap was that the active prompt template (`triage_narrative@1.0.0`) framed only
*retrieved* repository evidence as untrusted data -- never the current issue's own title/body,
which is the more realistic injection surface, since anyone can file a GitHub issue. There was
also no adversarial fixture, no output-length bound, no secret-redaction path, and no rate
limiting anywhere in the API layer.

This application remains entirely non-agentic: the AI stage produces narrative text only, which
is displayed and persisted, never executed. This ADR does not introduce, and explicitly rejects,
any capability that assumes otherwise.

## Decision

**1. Non-agentic threat model.** Trusted: the fixed prompt template, deterministic triage rules,
gateway/router code, workflow stage machinery, the human-review boundary. Untrusted: the current
issue's title/body (attacker-controlled at ingestion) and retrieved evidence excerpts
(attacker-controlled one hop removed, via an earlier ingested issue). The realistic attack goal
is manipulating the AI narrative's content or citations, or exfiltrating a secret pasted into
issue text -- not remote code execution or tool misuse, since no tool-execution surface exists.

**2. Untrusted-data framing as risk reduction, not immunity.** `triage_narrative` is superseded
by a new, hash-pinned `1.1.0` release (`docs.../app/ai_gateway/prompts.py`) that: frames *both*
the current issue's evidence and retrieved repository evidence as untrusted data, not
instructions; adds explicit, app-controlled `BEGIN`/`END` section boundaries (fixed literal
strings emitted by `render_active_prompt`, never derived from request data, so injected content
cannot forge or prematurely close them); states the model must never claim to have performed an
action; and states the model should say evidence is insufficient rather than invent it. This is
explicitly documented as risk reduction: prompt wording and delimiters do not "solve" prompt
injection, and a sufficiently capable adversarial model could still be misled by injected text
that never crosses one of the deterministic boundaries below. `1.0.0` remains registered,
unmodified, for historical inspection.

**3. Deterministic output/citation constraints.** `AIResponse` gained a provider-neutral
`MAX_NARRATIVE_LENGTH` (4000 characters) bound, enforced in `__post_init__` for both adapters;
the OpenAI adapter rejects an oversized narrative as a controlled failure
(`openai_narrative_too_long`) before ever constructing a response. `validate_citations` gained
two checks: duplicate citation identifiers are rejected, and citation count may never exceed the
number of records actually supplied as `retrieved_context`. These are exact, deterministic
invariants -- not schema validation against a provider-specific structured-output format (see
Alternative 1).

**4. Bounded inputs and outputs.** Reused, not reimplemented: `app.importer.sanitize` already
bounds title (500 chars) and body (20000 chars) and strips C0/C1 control characters at import
time; `app.triage.rules._excerpt` already bounds any evidence excerpt to 240 characters before it
reaches a prompt; `RetrievalRequest.max_results`/`max_excerpt_chars` already bound retrieved
context. The new narrative-length bound (above) closes the one missing bound, on the output side.

**4a. State-changing request-body limits (correction).** A verification pass found two
free-text fields on the two state-changing endpoints that were parsed and held in memory by
Pydantic before any downstream check could reject them: `TriageRequest.ruleset_version` (compared
only after full parsing against the one valid `TRIAGE_RULESET_VERSION`) and
`TriageDecisionRequest.decision` (compared only after full parsing against
`ALLOWED_DECISIONS = ("approve", "reject", "request_revision")`). Both gained a conservative
`Field(max_length=...)` bound (20 and 32 characters respectively -- generous headroom over the
longest real value, 17 characters) so an oversized value now fails fast with a safe 422 instead of
being fully parsed first. `TriageDecisionRequest.rationale` already had `max_length=1000` from an
earlier milestone; no other state-changing request field exists (`app/api/v1` has exactly these
two `POST` endpoints).

**5. Narrow, provider-bound secret redaction -- as a versioned, hash-pinned policy (corrected).**
A new module, `app.ai_gateway.redaction`, defines `RedactionPolicy`: an immutable,
semantically-versioned, hash-pinned set of pattern definitions, structurally mirroring
`app.ai_gateway.prompts.PromptVersion` (ADR 0010) -- `__post_init__` recomputes `policy_hash` from
the actual pattern definitions and raises at import time if it does not match the pinned literal.
The initial release, `provider_input_redaction@1.0.0` (`released`), matches exactly three
high-confidence, well-known credential formats -- an OpenAI-style key (`sk-[A-Za-z0-9_-]{20,}`), a
GitHub personal-access token (classic `gh[pousr]_...` or fine-grained `github_pat_...`), and a PEM
private-key block -- and replaces a match with a visible `[REDACTED:<pattern_name>]` marker.

*Why this needed versioning (a correction found before Jesse's ownership check):* Milestone 2.4
guarantees a historical analysis's exact `rendered_prompt_hash` stays reproducible (ADR 0010).
Once redaction runs inside the same prompt-generation pipeline, an unversioned redaction
implementation would silently break that guarantee the first time a pattern was ever tweaked --
replaying an old analysis's evidence through the *current* patterns would not necessarily produce
the same redacted text, hence not the same hash, and there would be no recorded identity to even
know which rules had actually run (only pattern *names*, e.g. `"openai_api_key"`, were being kept
-- a name is stable across a regex edit and is not itself a version). `RedactionPolicy` closes
this: superseded versions stay registered (`get_redaction_policy(policy_id, version)`) for exact
historical resolution, independent of whatever policy is active now.

Applied exactly once, in `app.ai_gateway.router.route_ai_inference`, to the `AIRequest`'s
`evidence` and `retrieved_context` fields *before* `render_active_prompt` or either adapter runs,
so the rendered prompt text, the real provider's outgoing payload, and the mock adapter's own
narrative (which reads `AIRequest` fields directly, not the rendered text) all see the same
redacted content. Deliberately excluded: a broad "anything KEY=value-looking" regex, which would
destroy legitimate code samples and Markdown in real issue text. Application-controlled secrets
(the configured `OPENAI_API_KEY`, database credentials) are never redaction targets because they
are never placed into any `AIRequest`/`EvidenceItem`/`RetrievedRecord` field in the first place --
`AIRequest` has no field for them, a structural guarantee, not a runtime check.

*Scope of the redaction guarantee -- what it does and does not cover.* Redaction applies only to
the content the AI gateway actually sends onward or that a model influences: the rendered prompt
text, the outgoing provider payload, and `AIResponse.narrative`/`.citations`. It does **not**
apply to, and must never be described as applying to, the separately-persisted deterministic
snapshots that predate the AI-gateway stage: `Recommendation.content["evidence"]` (the current
issue's own excerpt, written by the evidence-retrieval stage before redaction ever runs) and
`Recommendation.content["retrieved_evidence"]` (the real retrieval result, also written before
redaction runs) legitimately retain the *original*, unredacted text -- including any secret
pattern present in it -- exactly as `app.importer.sanitize`'s pre-existing, unchanged policy for
imported issue content already intended. This module and this milestone are not a retroactive
database DLP layer over that snapshot; they narrow the provider-bound and model-influenced surface
only. A claim that "no sensitive value is ever present anywhere in a `Recommendation`" would be
false and must not be made.

**6. Safe fallback, always observable, with identical full provenance (corrected).** Every
controlled failure this milestone adds (oversized narrative, invalid citation count/duplicates)
routes through the existing `ProviderCallFailed` -> mock-adapter fallback path (ADR 0008),
unchanged. `AIResponse` gained `redaction_events` (pattern names, never matched values) plus
`redaction_policy_id`/`_version`/`_status`/`_hash` -- populated identically on every path (mock
success, real-provider success, and fallback) via one shared `_with_redaction_provenance` helper
in the router, so the fallback path cannot silently diverge from the success path's provenance
shape. All of it survives into the existing `AuditEvent` written by
`app.workflow.tasks._record_ai_routing_event`, and into the API's `TriageAIInference` schema for
technical/audit inspection (no frontend display). The four policy fields are always populated,
even when zero events fired, so "nothing was redacted" is attributable to a specific,
historically-resolvable policy version -- never ambiguous with "no policy ran at all."

**7. Adversarial evaluation, extending the Milestone 2.5 harness.** A new, versioned fixture
(`backend/tests/evaluation/adversarial_cases.json`, 15 cases) and module
(`app.evaluation.adversarial`) form Section D of `python -m app.evaluation`, kept explicitly
separate from Sections A (triage), B (retrieval policy), and C (narrative/citation wiring). Every
case runs the real `classify`/`assess`/`propose`/`human_review` functions and the real
`route_ai_inference` (therefore also the real redaction path) -- never a reimplementation.
Categories: prompt injection (current-issue and retrieved-evidence), a request to reveal an
application secret, a high-confidence secret embedded in issue text, a fabricated citation, empty
and malformed provider responses, an oversized provider narrative, oversized/repeated issue
content, HTML/Markdown/script-looking content, a Unicode format-character edge case, a provider
timeout, fallback-provenance preservation, the human-review boundary, and (the absence of) tool
execution. Every Section D case is a deterministic, blocking invariant -- there is no
known-limitation carve-out here, unlike Section B's #6139. The section explicitly states, in its
own report output and in code: *"These deterministic adversarial checks validate application
guardrails; they do not prove that every real model will resist every prompt-injection
technique."* Keyword/pattern matching in this fixture is not, and must never be described as,
comprehensive prompt-injection detection.

**8. Rate limiting (canonical scope, confirmed via Phase 0).** The roadmap's Milestone 2.6 bullet
explicitly reads "API limits, safe errors, rate limits, and secrets scanning" -- rate limiting is
required, not optional, though the rubric's broader "authentication, authorization, rate
limiting, input limits, and safe error handling" row is only partially addressed here: auth/RBAC
is explicitly deferred (it depends on a multi-tenant/user model that does not exist yet; see
Release 3 and Critical Gate G4, already out of this milestone's scope). A new
`app.api.rate_limit` module implements a fixed-window limiter backed by the existing Redis service
(already a dependency, via Celery's broker), keyed by `(route_name, client_ip)` -- independent per
route and per client. It applies only to the two state-changing endpoints (`POST .../triage`,
`POST .../triage/decision`); every read-only GET endpoint is never rate-limited. Defaults (20
requests/60s per client per route) are demo-friendly: generous enough that a normal ownership
walkthrough never trips them, tight enough to catch an accidental rapid-duplicate submission. A
`RateLimitExceeded` exception is handled by one dedicated FastAPI exception handler returning the
same flat `ErrorResponse` shape as every other error in this API (never FastAPI's default
`{"detail": ...}` envelope), plus a `Retry-After` header.

**8a. Atomic increment-and-expire (correction).** The first implementation issued the increment
and the expiry as two separate, unprotected `INCR`/`EXPIRE` Redis calls. A verification pass
correctly flagged this: if the process or connection failed between the two calls, the counter key
could persist indefinitely with no TTL (`-1`), permanently rate-limiting that client on that route.
The corrected implementation (`_INCR_AND_ENSURE_TTL_SCRIPT`) issues both as one Lua script
evaluated atomically by Redis's single-threaded execution model -- the whole script commits or
none of it does, so no client can ever observe the key between the increment and the expiry
assignment. The script additionally self-heals on every call (not only the first increment): if
the key exists with no TTL for any reason, it assigns one immediately, so the "no TTL forever"
failure mode is structurally impossible going forward, not merely less likely.

**9. Redis-unavailable behavior: fail open (explicit choice).** If Redis cannot be reached within
a 1-second timeout, the rate limiter allows the request through rather than rejecting it.
Rationale: both rate-limited endpoints already depend on Redis to do anything useful downstream
(starting a workflow needs Celery's broker, which is Redis) -- a Redis outage already degrades
the system, so rejecting requests here in addition protects nothing Redis's own unavailability
doesn't already block, while taking an otherwise-partially-working API fully offline. This
limiter exists to catch accidental rapid duplicate submissions in a demo-scale, unauthenticated
application, not to defend a high-value target against a determined attacker; fail-closed would
be the wrong tradeoff for that threat model. This is a deliberate choice, not a default left
unexamined -- see `app/api/rate_limit.py`'s module docstring and `test_redis_being_unavailable_fails_open`.

**10. Secrets scanning.** Already satisfied by the existing `gitleaks-action@v2` CI step (Milestone
0.2 era); no new scanning tool was added.

## Alternatives considered

- **Provider-specific structured-output/JSON mode for the OpenAI adapter.** Rejected for this
  milestone: it would couple the shared, provider-neutral gateway contract to one provider's
  response format (the mock adapter has no equivalent "JSON mode," and building a parallel schema
  for it just to keep parity is wasted surface). The existing free-text + `Citations:`-line
  parsing, plus the exact-match citation/length checks added here, is the smaller, defensible
  choice. **Revisit condition:** if the narrative/citation format ever needs machine-parsed fields
  beyond a flat narrative and a citation list, reconsider a structured schema for the OpenAI
  adapter specifically, keeping the shared `AIRequest`/`AIResponse` contract provider-neutral.
- **A tool/action allowlist policy engine.** Rejected: no tool-execution or agentic-loop
  capability exists anywhere in this codebase today (the AI stage produces narrative text only,
  consumed and displayed, never triggering an external call or state change beyond storing that
  narrative). Building an allowlist for a capability that does not exist would be speculative
  infrastructure with no rubric point or Critical Gate behind it today. Section D's
  `adv_no_tool_or_action_execution` case documents and regression-tests the absence instead.
- **Broad enterprise DLP / a generic `KEY=value`-shaped secret regex.** Rejected: would produce
  false positives against ordinary code samples and Markdown in real issue text (explicitly
  against this milestone's "do not strip ordinary content" instruction) and would overclaim a
  general data-loss-prevention capability this application does not have. The three narrow,
  high-confidence patterns in Decision 5 are the smaller, defensible choice.
- **Authentication/RBAC as part of this milestone.** Rejected: the rubric's own multi-tenancy/
  auditability section (§H) and Critical Gate G4 (tenant isolation) are explicitly a later,
  separate concern (Release 3), and no user/tenant data model exists yet to authenticate or
  authorize against. Adding it here would be a larger architectural decision than "guardrails for
  the AI stage."
- **A security dashboard or external moderation service.** Rejected: no rubric point or Critical
  Gate calls for either; the existing evaluation report (extended with Section D) already gives
  visible, reviewable evidence without a new UI surface or a third-party dependency.
- **CI runs against a real provider for adversarial replay.** Rejected as a default: it would
  require a paid API key in CI and introduce real-provider nondeterminism into a gate that must
  stay deterministic and free. Real-provider adversarial replay remains available as a
  separately-authorized, manual exercise (see Revisit conditions), analogous to how Milestone
  2.5's retrieval-policy section replays frozen real-BGE scores rather than re-querying a live
  model.
- **Separate `INCR`/`EXPIRE` Redis calls for the rate limiter.** Rejected after a verification
  pass identified the gap (Decision 8a): not atomic, and a mid-sequence failure could leave a
  counter key with no TTL forever. A single atomic Lua script is the smallest correct fix; a
  dedicated rate-limiting library/dependency was not added since the existing `redis` client
  already implements this cleanly.
- **A second, dedicated database table for redaction-policy metadata.** Rejected: `RedactionPolicy`
  is code-owned and hash-pinned exactly like `PromptVersion` (ADR 0010), which also has no table --
  historical resolution works by looking up the recorded `policy_id`/`policy_version` in the
  in-process registry, not by querying a database. No migration was added or needed.

## Consequences

**Positive:** the highest-likelihood injection surface (the issue's own text) is now explicitly
framed as untrusted, matching what was already done for retrieved context; a fixed, deterministic
adversarial fixture exists and blocks CI on regression; a real (if narrow), *versioned and
historically-replayable* secret-redaction path exists with a full provenance trail; the rate
limiter's counter-and-expiry is atomic, so it cannot get stuck without a TTL; the API's two
state-changing endpoints are protected from accidental rapid-duplicate submission and from
unbounded free-text fields; every new invariant is provider-neutral, so mock and real-provider
paths cannot silently diverge.

**Negative/residual:** semantic (non-keyword) prompt injection is not, and cannot be, reliably
caught by a fixture built on literal strings -- this is a permanent limitation of pattern-based
detection, not something a larger fixture would fix. The three secret patterns are narrow by
design and will miss secret formats outside OpenAI/GitHub/PEM. The redaction guarantee covers only
provider-bound and model-influenced content, never the separately-persisted original
evidence/retrieved-evidence snapshot (see Decision 5's scope note) -- that snapshot is not a
DLP-protected store and was never claimed to be. Rate limiting is IP-keyed with no authentication,
so clients sharing one IP (e.g. behind a NAT) share one counter. Fail-open on Redis unavailability
means a sustained Redis outage removes rate-limiting protection entirely for its duration
(accepted, see Decision 9).

**Operational/maintenance:** any future prompt-template change requires a new hash-pinned
`PromptVersion` (enforced at import time by `PromptVersion.__post_init__`, unchanged since ADR
0010); any future secret-pattern addition should stay in the same narrow, high-confidence
category as the existing three, reviewed the same way a new pattern-matching rule would be.

## Security implications

Trust boundaries and residual risks are stated above (Decision 1) and in Section D's own
disclaimer. This ADR and this milestone's code/comments claim exactly, and only:

- recognized high-confidence secret patterns (three: OpenAI-style key, GitHub PAT, PEM private
  key) are redacted from provider-bound content (the rendered prompt, the outgoing provider
  payload) and from model-influenced persisted output (`AIResponse.narrative`/`.citations`);
- application-controlled credentials are structurally excluded from ever reaching an `AIRequest`
  field -- not filtered at runtime, but never placed there to begin with;
- prompt framing (the untrusted-data sections and boundaries in `triage_narrative@1.1.0`) reduces
  injection risk -- it does not, and cannot, guarantee immunity;
- Section D's deterministic adversarial checks validate this application's own guardrail
  boundaries (redaction, citation validation, output bounds, fallback wiring), run against the
  mock provider and a monkeypatched simulation of the OpenAI adapter's HTTP boundary;
- real-model semantic (non-keyword) prompt-injection resistance remains unproven by anything in
  this milestone and cannot be proven by a fixture built on literal strings;
- the original imported issue record, and the deterministic evidence/retrieved-evidence snapshot
  derived from it (`Recommendation.content["evidence"]`/`["retrieved_evidence"]`), is not a
  comprehensive DLP-protected store -- it retains original, unredacted text by design, exactly as
  `app.importer.sanitize`'s pre-existing policy already intended, distinct from the redacted
  provider-bound/model-generated content described above.

Explicitly not claimed: comprehensive prompt-injection detection, comprehensive DLP, that no
sensitive value is ever present anywhere in a `Recommendation`, or that passing every Section D
case proves a real language model will resist every injection technique against it.

## Testing/evidence

- `backend/tests/test_prompt_registry.py`: 1.0.0 remains registered/unchanged; 1.1.0 is active
  with a distinct hash; both untrusted sections are delimited; a forged closing marker cannot
  escape the boundary; the trusted template is never duplicated or displaced.
- `backend/tests/test_ai_gateway.py`, `backend/tests/test_redaction.py`: narrative-length bound;
  citation uniqueness/count-bound; redaction integration (mock and OpenAI paths); the narrow
  pattern set does not touch ordinary code/Markdown; `AIRequest` has no field that could carry an
  application secret; fallback preserves identical redaction provenance.
- `backend/tests/test_redaction_provenance.py`: a synthetic-secret replay reconstructs the exact
  historical `rendered_prompt_hash` from recorded, serialized provenance; introducing (and
  activating) a hypothetical newer `provider_input_redaction` version does not change replay of
  `provider_input_redaction@1.0.0`, with a negative control proving the test is not vacuous; the
  no-redaction path still records a resolvable policy identity.
- `backend/tests/test_evaluation_adversarial.py`, `backend/tests/evaluation/adversarial_cases.json`:
  all 15 Section D cases, run through the real production code paths.
- `backend/tests/test_rate_limit.py`: under-limit success; over-limit 429 with a safe, flat error
  body and `Retry-After`; independent route/client counters; GET polling unaffected; a rejected
  request creates no `Analysis` row; window reset; fail-open on a simulated Redis outage; the first
  increment creates a positive TTL; later increments within the same window do not reset it; a
  key manually forced into a no-TTL state is self-healed on the next call; a unit-level test on
  `_incr_and_ensure_ttl` proves the count+TTL are set together.
- `backend/tests/test_triage_api.py`, `backend/tests/test_decisions_api.py`: an oversized
  `ruleset_version`/`decision` value is rejected with a safe 422 (no stack trace, no internal
  representation); redaction-policy provenance is present in the live API response.
- `python -m app.evaluation`: Section D reported separately, with its own pass/fail count,
  redaction-event count, fallback-event count, and the robustness disclaimer; a demonstrated
  intentional regression (temporarily disabling the `openai_api_key` pattern) produced exit code
  1, identified by case ID and metric; restoring the pattern produced a clean exit 0.
- CI (`ci.yml`): a `redis:7-alpine` service was added alongside the existing pgvector `postgres`
  service so the rate-limit integration tests run (rather than skip) in CI, matching how the
  pgvector image was added for retrieval in Milestone 2.3.

## Revisit conditions

- If the narrative/citation format needs machine-parsed structure beyond a flat narrative and
  citation list, reconsider a structured-output schema for the OpenAI adapter specifically
  (Alternative 1), without breaking the provider-neutral `AIRequest`/`AIResponse` contract.
- If a real-provider adversarial replay is ever wanted (to measure actual model robustness rather
  than this application's own guardrail code), that must be a separately authorized, manual
  exercise with a real, budgeted API key -- never folded into the default, free, deterministic CI
  gate.
- If authentication/authorization is introduced (Release 3 / multi-tenancy), revisit whether
  IP-keyed rate limiting should become user- or tenant-keyed instead.
- If a new high-confidence, well-known secret format becomes common enough to justify inclusion,
  register it as a new, hash-pinned `RedactionPolicy` version (e.g. `provider_input_redaction@1.1.0`)
  -- never edit `1.0.0`'s pattern definitions in place, and never broaden the existing patterns to
  a generic `KEY=value` match.
