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

Prompt text is never built here (see ADR 0010): the router renders the
active prompt once through `app.ai_gateway.prompts.render_active_prompt`
and passes the resulting `RenderedPrompt` in, so this adapter cannot
silently diverge from `mock_adapter` or from the registered template.
"""

from __future__ import annotations

import re
import time

import httpx

from app.ai_gateway.contracts import (
    STATUS_SUCCEEDED,
    AIRequest,
    AIResponse,
    InvalidCitationError,
    ProviderCallFailed,
    validate_citations,
)
from app.ai_gateway.prompts import RenderedPrompt
from app.config import Settings

PROVIDER_NAME = "openai"
_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
_CITATIONS_LINE = re.compile(r"\n?Citations:\s*(.+)\s*$", re.IGNORECASE)


def _split_citations(raw_text: str) -> tuple[str, tuple[str, ...]]:
    """Return (narrative_without_citations_line, parsed_citation_ids)."""
    match = _CITATIONS_LINE.search(raw_text)
    if match is None:
        return raw_text.strip(), ()
    citation_ids = tuple(part.strip() for part in match.group(1).split(",") if part.strip())
    narrative = raw_text[: match.start()].strip()
    return narrative, citation_ids


def call(request: AIRequest, rendered: RenderedPrompt, settings: Settings) -> AIResponse:
    """Call OpenAI once, sending exactly `rendered.text`. Raises
    `ProviderCallFailed` on any controlled failure.

    No `max_tokens` is set: the Chat Completions API does not require one,
    and omitting it avoids truncating a normal narrative response.
    """
    payload = {
        "model": settings.openai_model,
        "messages": [{"role": "user", "content": rendered.text}],
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
        raw_content = data["choices"][0]["message"]["content"]
        if not raw_content or not raw_content.strip():
            raise ProviderCallFailed("openai_empty_content")
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        model = data.get("model") or settings.openai_model
    except ProviderCallFailed:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderCallFailed("openai_malformed_response") from exc

    narrative, citations = _split_citations(raw_content)
    if not narrative:
        raise ProviderCallFailed("openai_empty_content")
    response_obj = AIResponse(
        narrative=narrative,
        status=STATUS_SUCCEEDED,
        provider=PROVIDER_NAME,
        model=model,
        prompt_id=rendered.prompt_id,
        prompt_version=rendered.prompt_version,
        prompt_status=rendered.prompt_status,
        prompt_template_hash=rendered.prompt_template_hash,
        rendered_prompt_hash=rendered.rendered_prompt_hash,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=(
            (input_tokens / 1_000_000) * settings.openai_input_price_per_million_usd
            + (output_tokens / 1_000_000) * settings.openai_output_price_per_million_usd
        ),
        latency_ms=latency_ms,
        citations=citations,
    )

    try:
        validate_citations(citations, request)
    except InvalidCitationError as exc:
        raise ProviderCallFailed(f"openai_invalid_citation:{exc}") from exc

    return response_obj
