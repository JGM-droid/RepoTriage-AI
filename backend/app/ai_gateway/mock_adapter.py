"""Deterministic, zero-network AI adapter.

The default for development, the automated test suite, and the normal
demo (see ADR 0008). Same input always produces the same narrative: no
randomness, no clock, no network call. Usage and cost are deterministically
zero because nothing was ever sent to a paid provider.

Renders through the shared prompt registry (`app.ai_gateway.prompts`, ADR
0010) exactly like `openai_adapter` does, and records that rendering's
provenance on its `AIResponse` — but never sends the rendered text
anywhere; its own narrative below is a separately, deterministically
synthesized summary, unchanged from before the registry existed.
"""

from __future__ import annotations

from app.ai_gateway.contracts import STATUS_SUCCEEDED, AIRequest, AIResponse
from app.ai_gateway.prompts import RenderedPrompt

PROVIDER_NAME = "mock"
MODEL_NAME = "deterministic-v1"


def call(request: AIRequest, rendered: RenderedPrompt) -> AIResponse:
    evidence_summary = "; ".join(f"{item.field}: {item.excerpt}" for item in request.evidence)
    narrative = (
        f"Classified as '{request.classification.label}' "
        f"(rule: {request.classification.matched_rule}) with "
        f"'{request.assessment.severity}' severity. "
        f"Evidence considered: {evidence_summary}. "
        f"Proposed action: {request.proposed_action.action}"
    )
    citations: tuple[str, ...] = ()
    if request.retrieved_context:
        citations = tuple(record.identifier for record in request.retrieved_context)
        related = "; ".join(
            f"{record.identifier} ({record.title})" for record in request.retrieved_context
        )
        narrative += f" Related repository evidence: {related}. [cites: {', '.join(citations)}]"
    return AIResponse(
        narrative=narrative,
        status=STATUS_SUCCEEDED,
        provider=PROVIDER_NAME,
        model=MODEL_NAME,
        prompt_id=rendered.prompt_id,
        prompt_version=rendered.prompt_version,
        prompt_status=rendered.prompt_status,
        prompt_template_hash=rendered.prompt_template_hash,
        rendered_prompt_hash=rendered.rendered_prompt_hash,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0.0,
        latency_ms=0.0,
        citations=citations,
    )
