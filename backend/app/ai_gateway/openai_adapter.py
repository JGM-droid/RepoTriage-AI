"""OpenAI provider adapter.

Uses the project's existing `httpx` dependency directly against OpenAI's
Chat Completions REST endpoint, rather than adding the official `openai`
SDK as a new dependency — the request/response shape needed here is a
single JSON POST, well within what `httpx` already handles safely.

Every failure this adapter can hit (timeout, HTTP error, malformed or
missing content) is translated into `ProviderCallFailed`, which the router
treats as a controlled provider failure and falls back from. This adapter
never decides to fall back itself and never swallows a configuration
error — that check happens in the router before this module is called.

The API key is read from settings for exactly one request and is never
logged or persisted.
"""

from __future__ import annotations

import time

import httpx

from app.ai_gateway.contracts import (
    PROMPT_NAME,
    STATUS_SUCCEEDED,
    AIRequest,
    AIResponse,
    ProviderCallFailed,
)
from app.config import Settings

PROVIDER_NAME = "openai"
_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"


def _build_prompt(request: AIRequest) -> str:
    evidence_lines = "\n".join(f"- {item.field}: {item.excerpt}" for item in request.evidence)
    return (
        "You are assisting with deterministic, evidence-backed GitHub issue "
        "triage. Write a short narrative (2-4 sentences) that supplements — "
        "and never contradicts — the classification, severity, and proposed "
        "action below. Ground every statement only in the evidence provided; "
        "do not invent facts.\n\n"
        f"Classification: {request.classification.label} "
        f"(rule: {request.classification.matched_rule})\n"
        f"Severity: {request.assessment.severity}\n"
        f"Rationale: {request.assessment.rationale}\n"
        f"Proposed action: {request.proposed_action.action}\n"
        f"Evidence:\n{evidence_lines}\n"
    )


def call(request: AIRequest, settings: Settings) -> AIResponse:
    """Call OpenAI once. Raises `ProviderCallFailed` on any controlled failure.

    No `max_tokens` is set: the Chat Completions API does not require one,
    and omitting it avoids truncating a normal narrative response.
    """
    payload = {
        "model": settings.openai_model,
        "messages": [{"role": "user", "content": _build_prompt(request)}],
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    start = time.monotonic()
    try:
        response = httpx.post(
            _CHAT_COMPLETIONS_URL,
            json=payload,
            headers=headers,
            timeout=settings.ai_provider_timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        raise ProviderCallFailed("openai_timeout") from exc
    except httpx.HTTPError as exc:
        raise ProviderCallFailed("openai_request_error") from exc
    latency_ms = (time.monotonic() - start) * 1000

    if response.status_code != 200:
        raise ProviderCallFailed(f"openai_http_{response.status_code}")

    try:
        data = response.json()
        narrative = data["choices"][0]["message"]["content"]
        if not narrative or not narrative.strip():
            raise ProviderCallFailed("openai_empty_content")
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        model = data.get("model") or settings.openai_model
    except ProviderCallFailed:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderCallFailed("openai_malformed_response") from exc

    estimated_cost_usd = (
        input_tokens / 1_000_000
    ) * settings.openai_input_price_per_million_usd + (
        output_tokens / 1_000_000
    ) * settings.openai_output_price_per_million_usd

    return AIResponse(
        narrative=narrative.strip(),
        status=STATUS_SUCCEEDED,
        provider=PROVIDER_NAME,
        model=model,
        prompt_name=PROMPT_NAME,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        latency_ms=latency_ms,
    )
