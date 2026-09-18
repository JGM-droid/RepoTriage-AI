"""The `ai_inference` workflow stage.

The only place `app.ai_gateway` is wired into the deterministic triage
pipeline (see `app.workflow.tasks`). Builds the bounded request from
already-computed, already-sanitized values and routes it; never touches
the database or Celery directly, so it stays testable as a plain function
like every other stage in `app.triage.rules`.
"""

from __future__ import annotations

from app.ai_gateway.contracts import AIRequest, AIResponse
from app.ai_gateway.router import route_ai_inference
from app.observability import span
from app.retrieval.contracts import RetrievedRecord
from app.triage.rules import Assessment, Classification, EvidenceItem, ProposedAction

AI_INFERENCE_TASK_NAME = "triage_narrative"


def run_ai_inference_stage(
    issue: object,
    classification: Classification,
    evidence: tuple[EvidenceItem, ...],
    assessment: Assessment,
    proposed_action: ProposedAction,
    retrieved_context: tuple[RetrievedRecord, ...] = (),
) -> AIResponse:
    del issue  # the request below carries only the already-gathered fields
    request = AIRequest(
        task=AI_INFERENCE_TASK_NAME,
        classification=classification,
        evidence=evidence,
        assessment=assessment,
        proposed_action=proposed_action,
        retrieved_context=retrieved_context,
    )
    with span("ai.inference", attributes={"ai.task": AI_INFERENCE_TASK_NAME}) as current_span:
        response = route_ai_inference(request)
        current_span.set_attribute("ai.provider", response.provider)
        current_span.set_attribute("ai.status", response.status)
        current_span.set_attribute("ai.input_tokens", response.input_tokens)
        current_span.set_attribute("ai.output_tokens", response.output_tokens)
        current_span.set_attribute("ai.estimated_cost_usd", response.estimated_cost_usd)
        return response
