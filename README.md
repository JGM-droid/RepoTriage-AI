# RepoTriage AI

## A Governed, Evidence-Backed GitHub Issue Intelligence Platform

RepoTriage AI addresses the repeated triage burden created by duplicate, incomplete, misclassified, repetitive, and misrouted GitHub issues. It is intended for support engineers, developer-relations teams, engineering managers, QA leads, product-operations teams, and open-source maintainers in software organizations with limited senior-review capacity.

The planned workflow is `import → classify → retrieve evidence → assess → propose → human review → decision`. Planned capabilities include evidence-backed recommendations, human approval, provider-neutral AI routing, repository-grounded retrieval, evaluation, security guardrails, auditability, observability, and reproducible delivery.

**Current status:** Release 1 — Product foundation / Milestone 1.6 — Release 1 verification in progress. Import, issue browsing, deterministic triage (classify → retrieve fixture evidence → assess → propose → human review), and an explicit human approve/reject/request-revision decision are implemented; none of it requires a paid AI provider.

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

## Offline Issue Import (Milestone 1.2)

Import the committed, cached fixture of exactly 100 sanitized `pallets/flask` issues into the running PostgreSQL database. This is the default demo/import path and performs no network call:

```powershell
& .\backend\.venv\Scripts\python.exe -m app.importer
```

Running the command twice is idempotent: the second run reports `0 new issue(s)` because the existing `repositories.source_url` and `(issues.repository_id, issues.external_number)` constraints make repeat imports safe. The fixture and its provenance manifest live at `backend/fixtures/pallets_flask/`; see [ADR 0006](docs/adr/0006-bounded-issue-importer-design.md) for the import boundary, bounds, and sanitization policy.

## Release 1 Acceptance Test (Milestone 1.6)

`backend/tests/test_release1_acceptance.py` is the automated, documented acceptance test for the Release 1 exit gate: a fresh local run that completes `import → browse → analyze → review → decision` end to end using only deterministic, offline logic and no paid AI provider. It runs as part of the normal backend test suite against a migrated PostgreSQL database:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://repotriage:repotriage@localhost:5432/repotriage"
& .\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_release1_acceptance.py
```

To walk through the same acceptance path manually in the browser: run `docker compose up --build -d`, open `http://localhost:5173`, confirm the health banner is healthy, run `python -m app.importer` once for the running stack, browse the issue list and open an issue, click **Run deterministic triage**, review the separated Evidence/System inference/Proposed action sections, then click **Approve**, **Reject**, or **Request revision** and confirm the recorded decision is displayed and the controls disappear.

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
- [ADR 0006: Bounded issue importer design](docs/adr/0006-bounded-issue-importer-design.md)
- [Agent instructions](AGENTS.md)

## Claim Boundary

The project may demonstrate production-oriented architecture and tested behavior. It does not claim proven enterprise scale, live customer ROI, staff or principal engineering tenure, or operation under significant customer load.