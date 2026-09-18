# ADR 0016: OpenTelemetry observability and durable correlation context

**Status:** Proposed — implemented for Jesse's Milestone 3.3 ownership review
**Date:** 2026-09-20

## Context

Milestone 3.3, rubric §I, and Critical Gate G8 require one analysis to be followed from the
browser through API, durable Celery execution, retrieval, AI inference, token/cost evidence, and
append-only audit history. The existing workflow deliberately sends only an analysis id through
Celery and resumes from PostgreSQL, so transient task headers cannot be the authoritative context.

## Decision

Use one OpenTelemetry SDK boundary in `app.observability`. The browser generates a UUID correlation
value. API middleware accepts only 1–64 characters from a bounded safe alphabet, otherwise replaces
it, returns it in `X-Correlation-ID`, and exposes it in triage response bodies. New `Analysis`
columns persist that value and the bounded W3C `traceparent`; migration `20260920_0008` leaves old
analyses null rather than inventing provenance. The worker continues to receive only the analysis
id and reloads both values from PostgreSQL for every delivery, retry, or resume.

Manual spans cover `http.request`, identity/role authorization, `workflow.run`, every
`workflow.stage`, `retrieval.query`, `ai.inference`, recommendation persistence, and human-decision
persistence. Metrics cover HTTP counts/errors/duration; workflow outcomes/duration/retries;
stage outcomes/duration; retrieval outcomes; AI calls/input tokens/output tokens/estimated cost;
human decisions; and evaluation pass/fail runs. Metric attributes are code-owned bounded enums or
route templates—never organization, actor, issue, analysis, repository, correlation, URL, prompt,
or source identifiers.

Operational logs are JSON and add correlation/trace/span context automatically. Telemetry accepts
no issue body, retrieved excerpt/document content, prompt, provider payload, secret, credential,
raw header, or exception message. Error spans record exception class only. Existing audit events
gain the correlation value; they retain their existing tenant-scoped read path and append-only
database enforcement.

Compose adds an OpenTelemetry Collector, Jaeger trace UI, and Prometheus metric UI. API and worker
export OTLP/HTTP asynchronously. Neither depends on Collector health, so missing or failed export
loses telemetry but never changes triage, authorization, persistence, or human-review results.

## Alternatives considered

Passing tenant, actor, correlation, and trace data in the Celery payload was rejected because it
would duplicate and weaken the established database-derived trust boundary. A custom telemetry
database/dashboard was rejected in favor of standard OpenTelemetry, Jaeger, and Prometheus. Two
parallel metrics/logging stacks were rejected because they would duplicate initialization and
make evidence inconsistent. Persisting full span history in application tables was rejected;
audit history and operational telemetry have different retention and access purposes.

## Consequences

The backend adds pinned OpenTelemetry SDK and OTLP HTTP exporter dependencies and two nullable
analysis columns. Export is deliberately fail-open: when the Collector is unavailable, the
application continues and the SDK reports export loss in process logs. This is operational
diagnostics, not durable business evidence; PostgreSQL remains authoritative. Old analyses cannot
be retroactively connected to traces. Local Jaeger/Prometheus data is ephemeral unless operators
add storage, which is outside this milestone.

## Security and tenancy

Organization and analysis identifiers may appear on trace spans for diagnosis, but never as metric
labels. Correlation values are validated and bounded before use. No telemetry read API was added,
and no application authorization rule changed. Jaeger and Prometheus are local development ports,
not authenticated production services and not a cloud deployment.

## Verification

Focused tests prove correlation generation/validation/response/persistence, API-to-worker parentage,
required span/metric emission, correlated audit history, prohibited-content exclusion, durable
retry/resume context, exporter-failure fail-open behavior, and unchanged tenant isolation. The
diagnostic procedure is in `docs/OBSERVABILITY_RUNBOOK.md`. Milestone 3.3 and G8 remain in progress
pending Jesse's explicit completion approval and GitHub CI proof.
