# RepoTriage AI

## A Governed, Evidence-Backed GitHub Issue Intelligence Platform

RepoTriage AI addresses the repeated triage burden created by duplicate, incomplete, misclassified, repetitive, and misrouted GitHub issues. It is intended for support engineers, developer-relations teams, engineering managers, QA leads, product-operations teams, and open-source maintainers in software organizations with limited senior-review capacity.

The planned workflow is `import → classify → retrieve evidence → assess → propose → human review → decision`. Planned capabilities include evidence-backed recommendations, human approval, provider-neutral AI routing, repository-grounded retrieval, evaluation, security guardrails, auditability, observability, and reproducible delivery.

**Current status:** Release 0 — Foundation and repository bootstrap / Milestone 0.2 — Runnable skeleton in progress. This foundation provides only frontend-to-health-check and PostgreSQL-readiness connectivity; issue triage and AI functionality have not started.

## Local Setup

Use Windows PowerShell from the repository root.

```powershell
Copy-Item .env.example .env
py -3.12 -m venv backend/.venv
& .\backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
Set-Location frontend
npm ci
Set-Location ..
```

Run local checks:

```powershell
& .\backend\.venv\Scripts\python.exe -m ruff format --check backend
& .\backend\.venv\Scripts\python.exe -m ruff check backend
& .\backend\.venv\Scripts\python.exe -m pytest backend/tests
Set-Location frontend; npm run lint; npm test; npm run build; Set-Location ..
```

Validate and run the container stack:

```powershell
docker compose config
docker compose up --build -d
Invoke-WebRequest http://localhost:8000/api/v1/health
Invoke-WebRequest http://localhost:8000/api/v1/readiness
docker compose down
```

The frontend is available at `http://localhost:5173`. The Compose stack uses frontend port 5173, API port 8000, and PostgreSQL internally; it does not publish PostgreSQL.

## Project Controls

- [Portfolio rubric](docs/PORTFOLIO_RUBRIC.md)
- [Execution roadmap](docs/EXECUTION_ROADMAP.md)
- [Business case](docs/BUSINESS_CASE.md)
- [Release 1 requirement traceability](docs/REQUIREMENT_TRACEABILITY.md)
- [Future ideas and deferred scope](docs/FUTURE_IDEAS.md)
- [ADR guide](docs/adr/README.md)
- [ADR template](docs/adr/ADR_TEMPLATE.md)
- [ADR 0001: Initial technology stack](docs/adr/0001-initial-technology-stack.md)
- [ADR 0002: Public fixture-data policy](docs/adr/0002-public-fixture-data-policy.md)
- [ADR 0003: Mandatory human-review boundary](docs/adr/0003-mandatory-human-review-boundary.md)
- [ADR 0004: Deferred async workflow technology selection](docs/adr/0004-defer-async-workflow-technology-selection.md)
- [Agent instructions](AGENTS.md)

## Claim Boundary

The project may demonstrate production-oriented architecture and tested behavior. It does not claim proven enterprise scale, live customer ROI, staff or principal engineering tenure, or operation under significant customer load.