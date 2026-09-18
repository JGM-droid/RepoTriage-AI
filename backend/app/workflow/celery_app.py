"""Celery application instance for the durable triage workflow.

Redis is only the broker/result backend for transient task coordination;
PostgreSQL (Analysis, StageAttempt, Recommendation, AuditEvent) remains the
canonical, durable workflow-state store. See docs/adr/0007.
"""

from celery import Celery
from celery.signals import worker_process_init

from app.ai_gateway.router import ensure_ai_gateway_configured
from app.config import get_settings
from app.observability import configure_observability

settings = get_settings()


@worker_process_init.connect
def _configure_worker_observability(**_kwargs) -> None:
    # Deferred until a real worker process starts. Importing this module from
    # the API (to enqueue a task) must not claim the API process as a worker.
    configure_observability("repotriage-worker", settings.otel_exporter_otlp_endpoint)


# Fail startup, not a task: an invalid AI-gateway configuration (e.g.
# AI_PROVIDER=openai with no API key) must crash the worker/API process
# immediately rather than surface later as a confusing per-task failure or,
# worse, be silently treated as a provider failure and masked by the mock
# fallback. See ADR 0008.
ensure_ai_gateway_configured(settings)

celery_app = Celery(
    "repotriage",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Redis' default 3600s visibility timeout would let a hard worker crash
    # strand an unacked task for up to an hour before redelivery. Bound it
    # to roughly one worst-case full-retry run instead.
    broker_transport_options={
        "visibility_timeout": settings.triage_stage_timeout_seconds
        * (settings.triage_max_attempts + 1)
    },
)

# Imported after `celery_app` is constructed so the worker (started as
# `celery -A app.workflow.celery_app worker`) registers the task.
from app.workflow import tasks  # noqa: E402,F401
