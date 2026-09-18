# Local observability and collector-failure runbook

## Inspect one analysis

1. Start the documented Compose stack and run one triage analysis in the browser.
2. Copy `correlation_id` from the triage-start/result JSON or `X-Correlation-ID` response header.
3. Open Jaeger at `http://localhost:16686`, select `repotriage-api`, search recent traces, and open
   the matching API trace. The linked `repotriage-worker` span contains the authorization,
   retrieval, AI, persistence, and human-review stage spans. The correlation value is also present
   in JSON API/worker logs and the analysis's tenant-scoped audit history.
4. Open Prometheus at `http://localhost:9090` and query `{__name__=~"repotriage_.*"}`. Useful series
   cover HTTP request duration/errors, workflow and stage outcomes/duration/retries, retrieval,
   AI call/token/cost totals, human decisions, and evaluation pass/fail runs.

The evaluation metric is emitted when `python -m app.evaluation` runs with an OTLP endpoint set.
Normal mock/local configuration remains `AI_PROVIDER=mock` and `EMBEDDING_PROVIDER=local`.

## Intentionally simulate Collector loss

Use only disposable/local data for this exercise.

```powershell
docker compose stop otel-collector
# Start and complete one triage analysis in the browser.
docker compose logs api worker --since 5m
docker compose start otel-collector
```

Expected: the analysis still reaches its normal durable terminal state, authorization and tenant
checks still run, the recommendation/audit rows persist, and an explicit human decision remains
required. The API/worker may log bounded OTLP connection/export errors, and telemetry generated
while the Collector was stopped can be lost. After restart, a new analysis appears normally in
Jaeger and Prometheus. If triage itself fails, diagnose PostgreSQL/Redis/provider configuration;
Collector availability is intentionally not an application readiness dependency.

Automated evidence uses a deterministic exporter that returns `FAILURE`; the workflow still
completes, and the test-scoped substitution is automatically restored afterward.
