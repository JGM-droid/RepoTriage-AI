# RepoTriage AI — Execution Roadmap

**Version:** 1.0
**Status:** Approved starting roadmap
**Project owner:** Jesse Montemayor
**Purpose:** Keep the project aligned to a verified business problem, prevent feature drift, and produce credible portfolio evidence for applied AI, backend, solutions, implementation, and consulting roles.

## 1. Authority and working rules

This roadmap controls build order and day-to-day execution. The Portfolio Grading Rubric controls scoring and the final definition of portfolio readiness.

When instructions conflict, use this order:

1. Portfolio Grading Rubric and Critical Gates
2. This execution roadmap
3. Accepted architecture decision records (ADRs)
4. Current milestone plan
5. Optional ideas and later enhancements

No feature enters the active build unless it:

- earns a rubric point;
- passes a Critical Gate; or
- fixes a verified defect blocking the current release.

Everything else goes into `docs/FUTURE_IDEAS.md`.

## 2. Verified business case

### Problem

Growing software companies, developer-tool teams, and open-source maintainers receive duplicate, incomplete, incorrectly categorized, repetitive, or misrouted GitHub issues. Senior engineers and maintainers must repeatedly read each report, search documentation and resolved issues, estimate priority, determine whether it is actionable, and prepare a response.

### Target user

A small-to-mid-sized B2B software or developer-tools organization with multiple repositories, a limited support/engineering team, and more incoming issues than senior engineers can efficiently investigate.

Primary users:

- support engineers;
- developer-relations teams;
- engineering managers;
- QA leads;
- product operations teams; and
- open-source maintainers.

### Product promise

RepoTriage AI analyzes imported GitHub issues, retrieves relevant repository evidence, and proposes an auditable triage recommendation while preserving human control over every final decision.

### Differentiation

RepoTriage AI is not merely an automatic labeler or generic RAG chatbot. It demonstrates:

- evidence-backed recommendations with source links;
- explicit separation of evidence, AI inference, proposed action, and human decision;
- mandatory human review before final action;
- multi-repository and multi-tenant isolation;
- provider-neutral model routing and controlled fallback;
- prompt, model, retrieval, cost, and latency traceability;
- evaluation and regression blocking; and
- append-only auditability.

### Claim boundary

The project may demonstrate production-oriented architecture and tested behavior. It must not claim proven enterprise scale, real-customer ROI, principal/staff tenure, or operation under significant customer load.

## 3. Portfolio outcome

The completed project should support this honest statement:

> Researched a documented issue-triage problem and designed and built a production-oriented, multi-tenant AI issue-operations platform that classifies GitHub issues, retrieves repository evidence, proposes grounded responses, enforces human approval, evaluates quality, records cost and traces, and deploys through a reproducible containerized cloud workflow.

Portfolio ready means:

- weighted rubric score is at least 90;
- all 12 Critical Gates pass;
- the final repository is reproducible from a fresh clone;
- implementation, automated verification, documentation, and visible evidence agree; and
- Jesse passes the ownership walkthrough.

## 4. System boundaries

### In scope

- Bounded import of approximately 100–300 public GitHub issues
- Cached fixture data for repeatable and offline demonstrations
- React and TypeScript interface
- FastAPI and versioned API contracts
- PostgreSQL and pgvector
- Deterministic mock/local analysis path
- At least one real AI provider
- Provider-neutral contracts, routing, usage, cost, and fallback
- Staged asynchronous workflow
- RAG over repository documentation and resolved issues
- Prompt registry and evaluation suite
- Guardrails, role permissions, tenant isolation, and audit events
- OpenTelemetry-based traces and operational reporting
- Docker Compose, CI, cloud deployment, and rollback/redeploy instructions

### Out of scope until the portfolio is approved

- Automatically commenting on or modifying third-party repositories
- Autonomous code changes or pull requests
- Kubernetes and Helm
- MCP implementation
- Mobile or native desktop applications
- Training or fine-tuning a foundation model
- Multiple paid AI subscriptions
- Enterprise SSO/SAML
- Billing and subscriptions
- Real-time collaboration
- Large-scale load testing
- Extensive animation or visual polish

## 5. Technical direction

Initial architecture:

- **Frontend:** React + TypeScript with typed API contracts
- **Backend:** FastAPI + Pydantic
- **Persistence:** PostgreSQL, SQLAlchemy, and Alembic
- **Vector retrieval:** pgvector
- **Async execution:** a durable worker/workflow abstraction selected through an ADR
- **AI gateway:** provider-neutral adapter with real and deterministic implementations
- **Observability:** structured logs, correlation IDs, and OpenTelemetry
- **Local runtime:** Docker Compose
- **CI:** GitHub Actions
- **Cloud:** one economical public deployment target selected through an ADR

Frameworks and vendors are not credited merely for appearing in dependencies. Every important technology requires working behavior, verification, documentation, and demonstrable evidence.

## 6. Release roadmap

## Release 0 — Foundation and repository bootstrap

**Goal:** Establish the project controls and a clean, runnable skeleton before feature work.

### Milestone 0.1 — Project controls

**Status:** Complete — approved by Jesse and the lead architect.

Deliverables:

- `docs/PORTFOLIO_RUBRIC.md`
- `docs/EXECUTION_ROADMAP.md`
- `docs/REQUIREMENT_TRACEABILITY.md`
- `docs/BUSINESS_CASE.md`
- `docs/FUTURE_IDEAS.md`
- `docs/adr/` with an ADR template
- `AGENTS.md` with project authority, boundaries, and verification rules

Acceptance:

- Every Release 1 requirement maps to planned implementation, test, documentation, and demo evidence.
- Business claims distinguish sourced facts, retrospective evaluation goals, and unproven assumptions.
- Out-of-scope features are explicitly deferred.

Learning checkpoint:

- Jesse can explain the customer, problem, product boundary, intended business outcome, and why the system requires human review.

### Milestone 0.2 — Runnable skeleton

**Status:** Complete — approved after successful GitHub Actions verification for commit `dbca8ba`.

Deliverables:

- React/TypeScript frontend
- FastAPI backend with `/api/v1/health` and `/api/v1/readiness`
- PostgreSQL service
- Initial Alembic migration setup
- Docker Compose development stack
- Formatting, linting, unit-test commands, and initial CI
- `.env.example` with no secrets

Acceptance:

- A fresh clone starts from documented commands.
- Frontend reaches the versioned backend health endpoint.
- Backend reaches PostgreSQL.
- Formatting, linting, tests, and secret scanning pass.

**Release 0 exit:** The empty product platform starts reproducibly and CI passes.

---

## Release 1 — Complete product foundation

**Goal:** Complete the main workflow without a paid AI call.

### Milestone 1.1 — Core data model

Build the initial schemas for repositories, issues, analyses, recommendations, human decisions, and audit events. Include identifiers that will allow tenants and users to be added safely in Release 3 without destructive redesign.

Acceptance:

- Migrations create and downgrade the schema.
- Schema constraints prevent invalid relationships and duplicate imports.
- API schemas and database models remain separate.
- Initial ER diagram matches the migration.

### Milestone 1.2 — Bounded issue importer

Build a reproducible importer for a selected public repository plus a cached 100–300 issue fixture.

Acceptance:

- Import is bounded, repeatable, sanitized, provenance-tracked, and idempotent.
- Demonstration can run without live GitHub access.
- Tests cover limits, malformed content, duplicate imports, and sanitization.

### Milestone 1.3 — Issue browsing experience

Build the issue list and issue detail views with typed API contracts and clear loading, error, and empty states.

Acceptance:

- User can browse imported issues and open one issue.
- Frontend and API tests cover primary and failure states.
- Important imported metadata and provenance are visible.

### Milestone 1.4 — Deterministic triage workflow

Implement the explicit stages `classify → retrieve fixture evidence → assess → propose → human review` using deterministic logic and fixtures.

Acceptance:

- Same input and configuration produce the same result.
- Workflow status exposes queued, running, completed, and failed states even before real AI is introduced.
- UI distinguishes evidence, system inference, and proposed action.

### Milestone 1.5 — Human decision gate

Allow an authorized local reviewer to approve, reject, or request revision. No decision may be inferred from model output.

Acceptance:

- Recommendation remains proposed until an explicit human decision.
- Approval, rejection, and revision are recorded.
- Tests prove that analysis completion cannot silently produce final approval.

### Milestone 1.6 — Release 1 verification

Acceptance:

- Fresh local run completes import → browse → analyze → review → decision.
- Main workflow requires no paid provider.
- Basic CI and documented acceptance test pass.
- Release 1 rubric evidence is captured.
- Jesse completes the Release 1 ownership review.

**Release 1 exit:** A fresh local run completes the primary workflow without a paid AI call.

---

## Release 2 — AI architecture

**Goal:** Add grounded AI behavior, reliable execution, evaluation, regression protection, and security controls.

### Milestone 2.1 — Durable asynchronous workflow

**Status:** Complete — approved by Jesse.

- Stage boundaries and persisted status
- Retry and timeout policy
- Idempotency keys
- Recoverable failures and resumability
- Forced-failure tests

### Milestone 2.2 — Provider-neutral AI gateway

**Status:** Complete — approved by Jesse.

- Neutral request, response, usage, and error contracts
- Deterministic mock/local adapter
- One real provider adapter
- Explainable routing decision
- Controlled provider fallback
- Token and estimated-cost recording

### Milestone 2.3 — Repository-grounded RAG

**Status:** Complete — approved by Jesse.

- Ingest repository documentation and resolved issues
- Chunk and embed content in pgvector
- Enforce repository and tenant scope
- Return stable metadata and source links
- Bound context size and exclude unauthorized material
- Require recommendations to cite retrieved evidence

### Milestone 2.4 — Prompt registry and traceability

**Status:** Complete — approved by Jesse.

- Stable prompt names
- Semantic versions and content hashes
- Draft/released/retired status
- Record prompt, model, parameters, provider, and context identifiers with every result

### Milestone 2.5 — Evaluation and regression harness

**Status:** Complete — approved by Jesse.

Evaluation set covers:

- classification;
- retrieval hit rate;
- groundedness;
- citation correctness;
- usefulness;
- structured-output validity; and
- safety.

Acceptance:

- One command produces machine-readable and human-readable reports.
- Documented thresholds block a release.
- An intentionally degraded prompt, model, or retriever demonstrates a failing regression gate.
- Documentation uses “behavior regression,” not population drift, unless real production distributions exist.

### Milestone 2.6 — Guardrails and AI security

**Status:** Complete — approved by Jesse.

- Treat issue and retrieved text as untrusted data
- Prompt-injection fixtures
- Structured-output validation and fail-closed behavior
- Tool/action allowlist
- API limits, safe errors, rate limits, and secrets scanning

### Milestone 2.7 — Release 2 verification

**Status:** Complete — approved by Jesse.

**Release 2 exit:** Evaluation thresholds pass, a provider failure recovers safely, an injection attempt fails safely, and human approval remains mandatory.

**Release 2 status:** Complete — approved by Jesse. All four exit criteria demonstrated together,
through the real production code paths, in one focused acceptance file plus a live browser
ownership run (see Current Project State and [R2-07](REQUIREMENT_TRACEABILITY.md) for full
evidence).

---

## Release 3 — Enterprise hardening

**Goal:** Demonstrate secure multi-tenant application design, auditability, observability, containers, and cloud delivery.

### Milestone 3.1 — Multi-tenancy and roles

- At least two organizations
- Viewer, reviewer, and administrator roles
- Backend-enforced permissions
- Cross-tenant negative tests covering records, vectors, analyses, and traces

### Milestone 3.2 — Append-only auditability

Capture imports, analyses, workflow transitions, routing decisions, approvals, rejections, edits, and administrative changes.

### Milestone 3.3 — End-to-end observability

- Correlation ID from frontend through API, workflow, retrieval, model call, cost, and audit event
- OpenTelemetry spans and errors
- Operational view for latency, failures, retries, token use, cost, and evaluation status
- Diagnostic runbook for one intentionally simulated failure

### Milestone 3.4 — Security hardening

- Authentication and authorization
- Input and rate limits
- Secure configuration and secrets
- Dependency and secret scanning
- Sanitized imported data
- Threat model and security test map

### Milestone 3.5 — Containers and cloud

- Frontend, API, worker, and PostgreSQL through documented containers
- Fresh-start smoke test
- Public cloud deployment
- Secrets outside source control
- Reproducible redeploy or rollback procedure

### Milestone 3.6 — Critical Gate audit

**Release 3 exit:** All Critical Gates pass and the weighted rubric score is at least 90.

---

## Release 4 — Portfolio packaging

**Goal:** Make the work understandable, verifiable, demonstrable, and defensible by someone who did not participate in the build.

Deliverables:

- Final README
- Architecture, ER, workflow, and threat-model diagrams
- Key ADRs
- Business-case and limitations documentation
- Saved test, evaluation, latency, cost, and security reports
- Three-to-five-minute demo showing primary workflow, provider fallback, and security denial
- Recruiter walkthrough
- Resume bullets and interview talking points
- Oral ownership review

**Release 4 exit:** A reviewer can understand, run, verify, and discuss the project without access to the build conversation.

## 7. Business and quality measurements

The project will measure rather than assume value.

| Question | Planned measurement |
|---|---|
| Can classification support triage? | Accuracy, precision, recall, and F1 against historical labels |
| Can retrieval find known material? | Hit rate/Recall@K on fixed resolved-issue and documentation queries |
| Can it identify likely duplicates? | Precision and Recall@K on known duplicate pairs when available |
| Are recommendations grounded? | Groundedness and citation-correctness evaluation |
| Are recommendations useful? | Approve/edit/reject rate during documented review exercises |
| Is execution reliable? | Failure, retry, fallback, and terminal-state rates in tests |
| Is the system economical? | Tokens and estimated cost per analysis |
| Is it responsive? | Stage and end-to-end latency percentiles in the demo environment |
| Is it secure? | Injection denial, unauthorized-action, and tenant-isolation test results |

No simulated or retrospective measurement will be represented as live customer ROI.

## 8. Required evidence for every milestone

A milestone is not complete because code was generated. Completion requires:

1. Working implementation
2. Automated test or repeatable verification
3. Documentation of the design decision
4. Visible evidence in the interface, trace, audit event, report, or demo
5. Jesse’s ownership checkpoint
6. Clean commit and current-status update

Each milestone handoff must report:

- what was implemented;
- which rubric items it satisfies;
- exact verification performed;
- what demonstrably works;
- what remains unproven;
- risks or deferred items; and
- the single recommended next milestone.

## 9. Ownership curriculum

For each major component, Jesse must be able to answer:

1. What problem does it solve?
2. Why was this design chosen over a simpler alternative?
3. What is one likely failure or security risk?
4. Which test proves the important behavior?
5. What is demonstrated, and what remains unproven at enterprise scale?

The project is not portfolio ready until these answers can be given without reading generated text.

## 10. Drift-control process

Before starting any new feature, record:

- rubric requirement or Critical Gate served;
- current release and milestone;
- smallest acceptable implementation;
- acceptance test;
- evidence artifact; and
- explicit exclusions.

If the feature does not serve the active milestone, defer it.

At the end of every work session, update the Current Project State below. Do not rely on chat history as the canonical status record.

## 11. Current Project State

**Current release:** Release 3 — Enterprise hardening
**Current milestone:** Milestone 3.1 — Multi-tenancy and roles (not started)
**Status:** Release 1 — Product foundation is complete. Milestones 2.1–2.5 are complete (Jesse
approved all five): a provider-neutral AI gateway with deterministic fallback (ADR 0008);
repository-grounded pgvector retrieval with a calibrated two-band confidence model, disclosed
#6139 polysemy limitation (ADR 0009); a code-owned, immutable, hash-pinned prompt registry (ADR
0010); and a deterministic, zero-cost, zero-network evaluation/regression harness with a
checked-in, reviewable baseline covering deterministic triage, retrieval-policy regression, and
narrative/citation behavior (ADR 0011). Milestone 2.6 — Guardrails and AI security is complete
(Jesse approved): [ADR 0012](adr/0012-ai-guardrails-and-adversarial-evaluation.md) treats the
current issue's own title/body and retrieved repository evidence as untrusted data, not
instructions — a new, hash-pinned `triage_narrative@1.1.0` prompt release frames both inside
explicit, app-controlled `BEGIN`/`END` boundaries (the previous `triage_narrative@1.0.0` framed
only retrieved evidence; it remains registered, unmodified, for historical inspection). This
framing is documented and must be understood as risk reduction, not proof of injection immunity —
a sufficiently capable adversarial model could still be misled by injected text, and nothing in
this milestone proves real-model semantic prompt-injection resistance. Provider-neutral output/
citation validation was extended: a 4,000-character narrative bound (`MAX_NARRATIVE_LENGTH`),
duplicate-citation rejection, and a citation-count-vs-retrieved-context-count check, all routing
through the existing deterministic mock-adapter fallback (ADR 0008) on any violation. A new,
versioned, hash-pinned redaction policy (`provider_input_redaction@1.0.0`, `app.ai_gateway.
redaction`) matches exactly three high-confidence, well-known secret formats (an OpenAI-style key,
a GitHub personal-access token, a PEM private-key block) and redacts them from the rendered
prompt, the outgoing provider payload, and model-influenced persisted narrative/citations —
never from the separately-persisted original evidence/retrieved-evidence snapshot, which remains
unredacted by design, exactly as `app.importer.sanitize`'s pre-existing import policy already
intended. The redaction policy's ID/version/status/hash and match events are persisted alongside
prompt provenance on every `AIResponse` (mock, real-provider, and fallback paths identically), and
a dedicated replay test proves a historical analysis's exact `rendered_prompt_hash` remains
reproducible even after a newer redaction-policy version becomes active. A new, versioned
adversarial-guardrail fixture (`backend/tests/evaluation/adversarial_cases.json`, 15 cases) forms
Section D of `python -m app.evaluation`, covering prompt injection, fabricated citations,
malformed/empty/oversized provider output, secret redaction, resource exhaustion, the
human-review boundary, and the absence of tool/action execution — every case deterministic and
blocking, run through the real production code paths; the section states explicitly that these
checks validate application guardrails and do not prove any real model resists every
prompt-injection technique. A Redis-backed, atomically-incremented (single Lua script) fixed-window
rate limiter now guards the two state-changing endpoints (start triage, record a human decision;
every read-only GET endpoint is unaffected), returning HTTP 429 with `Retry-After` on a safe, flat
error body; Redis unavailability is a documented, deliberate fail-open tradeoff (rate limiting is
IP-keyed, with no authentication in this milestone). Two previously-unbounded state-changing
request fields (`TriageRequest.ruleset_version`, `TriageDecisionRequest.decision`) gained
conservative `max_length` bounds. Verified: 385/385 backend tests passed against isolated
PostgreSQL and Redis (the live demo stack — 100 issues, 405 retrieval chunks, migration head
`20260916_0004`, all five services healthy, `AI_PROVIDER=mock`/`EMBEDDING_PROVIDER=local` — was
never touched); ruff format/lint clean; `git diff --check` clean; a demonstrated intentional
guardrail regression (redaction bypassed) produced exit code 1, naming the exact failing case and
metric; the restored, clean evaluation run produced exit code 0 with the checked-in baseline
byte-identical throughout. No real, paid OpenAI call was made at any point. Jesse personally ran
`python -m app.evaluation` with `AI_PROVIDER=mock`/`EMBEDDING_PROVIDER=fake` and confirmed: Section
D adversarial guardrails 15/15 passed; prompt version `triage_narrative@1.1.0`; the
secret-redaction case passed; a fabricated citation safely triggered fallback; malformed, empty,
oversized, and timeout provider cases safely triggered fallback; prompt and redaction provenance
survived fallback; no `HumanDecision` was created; no tool/action execution occurred; baseline
comparison PASS; exit code 0. Jesse reviewed this run and explicitly approved Milestone 2.6 as
complete.

Milestone 2.7 — Release 2 verification is complete (Jesse approved), and with it, **Release 2 —
AI architecture is complete**. A new acceptance file (`backend/tests/test_release2_acceptance.py`,
11 tests) proves the roadmap's own Release 2 exit criteria hold together through the real
production code paths in one run: Section A calls the real `python -m app.evaluation` entry point
and confirms Sections A/B/C/D all pass with no blocking regression, exit code 0, and baseline
comparison PASS; Section B drives a real, simulated OpenAI timeout through the actual API ->
Celery-eager workflow -> router -> adapter path (`httpx.post` monkeypatched before any request is
built, never a real network call) and confirms the analysis completes with AI-inference status
`fallback`, a bounded reason, and full prompt/redaction provenance intact; Section C proves
current-issue and retrieved-context injection attempts stay data (never instructions), a
fabricated citation is rejected, a synthetic secret is redacted from provider-bound/model-generated
content while the original evidence snapshot legitimately remains unredacted by design, and no
`HumanDecision`/tool execution ever results — explicitly documented as proving *this application's*
guardrail boundaries under a deterministic/mock provider, not universal real-model
prompt-injection immunity; Section D proves the human-approval boundary end to end, including the
existing 409 conflict contract for a second, conflicting decision. An intentional, temporary
in-memory redaction bypass was demonstrated to fail the acceptance test with the exact predicted
assertion, then reverted with zero net source diff, proving the suite is meaningful rather than
permanently green. Full backend suite: 398/398 passed against isolated PostgreSQL/Redis (up from
385, after a CI-portability correction added two cross-platform hashing regression tests -- see
below); ruff format/lint clean; `git diff --check` clean; Compose configuration validated.

GitHub Actions initially failed on this work (6 failures, `BaselineIncompatibleError:
adversarial_fixture_hash differs from the checked-in baseline`) even though the same evaluation run
passed locally on Windows. Root cause, confirmed with exact computed hashes rather than assumed:
`adversarial_cases.json` was authored via a Windows text-mode file write (introducing CRLF line
endings into the working-tree copy); Git's `core.autocrlf=true` normalized the *committed blob* to
LF without rewriting the working tree, so the checked-in baseline's fixture hash (computed from the
Windows CRLF bytes) never matched what a Linux CI checkout of the same, semantically-unchanged blob
(LF) computed. The fix is a new `app.evaluation.canonical.canonical_json_hash`, applied consistently
to both fixture fingerprints (`fixed_cases.json` and `adversarial_cases.json`): it hashes a
canonical re-serialization of the *parsed* JSON, never raw file bytes, so line endings, indentation,
and key order can never again change the hash while any real content change still does. The
resulting baseline diff was exactly the two hash values -- no metric, case count, or expected
outcome changed. GitHub Actions is green for the corrected commit
(`997eddea7cdc9dfac3f99776536cfcdc3d33a964`).

Jesse's first live browser ownership run showed `Prompt: triage_narrative@1.0.0` instead of the
expected `1.1.0`. Read-only diagnosis (no source change) found the cause: the running `api`/`worker`
Docker containers were built from an image over a day older than the Milestone 2.6 commit that
introduced `1.1.0`, and had never been rebuilt since. Only the `api` and `worker` services were
rebuilt and recreated from current source; PostgreSQL, Redis, and the frontend were never touched.
Both containers then confirmed active prompt `triage_narrative@1.1.0` and active redaction policy
`provider_input_redaction@1.0.0`. Jesse's subsequent live run confirmed: `Provider: mock
(deterministic-v1)`; `Prompt: triage_narrative@1.1.0`; repository-grounded citations; deterministic
evidence displayed separately from the AI-generated inference; no automatic `HumanDecision`; and
working Approve/Reject/Request-revision controls. Demo integrity throughout every step of this
milestone: exactly 100 issues, 405 retrieval chunks, migration head `20260916_0004`, all five
services healthy, `AI_PROVIDER=mock`/`EMBEDDING_PROVIDER=local`, and zero paid provider calls.

Accepted, disclosed limitations carried forward (not claimed solved): deterministic/mock
adversarial tests prove this application's own guardrail boundary behavior, not universal
real-model prompt-injection immunity; redaction covers only the three documented high-confidence
secret patterns; the original imported evidence snapshot remains unredacted by design (a
deliberate scope boundary, not an oversight); the retrieval-policy evaluation remains a small,
frozen sample and retains the accepted #6139 polysemy limitation; rate limiting is IP-keyed and
fails open if Redis is unavailable. No Release 3 capability (multi-tenancy, roles,
backend-enforced permissions, append-only auditability, observability, security hardening,
containerized cloud delivery) has been implemented yet.

**Last approved decision:** Milestone 2.7 — Release 2 verification, and Release 2 — AI architecture
as a whole (Release 2 exit criteria demonstrated together via
`backend/tests/test_release2_acceptance.py`; CI-portability defect diagnosed and corrected via
canonical fixture hashing; stale `api`/`worker` Docker images diagnosed and refreshed) approved
complete by Jesse.
**Next action:** Begin Milestone 3.1 — Multi-tenancy and roles.
**Blockers:** None identified.

**Ownership follow-up:** Review remaining technical ownership topics when their corresponding components are implemented.

## 12. Immediate first build sequence

1. Complete — Create and open the local `repotriage-ai` repository and initialize Git.
2. Complete — Add the controlling documents for Milestone 0.1.
3. Complete — Create the initial Release 1 requirement traceability matrix.
4. Complete — Create ADRs for the stack, fixture-data policy, human-review boundary, and asynchronous-workflow decision timing.
5. Complete — Verify documentation consistency and obtain Milestone 0.1 ownership approval.
6. Plan, but do not begin implementation of, Milestone 0.2 until its scope is approved.

Milestone 0.2 planning must preserve the project controls and must not claim any application functionality is complete.
