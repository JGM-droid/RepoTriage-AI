"""AI gateway tests (Milestone 2.2): contracts, adapters, and routing.

No test here makes a real network call. The OpenAI adapter is exercised
exclusively via a monkeypatched `httpx.post`; every other adapter is the
zero-network deterministic mock.
"""

from __future__ import annotations

import httpx
import pytest

from app.ai_gateway import mock_adapter, openai_adapter, router
from app.ai_gateway.contracts import (
    STATUS_FALLBACK,
    STATUS_SUCCEEDED,
    AIGatewayConfigurationError,
    AIRequest,
    AIResponse,
    ProviderCallFailed,
)
from app.config import Settings
from app.triage.rules import Assessment, Classification, EvidenceItem, ProposedAction

CLASSIFICATION = Classification(
    label="bug-crash", matched_rule="crash-keyword", matched_keywords=("crash",)
)
EVIDENCE = (
    EvidenceItem(
        field="title", excerpt="CLI crashes on startup", source_url="https://example.test/1"
    ),
)
ASSESSMENT = Assessment(severity="high", rationale="Matched rule 'crash-keyword'.")
PROPOSED_ACTION = ProposedAction(
    action="Prioritize for immediate triage.", rationale="Derived from classification."
)


def make_request(task: str = "triage_narrative") -> AIRequest:
    return AIRequest(
        task=task,
        classification=CLASSIFICATION,
        evidence=EVIDENCE,
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
    )


def make_settings(**overrides: object) -> Settings:
    defaults = {
        "ai_provider": "mock",
        "openai_api_key": None,
        "openai_model": "gpt-4o-mini",
        "ai_provider_timeout_seconds": 5.0,
        "openai_input_price_per_million_usd": 1.0,
        "openai_output_price_per_million_usd": 2.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# --- contracts -----------------------------------------------------------


def test_ai_request_rejects_an_empty_task() -> None:
    with pytest.raises(ValueError):
        AIRequest(
            task="",
            classification=CLASSIFICATION,
            evidence=EVIDENCE,
            assessment=ASSESSMENT,
            proposed_action=PROPOSED_ACTION,
        )


def test_ai_request_rejects_evidence_that_is_not_a_tuple() -> None:
    with pytest.raises(TypeError):
        AIRequest(
            task="triage_narrative",
            classification=CLASSIFICATION,
            evidence=list(EVIDENCE),  # type: ignore[arg-type]
            assessment=ASSESSMENT,
            proposed_action=PROPOSED_ACTION,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"narrative": ""},
        {"status": "not_a_real_status"},
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"estimated_cost_usd": -0.01},
        {"latency_ms": -1.0},
    ],
)
def test_ai_response_rejects_invalid_fields(overrides: dict[str, object]) -> None:
    fields = {
        "narrative": "A narrative.",
        "status": STATUS_SUCCEEDED,
        "provider": "mock",
        "model": "deterministic-v1",
        "prompt_name": "triage_narrative_v1",
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "latency_ms": 0.0,
    }
    fields.update(overrides)
    with pytest.raises(ValueError):
        AIResponse(**fields)


# --- mock adapter ----------------------------------------------------------


def test_mock_adapter_is_deterministic_with_zero_usage_and_cost() -> None:
    first = mock_adapter.call(make_request())
    second = mock_adapter.call(make_request())

    assert first == second
    assert first.status == STATUS_SUCCEEDED
    assert first.provider == "mock"
    assert first.input_tokens == 0
    assert first.output_tokens == 0
    assert first.estimated_cost_usd == 0.0
    assert "bug-crash" in first.narrative
    assert "Prioritize for immediate triage." in first.narrative


# --- OpenAI adapter (mocked HTTP only) -------------------------------------


def _openai_response(
    *, status_code: int = 200, content: str = "A grounded narrative.", usage: dict | None = None
) -> httpx.Response:
    payload = {
        "choices": [{"message": {"content": content}}],
        "usage": usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 50},
        "model": "gpt-4o-mini-2024",
    }
    return httpx.Response(status_code, json=payload)


def test_openai_adapter_success_records_usage_and_computes_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_adapter.httpx,
        "post",
        lambda *args, **kwargs: _openai_response(
            usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
        ),
    )
    settings = make_settings(
        ai_provider="openai",
        openai_api_key="sk-test",
        openai_input_price_per_million_usd=1.0,
        openai_output_price_per_million_usd=2.0,
    )

    result = openai_adapter.call(make_request(), settings)

    assert result.status == STATUS_SUCCEEDED
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini-2024"
    assert result.input_tokens == 1_000_000
    assert result.output_tokens == 500_000
    assert result.estimated_cost_usd == pytest.approx(1.0 + 1.0)
    assert result.narrative == "A grounded narrative."


def test_openai_adapter_timeout_raises_provider_call_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(openai_adapter.httpx, "post", raise_timeout)
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    with pytest.raises(ProviderCallFailed) as exc_info:
        openai_adapter.call(make_request(), settings)
    assert exc_info.value.reason == "openai_timeout"


def test_openai_adapter_http_error_raises_provider_call_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_adapter.httpx, "post", lambda *args, **kwargs: httpx.Response(500, text="boom")
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    with pytest.raises(ProviderCallFailed) as exc_info:
        openai_adapter.call(make_request(), settings)
    assert exc_info.value.reason == "openai_http_500"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
    ],
)
def test_openai_adapter_malformed_or_missing_content_raises_provider_call_failed(
    monkeypatch: pytest.MonkeyPatch, payload: dict
) -> None:
    monkeypatch.setattr(
        openai_adapter.httpx, "post", lambda *args, **kwargs: httpx.Response(200, json=payload)
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    with pytest.raises(ProviderCallFailed):
        openai_adapter.call(make_request(), settings)


def test_openai_adapter_never_logs_or_persists_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter's own success/failure paths never surface the raw key."""
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):
        captured["headers"] = headers
        return _openai_response()

    monkeypatch.setattr(openai_adapter.httpx, "post", fake_post)
    settings = make_settings(ai_provider="openai", openai_api_key="sk-super-secret")

    result = openai_adapter.call(make_request(), settings)

    assert "sk-super-secret" not in repr(result)
    assert "sk-super-secret" not in str(result.narrative)
    # The key is sent exactly once, as a bearer header, to the provider only.
    assert captured["headers"]["Authorization"] == "Bearer sk-super-secret"


# --- router: configuration vs. provider failure -----------------------------


def test_ensure_ai_gateway_configured_passes_for_the_default_mock_provider() -> None:
    router.ensure_ai_gateway_configured(make_settings(ai_provider="mock"))


def test_ensure_ai_gateway_configured_raises_for_openai_without_an_api_key() -> None:
    with pytest.raises(AIGatewayConfigurationError):
        router.ensure_ai_gateway_configured(
            make_settings(ai_provider="openai", openai_api_key=None)
        )


def test_router_uses_the_mock_adapter_by_default_with_no_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("the real OpenAI adapter must not be called when ai_provider=mock")

    monkeypatch.setattr(router.openai_adapter, "call", fail_if_called)

    result = router.route_ai_inference(make_request(), make_settings(ai_provider="mock"))

    assert result.status == STATUS_SUCCEEDED
    assert result.provider == "mock"


def test_router_falls_back_to_mock_within_the_same_call_on_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_failed(*args, **kwargs):
        raise ProviderCallFailed("openai_timeout")

    monkeypatch.setattr(router.openai_adapter, "call", raise_failed)
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    result = router.route_ai_inference(make_request(), settings)

    assert result.status == STATUS_FALLBACK
    assert result.provider == "mock"
    assert result.fallback_reason == "openai_timeout"
    # The narrative is still a valid, usable mock narrative, not empty.
    assert result.narrative


def test_router_configuration_error_is_never_turned_into_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("openai adapter must not be reached for a configuration error")

    monkeypatch.setattr(router.openai_adapter, "call", fail_if_called)
    settings = make_settings(ai_provider="openai", openai_api_key=None)

    with pytest.raises(AIGatewayConfigurationError):
        router.route_ai_inference(make_request(), settings)
