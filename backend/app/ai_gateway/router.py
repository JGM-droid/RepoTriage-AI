"""Explainable single-provider routing with controlled, in-stage fallback.

Selects the configured adapter (mock by default) and, on any controlled
OpenAI failure (timeout, HTTP error, malformed/missing content), falls back
to the deterministic mock adapter within the *same* stage attempt — never
spending an additional Celery-level retry on a provider failure (see ADR
0008). Configuration errors (e.g. `AI_PROVIDER=openai` with no API key) are
not provider failures: they are never silently turned into a fallback and
raise immediately instead.

Renders the active prompt exactly once per call (see
`app.ai_gateway.prompts`, ADR 0010) and passes the same `RenderedPrompt` to
whichever adapter runs — including the fallback path — so provenance is
never recomputed or duplicated per adapter.
"""

from __future__ import annotations

import time

from app.ai_gateway import mock_adapter, openai_adapter
from app.ai_gateway.contracts import (
    STATUS_FALLBACK,
    AIGatewayConfigurationError,
    AIRequest,
    AIResponse,
    ProviderCallFailed,
)
from app.ai_gateway.prompts import render_active_prompt
from app.config import Settings, get_settings

PROVIDER_MOCK = "mock"
PROVIDER_OPENAI = "openai"


def ensure_ai_gateway_configured(settings: Settings | None = None) -> None:
    """Fail fast on invalid configuration. Called at worker startup and,
    defensively, by the router itself before every call."""
    settings = settings or get_settings()
    if settings.ai_provider == PROVIDER_OPENAI and not settings.openai_api_key:
        raise AIGatewayConfigurationError(
            "AI_PROVIDER=openai requires OPENAI_API_KEY to be configured."
        )


def route_ai_inference(request: AIRequest, settings: Settings | None = None) -> AIResponse:
    settings = settings or get_settings()
    ensure_ai_gateway_configured(settings)
    rendered = render_active_prompt(request.task, request)

    if settings.ai_provider != PROVIDER_OPENAI:
        return mock_adapter.call(request, rendered)

    start = time.monotonic()
    try:
        return openai_adapter.call(request, rendered, settings)
    except ProviderCallFailed as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        fallback = mock_adapter.call(request, rendered)
        return AIResponse(
            narrative=fallback.narrative,
            status=STATUS_FALLBACK,
            provider=fallback.provider,
            model=fallback.model,
            prompt_id=fallback.prompt_id,
            prompt_version=fallback.prompt_version,
            prompt_status=fallback.prompt_status,
            prompt_template_hash=fallback.prompt_template_hash,
            rendered_prompt_hash=fallback.rendered_prompt_hash,
            input_tokens=fallback.input_tokens,
            output_tokens=fallback.output_tokens,
            estimated_cost_usd=fallback.estimated_cost_usd,
            latency_ms=elapsed_ms,
            fallback_reason=exc.reason,
        )
