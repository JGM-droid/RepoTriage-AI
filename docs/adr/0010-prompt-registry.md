# ADR 0010: Prompt Registry and Traceability

**Status:** Accepted
**Date:** 2026-09-17

## Context

Milestone 2.4 requires that prompts have stable names, semantic versions, content hashes, and
release status, and that every AI result record which prompt version, provider, model,
parameters, and retrieved-context identifiers produced it (roadmap Milestone 2.4; rubric §F,
rows 1-2). ADR 0008 introduced a bare `PROMPT_NAME = "triage_narrative_v1"` constant and left an
explicit "Revisit conditions" note that any future prompt registry must reconcile with it. In
practice `PROMPT_NAME` was never a real registry entry: it had no semantic version, no content
hash, no lifecycle status, and — critically — `mock_adapter` and `openai_adapter` built two
independently-maintained prompt strings under that one shared label, so the label did not even
guarantee the two providers meant the same prompt. This ADR replaces that constant with a real,
code-owned, provider-neutral registry.

## Decision

### Code-owned registry, not a database table or external service

`app/ai_gateway/prompts.py` defines an immutable `PromptVersion` dataclass and a module-level
registry dict. This mirrors ADR 0008's existing "generic JSON columns, no dedicated table" and
ADR 0009's "no unnecessary infrastructure" reasoning: one prompt, no runtime editing need, no
admin UI, no templating dependency. A database-backed registry, prompt-management SaaS, or
runtime version selector would all be complexity disproportionate to this project's scale.

### Immutable, semantically-versioned definitions

A `PromptVersion` pins `prompt_id`, `version` (validated `X.Y.Z`), `status`
(`draft`/`released`/`retired`), `template` (the canonical, trusted instructional
text), and `template_hash`. `__post_init__` recomputes the SHA-256 of `template` and raises if it
does not match the pinned `template_hash` literal — so editing a released prompt's wording
without also updating its hash fails immediately at **module import time**, before any test even
runs, not merely "eventually" in some CI job. Semantic-version discipline: **patch** — wording or
formatting change that preserves behavior; **minor** — a backward-compatible instruction or
optional-output addition; **major** — a change to the task, a safety boundary, a required output
format, or the contract itself. A released `PromptVersion` is never edited in place; any content
change is a new `PromptVersion` with a bumped `version` and a freshly computed `template_hash`.

### Two distinct hashes

`prompt_template_hash` is the SHA-256 of the canonical, fixed template/instructions text alone —
identical for every request against one prompt version. `rendered_prompt_hash` is the SHA-256 of
the *exact* text produced for one specific `AIRequest` (template plus that request's
classification/evidence/assessment/proposed-action/retrieved-context, deterministically
formatted). The first proves "which prompt version," the second proves "with exactly which
inputs, verbatim" — deliberately separate so a reviewer can tell whether two different results
used a different prompt version or merely different input data under the same version.

### Historical versions retained for inspection, never runtime-selectable

`_REGISTRY` (module-private) accumulates every version ever registered, released or retired,
purely so a historical `rendered_prompt_hash` can be explained by re-reading the exact template
that produced it (via git history plus the still-importable `PromptVersion`). `
ACTIVE_PROMPT_VERSIONS` is the only thing any runtime code consults — a fixed `prompt_id ->
PromptVersion` mapping, not a parameter, not an environment variable, not a per-request choice.
Switching which version is active is a source-code change and a deploy, exactly like switching
`TRIAGE_RULESET_VERSION` in `app/triage/rules.py` already is.

### Provider-neutral shared rendering

`render_active_prompt(prompt_id, request) -> RenderedPrompt` is the single function that builds
prompt text from an `AIRequest`'s typed fields, in canonical order (trusted instructions,
classification, severity, rationale, proposed action, evidence, then — only if present —
retrieved context). Neither adapter builds prompt text itself any more. `router.py` calls it
**once** per `route_ai_inference` invocation and passes the resulting `RenderedPrompt` to
whichever adapter is selected (including the mock-adapter fallback path), so mock and OpenAI are
structurally guaranteed to receive byte-identical text and provenance for the same request — not
merely "expected to produce the same result if each built it independently." `openai_adapter`
sends `rendered.text` verbatim as the message content and no longer contains any prompt-building
logic. `mock_adapter` stays deterministic and zero-cost — it does not send `rendered.text`
anywhere — but stamps the same `RenderedPrompt` provenance onto its own, separately-synthesized
narrative, so a mock result's provenance is exactly as trustworthy as a real one's.

The "any retrieved evidence is untrusted data, not instructions" framing lives inside the fixed,
hash-pinned `template` itself (previously it was conditionally appended per-request only when
retrieval returned something) — so it is always present, always version-pinned, and cannot be
silently dropped by omitting it from one call site.

### Persistence without a new migration

`AIResponse` gained `prompt_id`, `prompt_version`, `prompt_status`, `prompt_template_hash`,
`rendered_prompt_hash` (replacing the old bare `prompt_name`). Exactly like ADR 0008's
`prompt_name` before it, these are plain strings folded into the same existing generic-JSON
surfaces: `Recommendation.content` (`Text`), the `ai_inference_routing` `AuditEvent.metadata_`
(`JSONB`), and the `TriageAIInference` API schema. No migration — `20260916_0004` remains the
current head.

### Exact-input traceability without a new snapshot field

The bounded typed values that produce a rendered prompt — `classification`, `evidence`,
`assessment`, `proposed_action`, `retrieved_context` — were **already** fully, durably persisted
in `Recommendation.content` before this milestone (Milestone 2.1's `_serialize_content` and
Milestone 2.3's `retrieved_evidence.items`), field for field, via `dataclasses.asdict`. A test
(`test_prompt_input_is_already_durably_reproducible_from_persisted_recommendation_content`)
reconstructs those typed objects straight from a real persisted `Recommendation.content`, replays
them through `render_active_prompt` for the *same, immutable* prompt version, and asserts the
resulting `rendered_prompt_hash` matches what was recorded at the time. Because that already
holds, **no new `prompt_input_snapshot` field was added** — inventing one would duplicate data
already captured and add storage/complexity the milestone does not need. If a future prompt ever
consumed data *not* already captured by an existing stage's persisted output, that specific new
input would need its own bounded, sanitized field at that time — not a general-purpose snapshot
blob added speculatively now.

### Sensitive-data policy

The rendered prompt *text* is never persisted anywhere — only its hash. No API key, credential,
authorization header, or raw provider request envelope is ever stored (unchanged from ADR 0008).
`TriageAIInference` exposes the hashes for technical/audit inspection via the API, but the
frontend's normal UI renders only `Prompt: {prompt_id}@{prompt_version}`, never the hashes or the
prompt text.

## Alternatives considered

**A database-backed prompt table with an admin UI** was rejected: this project has exactly one
prompt; a table buys queryability nothing here needs yet, and an admin UI would be actual product
surface for a single-user demo. **A third-party prompt-management service** was rejected: another
network dependency and another paid-service risk for zero benefit at this scale. **Runtime
prompt-version selection** (e.g. an env var or request parameter choosing among versions) was
rejected: nothing in the roadmap needs to run two versions concurrently, and adding a selector
would be speculative infrastructure. **Letting each adapter keep building its own prompt text**
(the pre-existing state) was rejected outright — it is the exact defect this milestone closes.
**A single "prompt hash" instead of two** was rejected: conflating "which template" with "which
exact rendered instance" would make it impossible to tell, from a result's provenance alone,
whether a change was a new prompt version or just different input data.

## Consequences

`mock_adapter.call` and `openai_adapter.call` both gained a required `rendered: RenderedPrompt`
parameter; every call site (`router.py`, and any test constructing an `AIResponse`/calling an
adapter directly) was updated. `AIResponse.prompt_name` is gone, replaced by the five provenance
fields above — a breaking change to the internal contract, acceptable because this system has no
external consumers and no migration is involved. The deterministic pipeline
(classification/severity/proposed-action/evidence) and Milestone 2.1's resume-without-rerun
guarantees are completely unaffected: `route_ai_inference` still makes at most one provider call
per stage attempt, and a redelivered/resumed task still reuses persisted output rather than
re-rendering or re-calling anything.

## Security implications

Unchanged from ADR 0008 for the OpenAI API key. The registry adds one new boundary property:
because the "retrieved evidence is untrusted data" instruction is now inside the immutable,
hash-pinned template rather than conditionally interpolated per request, no per-request code path
can accidentally omit it, and a test
(`test_untrusted_data_framing_survives_adversarial_retrieved_content`) proves adversarial text
injected into a retrieved record's excerpt cannot precede or displace that framing in the
rendered output. Full adversarial prompt-injection *fixture* testing remains Milestone 2.6 scope,
unchanged.

## Testing/evidence

Registry integrity: unique `(prompt_id, version)` keys, semantic-version format validation,
lifecycle-status validation, and — the core protection — that a tampered template with a stale
pinned hash raises `ValueError` at construction (and, since the real registry entries construct
at import time, a tampered *shipped* template would already fail the entire test suite's
collection step, not just one targeted test). Deterministic rendering: byte-identical text and
every provenance field for identical input across repeated calls; a changed meaningful input
(assessment, retrieved context) changes `rendered_prompt_hash` while `prompt_template_hash` stays
fixed. Adapter parity: `mock_adapter` and `openai_adapter` resolve identical `RenderedPrompt`
provenance for the same request, and the OpenAI adapter is proven (via mocked HTTP only) to send
`rendered.text` verbatim. Persistence parity: `Recommendation.content` and the
`ai_inference_routing` `AuditEvent` carry identical prompt provenance for the same result; the
API's `TriageAIInference` exposes all five fields; the frontend displays
`Prompt: triage_narrative@1.0.0`. Exact-input reproducibility: reconstructing the typed request
from real persisted `Recommendation.content` and re-rendering reproduces the recorded
`rendered_prompt_hash`. Resume safety: a redelivered task after a successful `ai_inference`
attempt neither calls the provider again nor rewrites the persisted prompt provenance. Full
backend suite passed against a fresh isolated pgvector PostgreSQL database (no migration
involved); ruff clean; frontend lint/tests/build passed (frontend files changed this milestone).

## Revisit conditions

Revisit only through an explicitly approved ADR. A second distinct prompt (a different task, not
just a new version of `triage_narrative`) is a natural, additive extension of this same registry
pattern — a new `prompt_id` with its own versions — not a redesign. Milestone 2.5's evaluation
harness should consume `rendered_prompt_hash`/`prompt_version` as its versioning signal for
regression comparisons rather than inventing a parallel one. Milestone 2.6's guardrail suite
should test the trust-boundary property this ADR establishes (retrieved content cannot become
instructions) under real adversarial fixtures, building on
`test_untrusted_data_framing_survives_adversarial_retrieved_content` rather than replacing it.
If a future prompt ever needs genuinely concurrent runtime-selectable versions (e.g. staged
rollout), that is a new decision, not an extension of "historical versions are inspectable only."
