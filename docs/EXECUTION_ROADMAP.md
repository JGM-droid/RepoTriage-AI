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

- Stage boundaries and persisted status
- Retry and timeout policy
- Idempotency keys
- Recoverable failures and resumability
- Forced-failure tests

### Milestone 2.2 — Provider-neutral AI gateway

- Neutral request, response, usage, and error contracts
- Deterministic mock/local adapter
- One real provider adapter
- Explainable routing decision
- Controlled provider fallback
- Token and estimated-cost recording

### Milestone 2.3 — Repository-grounded RAG

- Ingest repository documentation and resolved issues
- Chunk and embed content in pgvector
- Enforce repository and tenant scope
- Return stable metadata and source links
- Bound context size and exclude unauthorized material
- Require recommendations to cite retrieved evidence

### Milestone 2.4 — Prompt registry and traceability

- Stable prompt names
- Semantic versions and content hashes
- Draft/released/retired status
- Record prompt, model, parameters, provider, and context identifiers with every result

### Milestone 2.5 — Evaluation and regression harness

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

- Treat issue and retrieved text as untrusted data
- Prompt-injection fixtures
- Structured-output validation and fail-closed behavior
- Tool/action allowlist
- API limits, safe errors, rate limits, and secrets scanning

### Milestone 2.7 — Release 2 verification

**Release 2 exit:** Evaluation thresholds pass, a provider failure recovers safely, an injection attempt fails safely, and human approval remains mandatory.

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

**Current release:** Release 1 — Product foundation
**Current milestone:** Milestone 1.2 — Bounded issue importer
**Status:** Milestone 0.1 project controls, Milestone 0.2 runnable skeleton, and Milestone 1.1 core data model are complete. Milestone 1.2 backend importer boundary, offline fixture, and tests are implemented and PR-pending review; Milestone 1.2 remains In Progress until Jesse approves it.
**Last approved decision:** Adopt governed, evidence-backed GitHub issue triage as the verified business case while retaining the approved portfolio rubric.
**Next action:** Review and approve the Milestone 1.2 pull request, then plan Milestone 1.3 — Issue browsing experience.
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
