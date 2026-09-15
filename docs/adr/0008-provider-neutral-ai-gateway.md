# ADR 0008: Provider-Neutral AI Gateway

**Status:** Accepted
**Date:** 2026-09-15

## Context

Milestone 2.2 requires a provider-neutral AI gateway: neutral request/response/usage/error
contracts, a deterministic mock/local adapter, one real provider adapter, an explainable
routing decision, controlled fallback on provider failure, and token/estimated-cost recording
(roadmap Milestone 2.2; rubric §D). The gateway must supplement the existing deterministic,
Celery-durable triage workflow (ADR 0007) without becoming a second execution system, and it
must never weaken the mandatory human-review boundary (ADR 0003): AI output is always
proposed, never a silently approved action.

## Decision

Add `app/ai_gateway/`, a small provider-neutral package, and wire it into the existing
workflow as one additional stage, `ai_inference`, running after the deterministic `propose`
stage and before `human_review`:

`classify → retrieve_fixture_evidence → assess → propose → ai_inference → human_review`

- **Contracts** (`contracts.py`): a validated `AIRequest` (task, classification, evidence,
  assessment, proposed action — nothing else) and a validated `AIResponse` (narrative, status,
  provider, model, prompt name, input/output tokens, estimated cost, latency, fallback reason).
- **Adapters**: `mock_adapter.py` is deterministic and makes zero network calls — the default
  for development, the automated test suite, and the normal demo. `openai_adapter.py` is the
  single real provider, built directly on the project's existing `httpx` dependency (promoted
  from a dev-only to a production dependency; no new dependency added) against OpenAI's Chat
  Completions REST endpoint — not the official `openai` SDK, which this project's needs (one
  JSON POST) do not warrant.
- **Router** (`router.py`): selects the adapter named by `AI_PROVIDER` (default `mock`). A
  controlled OpenAI failure (timeout, HTTP error, malformed/missing content) falls back to the
  mock adapter **within the same stage attempt**, recording why (`fallback_reason`); this never
  spends an additional Celery-level retry on a provider failure. Invalid *configuration*
  (`AI_PROVIDER=openai` with no `OPENAI_API_KEY`) is checked separately
  (`ensure_ai_gateway_configured`), raises `AIGatewayConfigurationError`, and is never turned
  into a fallback — it is called at worker/API process startup so a misconfigured deployment
  fails immediately and visibly, not on the first task.
- **Workflow integration**: `ai_inference` runs through the existing `_run_stage` persistence
  path unchanged, so it inherits, for free, everything Milestone 2.1 already built: persisted
  per-stage output in `StageAttempt.output`, the database-level uniqueness constraint, and
  resume-without-rerun on redelivery — which is also the mechanism that guarantees a paid call
  is never repeated for an attempt that already succeeded. The narrative and its provenance are
  also folded into `Recommendation.content` (alongside classification/evidence/assessment/
  proposed_action/human_review), and a routing/fallback decision is recorded as a new
  `AuditEvent` (`event_type="ai_inference_routing"`), written only when the stage genuinely
  executed this attempt (never duplicated on resume).
- **No database migration.** `stage_attempts.output` (Milestone 2.1) and `AuditEvent.metadata_`
  are already generic JSON-capable columns; `Recommendation.content` is already an arbitrary
  JSON blob. All three already represent this milestone's data adequately.
- **Cost controls**: only the already-gathered, already-sanitized evidence is sent (no new
  retrieval, no raw issue dump, no repeated context); at most one provider call per stage
  attempt; estimated cost is computed from explicit, configurable
  `OPENAI_INPUT_PRICE_PER_MILLION_USD` / `OPENAI_OUTPUT_PRICE_PER_MILLION_USD` settings rather
  than a hardcoded price in business logic; mock usage/cost is deterministically zero; no
  `max_tokens` cap is set (the Chat Completions API does not require one, and omitting it
  avoids truncating a normal narrative response).

## Alternatives considered

**Replacing the deterministic `assess`/`propose` stages with AI-generated output** was rejected:
it would make the primary recommendation non-deterministic, break Release 1's paid-call-free
guarantee for the core workflow, and blur the evidence/inference/decision separation the
product promise depends on. AI stays strictly additive.

**The official `openai` Python SDK** was rejected in favor of the existing `httpx` dependency:
the integration surface needed here is one JSON POST with a bearer token, well within what
`httpx` already handles, and the project already depends on it (previously dev-only, for
`TestClient`).

**Falling back via a full Celery retry** (raising like any other stage failure, so
`self.retry()` re-enqueues the whole task) was rejected: a provider failure has a cheap, instant,
deterministic alternative (the mock adapter) available immediately — spending a retry cycle
(with its countdown) on it would be slower and would not change the outcome.

**A dedicated table for AI provenance/usage** was rejected: `stage_attempts.output` and
`AuditEvent.metadata_` are already generic enough, and Milestone 2.2 does not need indexed
cross-analysis queries over provider/cost (that would be a Milestone 3.3 observability concern,
if ever needed).

## Consequences

The deterministic pipeline (classification, severity, proposed action, evidence) is completely
unchanged and remains independently correct and testable. The demo and CI remain free and
offline by default (`AI_PROVIDER=mock`). Real-provider use is strictly opt-in and requires
explicit configuration. A new stage extends `STAGE_ORDER`, so any code that assumed exactly
five stages (none found outside the workflow module and its tests) would need updating; all
affected tests were updated. `httpx` is now a runtime dependency, not just a dev one.

## Security implications

The `OPENAI_API_KEY` is read from settings for exactly one outbound request and is never logged
or persisted; it is never included in any exception message, audit record, or stored
`StageAttempt`/`Recommendation` content. Issue and evidence text sent to a provider is the same
already-sanitized content established by the importer (Milestone 1.2) — no new untrusted-data
exposure is introduced. The AI narrative can never itself become an approved action or a
`HumanDecision`; ADR 0003's boundary is untouched.

## Testing/evidence

Contract validation (rejects malformed `AIRequest`/`AIResponse`); mock-adapter determinism and
zero usage/cost; OpenAI adapter success (with token usage and configurable cost calculation),
timeout, HTTP error, and malformed/missing-response cases — all via mocked HTTP responses only,
no real network call; router provider selection, in-stage fallback with recorded reason, and
the configuration-error-is-never-a-fallback distinction; workflow-level tests proving the
stage's persisted output and routing `AuditEvent`, that a redelivered/resumed task never calls
the provider again once the stage has succeeded, that the workflow still completes when the
provider is unavailable, and that the existing per-stage-attempt uniqueness invariant,
single-recommendation guarantee, and zero-`HumanDecision` guarantee all still hold with the new
stage present. Frontend tests prove the AI section renders distinctly from the deterministic
sections and displays fallback/provenance correctly, and that existing polling-cap/Refresh
behavior is unaffected.

## Revisit conditions

Revisit only through an explicitly approved ADR. Any future prompt registry (Milestone 2.4)
must reconcile with the `prompt_name` field introduced here rather than replacing it ad hoc; any
future RAG grounding (Milestone 2.3) must continue sending only bounded, sanitized, already-
approved context through this same `AIRequest` contract.
