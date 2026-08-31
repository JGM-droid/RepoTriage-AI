# RepoTriage AI — Portfolio Grading Rubric

**Version:** 1.0  

**Purpose:** Control scope, prevent feature drift, measure genuine learning, and determine when RepoTriage AI is ready to publish as a portfolio project.

## 1. Project definition

RepoTriage AI is a production-oriented application that imports a bounded sample of public GitHub issues, classifies and prioritizes them, retrieves relevant repository documentation and resolved issues, proposes a grounded response, and requires human review before any action is recorded.

The project must demonstrate project-level competence in:

- Agent orchestration

- Multi-model provider adapters

- Retrieval-augmented generation (RAG)

- Prompt versioning

- Automated LLM evaluation

- Guardrails

- Model/prompt/retrieval regression detection

- Asynchronous processing

- Multi-tenant application design

- Audit logging

- Role-based permissions

- Cloud deployment

- Secrets management

- Production-style observability

- React and TypeScript application architecture

- Containerization

## 2. Scoring policy

| Final score | Result |
|---:|---|

| 90–100 | Portfolio ready; strong evidence for junior AI/backend/solutions roles |

| 80–89 | Functionally complete, but evidence or production hardening needs work |

| 70–79 | Learning prototype; not yet portfolio ready |

| Below 70 | Incomplete |

### Non-negotiable passing rule

A score of 90 is not sufficient by itself. Every **Critical Gate** in Section 4 must pass. A failed Critical Gate means the project is not portfolio ready, regardless of total points.

### Evidence rule

No points are awarded solely because a technology name appears in the README or dependency list. Credit requires:

1. Working implementation

2. Automated test or repeatable verification

3. Documentation explaining the design decision

4. Visible demonstration in the application, logs, evaluation report, or demo video

## 3. Weighted rubric — 100 points

### A. Product foundation and user experience — 8 points

| Requirement | Points | Evidence required |
|---|---:|---|

| A user can browse imported issues, open an issue, start analysis, and review a recommendation | 3 | Working UI and end-to-end test or documented manual acceptance test |

| React/TypeScript code uses typed API contracts, reusable components, and clear loading/error/empty states | 3 | Source inspection, frontend tests, screenshots |

| The UI clearly distinguishes evidence, AI inference, proposed action, and human decision | 2 | Screenshots and demo |

### B. Backend, API, and data architecture — 8 points

| Requirement | Points | Evidence required |
|---|---:|---|

| FastAPI exposes versioned, schema-validated endpoints with consistent errors | 2 | OpenAPI schema and API tests |

| PostgreSQL schema cleanly separates tenants, repositories, issues, analyses, prompts, approvals, and audit events | 3 | Migration files, ER diagram, schema tests |

| Public GitHub data import is bounded, reproducible, sanitized, cached, and works without special data access | 2 | Import command, fixture data, provenance documentation |

| Important architecture choices are recorded as ADRs | 1 | ADR directory with accepted decisions |

### C. Agent orchestration and async reliability — 10 points

| Requirement | Points | Evidence required |
|---|---:|---|

| Explicit workflow stages: classify → retrieve → assess → propose → human review | 3 | Workflow diagram, code, stage-level tests |

| Long-running work executes asynchronously and exposes queued/running/completed/failed status | 2 | Worker implementation and UI/API status evidence |

| Workflow supports retry, timeout, idempotency, and recoverable failure | 3 | Forced-failure tests and retry demonstration |

| Human approval is a true pause/gate; the system cannot silently perform a final action | 2 | Authorization/workflow tests and demo |

### D. Multi-provider AI gateway and cost governance — 9 points

| Requirement | Points | Evidence required |
|---|---:|---|

| Provider-neutral request, response, usage, and error contracts | 2 | Adapter interfaces and contract tests |

| At least one real provider plus one deterministic mock/local adapter | 2 | Integration configuration and tests |

| Routing policy considers task, availability, cost, or latency and records the reason | 2 | Routing tests and visible decision record |

| Provider failure triggers controlled fallback without corrupting workflow state | 2 | Forced-failure demonstration |

| Token usage and estimated cost are recorded per request and analysis | 1 | Dashboard/log/database evidence |

### E. RAG and contextual grounding — 10 points

| Requirement | Points | Evidence required |
|---|---:|---|

| Repository documentation and resolved issues are chunked, embedded, and stored with pgvector | 2 | Ingestion code, database evidence, tests |

| Retrieval is tenant/repository scoped and returns deterministic metadata and source links | 2 | Isolation and retrieval tests |

| Generated recommendations cite retrieved evidence | 2 | UI/API examples and grounded-answer tests |

| Context is bounded and excludes irrelevant or unauthorized material | 2 | Context-pack inspection and tests |

| Retrieval quality is measured on a fixed evaluation set | 2 | Recall/precision or hit-rate report with documented thresholds |

### F. LLMOps: prompts, evaluation, and regression detection — 12 points

| Requirement | Points | Evidence required |
|---|---:|---|

| Prompts have stable names, semantic versions, content hashes, and release status | 2 | Prompt registry and tests |

| Every AI result records prompt version, provider, model, parameters, and retrieved context identifiers | 2 | Trace/database evidence |

| Fixed evaluation dataset covers classification, groundedness, citation correctness, usefulness, and safety | 2 | Versioned evaluation fixtures |

| Evaluation runs automatically and produces machine-readable plus human-readable reports | 2 | CI/local command and saved report |

| Quality thresholds block a prompt/model release when results regress | 2 | Intentionally failing regression demonstration |

| Model, prompt, or retrieval behavior changes are tracked over time without misrepresenting this as real-world population drift | 2 | Comparison report and documented terminology |

### G. Guardrails and security — 10 points

| Requirement | Points | Evidence required |
|---|---:|---|

| GitHub issue text and retrieved documents are treated as untrusted data, not executable instructions | 2 | Prompt construction and injection tests |

| Structured outputs are schema validated; invalid or unsafe output fails closed | 2 | Negative tests |

| Tool/action allowlists prevent unauthorized external or state-changing operations | 2 | Policy code and denial tests |

| API applies authentication, authorization, rate limiting, input limits, and safe error handling | 2 | Security tests and configuration |

| Secrets are excluded from Git, loaded securely, documented, and scanned in CI | 2 | `.env.example`, secret scan, cloud configuration evidence |

### H. Multi-tenancy, roles, and auditability — 10 points

| Requirement | Points | Evidence required |
|---|---:|---|

| At least two organizations/tenants are supported by the data model | 2 | Seed data and schema evidence |

| Viewer, reviewer, and administrator roles have distinct backend-enforced permissions | 2 | Authorization matrix and tests |

| Tenant A cannot access Tenant B records, context, vectors, or traces | 3 | Mandatory cross-tenant negative tests |

| Append-only audit events capture imports, analyses, routing, approvals, rejections, edits, and administrative changes | 2 | Audit viewer/database evidence |

| Sensitive fields are minimized and sanitized from imported public data | 1 | Import tests and data policy |

### I. Observability and operational readiness — 8 points

| Requirement | Points | Evidence required |
|---|---:|---|

| Correlation IDs connect frontend request, API, workflow, retrieval, model call, and audit event | 2 | Trace demonstration |

| OpenTelemetry captures meaningful spans and errors rather than only HTTP access logs | 2 | Trace screenshots/export and instrumentation tests |

| Operational view reports latency, failures, retries, token use, cost, and evaluation status | 2 | Dashboard or report |

| Health/readiness endpoints and structured logs support diagnosis | 1 | Tests and sample logs |

| A short runbook explains how to diagnose one intentionally simulated failure | 1 | Runbook plus demo |

### J. Testing, containers, CI, and cloud delivery — 10 points

| Requirement | Points | Evidence required |
|---|---:|---|

| Unit, API, integration, frontend, security, tenant-isolation, and evaluation tests cover critical behavior | 3 | Passing test report and documented test map |

| CI runs formatting/linting, tests, secret scanning, and dependency/security checks | 2 | Passing GitHub Actions workflow |

| Frontend, API, worker, and PostgreSQL run through documented containers/Compose | 2 | Fresh-start verification |

| Application is deployed to a public cloud environment with secrets kept outside source control | 2 | Live URL or recorded deployment proof and deployment docs |

| Deployment has a rollback or reproducible redeploy procedure | 1 | Runbook and verification |

### K. Portfolio communication and personal understanding — 5 points

| Requirement | Points | Evidence required |
|---|---:|---|

| README explains the problem, users, architecture, setup, demo, results, security, limitations, and future work | 1 | README review |

| Architecture diagram, ER diagram, workflow diagram, threat model, and key ADRs are present and consistent with the code | 1 | Documentation review |

| Three-to-five-minute demo proves the primary workflow plus one failure/fallback and one security denial | 1 | Demo video |

| Recruiter walkthrough quantifies evaluation quality, latency, cost, and test results without inflated claims | 1 | Walkthrough document |

| Jesse can explain each major component, one tradeoff, one failure mode, and one test without reading generated text | 1 | Oral review checkpoint |

## 4. Critical Gates

All gates must pass before the project is labeled portfolio ready.

| Gate | Pass condition |
|---|---|

| G1 — Reproducible start | A fresh clone can start locally from documented commands with sample data |

| G2 — End-to-end workflow | Import → analyze → retrieve evidence → propose → human decision completes successfully |

| G3 — No silent action | No AI output can become an approved/final action without an authorized human decision |

| G4 — Tenant isolation | Automated negative tests prove cross-tenant records and vector results are inaccessible |

| G5 — Security | Prompt-injection fixtures fail safely; no committed secrets; security checks pass |

| G6 — Evaluation | Versioned evaluation suite runs automatically and enforces documented thresholds |

| G7 — Provider resilience | Demonstrated provider failure produces controlled fallback or a safe recoverable failure |

| G8 — Observability | One analysis can be followed across API, workflow, retrieval, model, cost, and audit records |

| G9 — Containers | Documented containerized stack starts and passes smoke tests |

| G10 — Cloud proof | Deployed application or recorded deployment verification completes successfully |

| G11 — CI quality | Required automated checks pass on the final commit |

| G12 — Ownership | Jesse passes the oral walkthrough described in Section 7 |

## 5. Phase gates

Work may not move to the next release until the current gate passes.

### Release 1 — Complete product foundation

Required:

- Public/cached issue dataset

- React/TypeScript issue list and detail experience

- FastAPI and database

- One deterministic classifier or mock analysis path

- Human review record

- Basic tests and CI

**Exit gate:** A fresh local run completes the main workflow without a paid AI call.

### Release 2 — AI architecture

Required:

- Real provider adapter plus mock/local adapter

- Explainable routing and fallback

- RAG with citations

- Prompt registry/versioning

- Automated evaluation and regression threshold

- Guardrails and prompt-injection tests

- Async workflow with retry/recovery

**Exit gate:** The evaluation suite passes; a provider failure and injection attempt are demonstrated safely.

### Release 3 — Enterprise hardening

Required:

- Multi-tenant data model

- Viewer/reviewer/admin permissions

- Cross-tenant isolation tests

- Append-only audit trail

- OpenTelemetry and operational dashboard/report

- Secrets management

- Containerized stack

- Cloud deployment

**Exit gate:** Every Critical Gate passes and the weighted score is at least 90.

### Release 4 — Portfolio packaging

Required:

- Final README and diagrams

- Recruiter walkthrough

- Evaluation and test reports

- Three-to-five-minute demo

- Honest limitations

- Oral ownership review

**Exit gate:** Repository is understandable and runnable by a reviewer who has not seen the build process.

## 6. Explicitly out of scope

The following items do not earn base-rubric points and must not delay completion:

- Automatically commenting on or modifying third-party GitHub repositories

- Autonomous code fixes or pull requests

- Training or fine-tuning a foundation model

- Kubernetes or Helm

- MCP server/client implementation

- Mobile application

- Multiple paid commercial AI subscriptions

- Enterprise SSO/SAML

- Billing/subscription system

- Real-time collaboration

- Native desktop application

- Large-scale load testing beyond a small documented baseline

- Perfect visual design or extensive animation

These may be considered only after Release 4 is approved.

## 7. Learning and ownership gate

For each major rubric area, Jesse must be able to answer:

1. What problem does this component solve?

2. Why did we choose this design instead of a simpler alternative?

3. What is one likely failure or security risk?

4. Which test proves the important behavior?

5. What part is demonstrated versus still unproven at real enterprise scale?

A component implemented by an AI coding assistant does not pass the ownership gate until these questions can be answered accurately.

## 8. Change-control rule

Every proposed feature must satisfy at least one of these conditions:

- It is required by a scored rubric item.

- It is required to pass a Critical Gate.

- It fixes a verified defect blocking a phase gate.

If none applies, record it in `docs/FUTURE_IDEAS.md` and do not implement it during the current release.

Any rubric change must include:

- The requirement being changed

- Why the existing requirement is insufficient

- Score impact

- Schedule/scope impact

- Explicit approval before implementation

## 9. Final portfolio claim boundary

On successful completion, the project supports this claim:

> Designed and built a production-oriented, multi-tenant AI issue-operations platform with provider-independent routing, RAG, human approval, prompt versioning, automated evaluation, security guardrails, asynchronous workflows, auditability, observability, containerization, and cloud deployment.

It does **not** support claims of enterprise-scale production mastery, staff/principal engineering tenure, or operation under real customer load.

## 10. Final sign-off checklist

- [ ] Weighted score is 90 or higher

- [ ] Every Critical Gate passes

- [ ] All required tests pass from a clean environment

- [ ] No secrets or sensitive imported data are committed

- [ ] Evaluation thresholds pass on the final prompt/model configuration

- [ ] Documentation matches the implemented system

- [ ] Cloud deployment is verified

- [ ] Demo video is complete

- [ ] Recruiter walkthrough is complete

- [ ] Honest limitations are documented

- [ ] Jesse passes the ownership review

- [ ] Unscored ideas remain deferred