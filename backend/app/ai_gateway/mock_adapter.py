"""Deterministic, zero-network AI adapter.

The default for development, the automated test suite, and the normal
demo (see ADR 0008). Same input always produces the same narrative: no
randomness, no clock, no network call. Usage and cost are deterministically
zero because nothing was ever sent to a paid provider.
"""

from __future__ import annotations

from app.ai_gateway.contracts import PROMPT_NAME, STATUS_SUCCEEDED, AIRequest, AIResponse

PROVIDER_NAME = "mock"
MODEL_NAME = "deterministic-v1"


def call(request: AIRequest) -> AIResponse:
    evidence_summary = "; ".join(f"{item.field}: {item.excerpt}" for item in request.evidence)
    narrative = (
        f"Classified as '{request.classification.label}' "
        f"(rule: {request.classification.matched_rule}) with "
        f"'{request.assessment.severity}' severity. "
        f"Evidence considered: {evidence_summary}. "
        f"Proposed action: {request.proposed_action.action}"
    )
    return AIResponse(
        narrative=narrative,
        status=STATUS_SUCCEEDED,
        provider=PROVIDER_NAME,
        model=MODEL_NAME,
        prompt_name=PROMPT_NAME,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0.0,
        latency_ms=0.0,
    )
