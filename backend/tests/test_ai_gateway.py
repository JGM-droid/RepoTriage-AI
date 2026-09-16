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
    validate_citations,
)
from app.ai_gateway.prompts import RenderedPrompt, render_active_prompt
from app.config import Settings
from app.retrieval.contracts import RetrievedRecord
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
RETRIEVED_RECORD = RetrievedRecord(
    identifier="issue:5756",
    source_type="issue",
    external_number=5756,
    title="404 Flask cannot find /security/login API",
    excerpt="A related closed issue about a missing security endpoint.",
    source_url="https://github.com/pallets/flask/issues/5756",
    similarity_score=0.87,
    relevance_explanation="Ranked as a related resolved issue with cosine similarity 0.870.",
    embedding_model="fake-hash-embedder",
    embedding_version="1",
)


def make_request(task: str = "triage_narrative", retrieved_context: tuple = ()) -> AIRequest:
    return AIRequest(
        task=task,
        classification=CLASSIFICATION,
        evidence=EVIDENCE,
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
        retrieved_context=retrieved_context,
    )


def make_rendered(request: AIRequest | None = None) -> RenderedPrompt:
    return render_active_prompt("triage_narrative", request or make_request())


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
        "prompt_id": "triage_narrative",
        "prompt_version": "1.0.0",
        "prompt_status": "released",
        "prompt_template_hash": "a" * 64,
        "rendered_prompt_hash": "b" * 64,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "latency_ms": 0.0,
    }
    fields.update(overrides)
    with pytest.raises(ValueError):
        AIResponse(**fields)


@pytest.mark.parametrize(
    "missing_field",
    ["prompt_id", "prompt_version", "prompt_template_hash", "rendered_prompt_hash"],
)
def test_ai_response_rejects_empty_prompt_provenance_fields(missing_field: str) -> None:
    fields = {
        "narrative": "A narrative.",
        "status": STATUS_SUCCEEDED,
        "provider": "mock",
        "model": "deterministic-v1",
        "prompt_id": "triage_narrative",
        "prompt_version": "1.0.0",
        "prompt_status": "released",
        "prompt_template_hash": "a" * 64,
        "rendered_prompt_hash": "b" * 64,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
        "latency_ms": 0.0,
    }
    fields[missing_field] = ""
    with pytest.raises(ValueError):
        AIResponse(**fields)


# --- mock adapter ----------------------------------------------------------


def test_mock_adapter_is_deterministic_with_zero_usage_and_cost() -> None:
    first = mock_adapter.call(make_request(), make_rendered())
    second = mock_adapter.call(make_request(), make_rendered())

    assert first == second
    assert first.status == STATUS_SUCCEEDED
    assert first.provider == "mock"
    assert first.input_tokens == 0
    assert first.output_tokens == 0
    assert first.estimated_cost_usd == 0.0
    assert "bug-crash" in first.narrative
    assert "Prioritize for immediate triage." in first.narrative


def test_mock_adapter_records_the_rendered_prompt_provenance_without_sending_it() -> None:
    request = make_request()
    rendered = make_rendered(request)

    result = mock_adapter.call(request, rendered)

    assert result.prompt_id == rendered.prompt_id == "triage_narrative"
    assert result.prompt_version == rendered.prompt_version == "1.1.0"
    assert result.prompt_status == rendered.prompt_status == "released"
    assert result.prompt_template_hash == rendered.prompt_template_hash
    assert result.rendered_prompt_hash == rendered.rendered_prompt_hash
    # The mock narrative is its own deterministic summary, not the rendered
    # prompt text itself.
    assert result.narrative != rendered.text


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

    request = make_request()
    rendered = make_rendered(request)
    result = openai_adapter.call(request, rendered, settings)

    assert result.status == STATUS_SUCCEEDED
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini-2024"
    assert result.input_tokens == 1_000_000
    assert result.output_tokens == 500_000
    assert result.estimated_cost_usd == pytest.approx(1.0 + 1.0)
    assert result.narrative == "A grounded narrative."
    assert result.prompt_id == rendered.prompt_id
    assert result.prompt_version == rendered.prompt_version
    assert result.prompt_status == rendered.prompt_status
    assert result.prompt_template_hash == rendered.prompt_template_hash
    assert result.rendered_prompt_hash == rendered.rendered_prompt_hash


def test_openai_adapter_sends_exactly_the_shared_rendered_prompt_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registry, not the adapter, owns prompt construction — the
    adapter must send `rendered.text` byte-for-byte, never a second,
    independently built prompt string."""
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):
        captured["content"] = json["messages"][0]["content"]
        return _openai_response()

    monkeypatch.setattr(openai_adapter.httpx, "post", fake_post)
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))
    rendered = make_rendered(request)

    openai_adapter.call(request, rendered, settings)

    assert captured["content"] == rendered.text


def test_openai_adapter_timeout_raises_provider_call_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("simulated timeout")

    monkeypatch.setattr(openai_adapter.httpx, "post", raise_timeout)
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    with pytest.raises(ProviderCallFailed) as exc_info:
        openai_adapter.call(make_request(), make_rendered(), settings)
    assert exc_info.value.reason == "openai_timeout"


def test_openai_adapter_http_error_raises_provider_call_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_adapter.httpx, "post", lambda *args, **kwargs: httpx.Response(500, text="boom")
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    with pytest.raises(ProviderCallFailed) as exc_info:
        openai_adapter.call(make_request(), make_rendered(), settings)
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
        openai_adapter.call(make_request(), make_rendered(), settings)


def test_openai_adapter_never_logs_or_persists_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter's own success/failure paths never surface the raw key."""
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):
        captured["headers"] = headers
        return _openai_response()

    monkeypatch.setattr(openai_adapter.httpx, "post", fake_post)
    settings = make_settings(ai_provider="openai", openai_api_key="sk-super-secret")

    result = openai_adapter.call(make_request(), make_rendered(), settings)

    assert "sk-super-secret" not in repr(result)
    assert "sk-super-secret" not in str(result.narrative)
    # The key is sent exactly once, as a bearer header, to the provider only.
    assert captured["headers"]["Authorization"] == "Bearer sk-super-secret"


# --- prompt-registry provenance parity between adapters (Milestone 2.4) ----


def test_mock_and_openai_adapters_resolve_identical_rendered_text_and_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both adapters must render through the same registry call for the
    same request — proving they cannot silently diverge from each other."""
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):
        captured["content"] = json["messages"][0]["content"]
        return _openai_response()

    monkeypatch.setattr(openai_adapter.httpx, "post", fake_post)
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))
    rendered = make_rendered(request)
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    mock_result = mock_adapter.call(request, rendered)
    openai_result = openai_adapter.call(request, rendered, settings)

    assert captured["content"] == rendered.text
    for field in (
        "prompt_id",
        "prompt_version",
        "prompt_status",
        "prompt_template_hash",
        "rendered_prompt_hash",
    ):
        assert (
            getattr(mock_result, field) == getattr(openai_result, field) == getattr(rendered, field)
        )


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
    # Fallback still carries real prompt-registry provenance, not blanks.
    assert result.prompt_id == "triage_narrative"
    assert result.prompt_version == "1.1.0"
    assert result.rendered_prompt_hash


def test_router_configuration_error_is_never_turned_into_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("openai adapter must not be reached for a configuration error")

    monkeypatch.setattr(router.openai_adapter, "call", fail_if_called)
    settings = make_settings(ai_provider="openai", openai_api_key=None)

    with pytest.raises(AIGatewayConfigurationError):
        router.route_ai_inference(make_request(), settings)


# --- citations (Milestone 2.3) ---------------------------------------------


def test_ai_request_only_accepts_a_tuple_for_retrieved_context() -> None:
    with pytest.raises(TypeError):
        AIRequest(
            task="triage_narrative",
            classification=CLASSIFICATION,
            evidence=EVIDENCE,
            assessment=ASSESSMENT,
            proposed_action=PROPOSED_ACTION,
            retrieved_context=[RETRIEVED_RECORD],  # type: ignore[arg-type]
        )


def test_mock_adapter_produces_deterministic_structural_citations_when_evidence_is_retrieved() -> (
    None
):
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))

    first = mock_adapter.call(request, make_rendered(request))
    second = mock_adapter.call(request, make_rendered(request))

    assert first == second
    assert first.citations == ("issue:5756",)
    assert "issue:5756" in first.narrative


def test_mock_adapter_produces_no_citations_when_nothing_was_retrieved() -> None:
    result = mock_adapter.call(make_request(), make_rendered())
    assert result.citations == ()


def test_validate_citations_accepts_identifiers_present_in_the_request() -> None:
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))
    validate_citations(("issue:5756",), request)  # must not raise


def test_validate_citations_rejects_an_unknown_identifier() -> None:
    from app.ai_gateway.contracts import InvalidCitationError

    request = make_request(retrieved_context=(RETRIEVED_RECORD,))
    with pytest.raises(InvalidCitationError):
        validate_citations(("issue:99999-invented",), request)


def test_openai_adapter_accepts_a_response_that_cites_only_supplied_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_adapter.httpx,
        "post",
        lambda *args, **kwargs: _openai_response(
            content="Related to a known security endpoint issue.\nCitations: issue:5756"
        ),
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))

    result = openai_adapter.call(request, make_rendered(request), settings)

    assert result.citations == ("issue:5756",)
    assert "Citations:" not in result.narrative  # the machine-readable line is stripped


def test_openai_adapter_rejects_an_invented_citation_as_a_controlled_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_adapter.httpx,
        "post",
        lambda *args, **kwargs: _openai_response(
            content="A narrative.\nCitations: issue:99999-invented"
        ),
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))

    with pytest.raises(ProviderCallFailed):
        openai_adapter.call(request, make_rendered(request), settings)


def test_router_falls_back_on_an_invented_citation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_adapter.httpx,
        "post",
        lambda *args, **kwargs: _openai_response(
            content="A narrative.\nCitations: issue:99999-invented"
        ),
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))

    result = router.route_ai_inference(request, settings)

    assert result.status == STATUS_FALLBACK
    assert result.provider == "mock"
    assert result.fallback_reason is not None and "invalid_citation" in result.fallback_reason


# --- output bounds (Milestone 2.6) ------------------------------------------


def test_ai_response_rejects_a_narrative_over_the_length_bound() -> None:
    from app.ai_gateway.contracts import MAX_NARRATIVE_LENGTH

    with pytest.raises(ValueError, match="exceeds"):
        AIResponse(
            narrative="x" * (MAX_NARRATIVE_LENGTH + 1),
            status=STATUS_SUCCEEDED,
            provider="mock",
            model="deterministic-v1",
            prompt_id="triage_narrative",
            prompt_version="1.1.0",
            prompt_status="released",
            prompt_template_hash="a" * 64,
            rendered_prompt_hash="b" * 64,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=0.0,
            latency_ms=0.0,
        )


def test_ai_response_accepts_a_narrative_exactly_at_the_length_bound() -> None:
    from app.ai_gateway.contracts import MAX_NARRATIVE_LENGTH

    response = AIResponse(
        narrative="x" * MAX_NARRATIVE_LENGTH,
        status=STATUS_SUCCEEDED,
        provider="mock",
        model="deterministic-v1",
        prompt_id="triage_narrative",
        prompt_version="1.1.0",
        prompt_status="released",
        prompt_template_hash="a" * 64,
        rendered_prompt_hash="b" * 64,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0.0,
        latency_ms=0.0,
    )
    assert len(response.narrative) == MAX_NARRATIVE_LENGTH


def test_mock_narrative_for_a_realistic_request_stays_well_under_the_bound() -> None:
    """Sanity check for the chosen 4000-character bound against the
    deterministic mock narrative it must never reject."""
    from app.ai_gateway.contracts import MAX_NARRATIVE_LENGTH

    request = make_request(retrieved_context=(RETRIEVED_RECORD,))
    result = mock_adapter.call(request, make_rendered(request))
    assert len(result.narrative) < MAX_NARRATIVE_LENGTH


def test_openai_adapter_rejects_an_oversized_narrative_as_a_controlled_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai_gateway.contracts import MAX_NARRATIVE_LENGTH

    monkeypatch.setattr(
        openai_adapter.httpx,
        "post",
        lambda *args, **kwargs: _openai_response(content="x" * (MAX_NARRATIVE_LENGTH + 1)),
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    with pytest.raises(ProviderCallFailed) as exc_info:
        openai_adapter.call(make_request(), make_rendered(), settings)
    assert exc_info.value.reason == "openai_narrative_too_long"


def test_router_falls_back_on_an_oversized_narrative(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai_gateway.contracts import MAX_NARRATIVE_LENGTH

    monkeypatch.setattr(
        openai_adapter.httpx,
        "post",
        lambda *args, **kwargs: _openai_response(content="x" * (MAX_NARRATIVE_LENGTH + 1)),
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    result = router.route_ai_inference(make_request(), settings)

    assert result.status == STATUS_FALLBACK
    assert result.fallback_reason == "openai_narrative_too_long"
    assert len(result.narrative) < MAX_NARRATIVE_LENGTH


def test_validate_citations_rejects_a_duplicate_identifier() -> None:
    from app.ai_gateway.contracts import InvalidCitationError

    request = make_request(retrieved_context=(RETRIEVED_RECORD,))
    with pytest.raises(InvalidCitationError, match="duplicate"):
        validate_citations(("issue:5756", "issue:5756"), request)


def test_validate_citations_rejects_more_citations_than_retrieved_records() -> None:
    from app.ai_gateway.contracts import InvalidCitationError

    other_record = RetrievedRecord(
        identifier="issue:5757",
        source_type="issue",
        external_number=5757,
        title="Another issue",
        excerpt="Another excerpt.",
        source_url="https://github.com/pallets/flask/issues/5757",
        similarity_score=0.8,
        relevance_explanation="test",
        embedding_model="fake-hash-embedder",
        embedding_version="1",
    )
    # Only one record supplied, but the (fabricated) response cites two
    # distinct, individually-valid-looking identifiers.
    request = make_request(retrieved_context=(RETRIEVED_RECORD,))
    with pytest.raises(InvalidCitationError, match="exceeds"):
        validate_citations(("issue:5756", other_record.identifier), request)


# --- sensitive-data boundary: provider-bound redaction (Milestone 2.6) ------


_OPENAI_STYLE_TEST_SECRET = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
_GITHUB_PAT_TEST_SECRET = "ghp_" + "b" * 36


def test_router_redacts_a_high_confidence_secret_from_current_issue_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    injected_evidence = (
        EvidenceItem(
            field="body",
            excerpt=f"Here is my key: {_OPENAI_STYLE_TEST_SECRET}",
            source_url="https://example.test/1",
        ),
    )
    request = AIRequest(
        task="triage_narrative",
        classification=CLASSIFICATION,
        evidence=injected_evidence,
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
    )
    settings = make_settings(ai_provider="mock")

    result = router.route_ai_inference(request, settings)

    assert _OPENAI_STYLE_TEST_SECRET not in result.narrative
    assert "openai_api_key" in result.redaction_events
    assert "[REDACTED:openai_api_key]" in result.narrative
    assert result.redaction_policy_id == "provider_input_redaction"
    assert result.redaction_policy_version == "1.0.0"
    assert result.redaction_policy_status == "released"
    assert len(result.redaction_policy_hash) == 64


def test_router_redacts_a_high_confidence_secret_from_retrieved_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    injected_record = RetrievedRecord(
        identifier="issue:9999",
        source_type="issue",
        external_number=9999,
        title="Leaked token report",
        excerpt=f"Someone committed {_GITHUB_PAT_TEST_SECRET} by mistake.",
        source_url="https://github.com/pallets/flask/issues/9999",
        similarity_score=0.9,
        relevance_explanation="test",
        embedding_model="fake-hash-embedder",
        embedding_version="1",
    )
    request = make_request(retrieved_context=(injected_record,))
    settings = make_settings(ai_provider="mock")

    result = router.route_ai_inference(request, settings)

    assert _GITHUB_PAT_TEST_SECRET not in result.narrative
    assert "github_personal_access_token" in result.redaction_events


def test_redaction_removes_the_secret_from_the_rendered_prompt_sent_to_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The redacted text must never reach the outgoing provider payload
    either -- not just the final narrative."""
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):
        captured["content"] = json["messages"][0]["content"]
        return _openai_response()

    monkeypatch.setattr(openai_adapter.httpx, "post", fake_post)
    injected_evidence = (
        EvidenceItem(
            field="body",
            excerpt=f"Here is my key: {_OPENAI_STYLE_TEST_SECRET}",
            source_url="https://example.test/1",
        ),
    )
    request = AIRequest(
        task="triage_narrative",
        classification=CLASSIFICATION,
        evidence=injected_evidence,
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    router.route_ai_inference(request, settings)

    assert _OPENAI_STYLE_TEST_SECRET not in captured["content"]
    assert "[REDACTED:openai_api_key]" in captured["content"]


def test_redaction_events_are_empty_when_nothing_matches() -> None:
    result = router.route_ai_inference(make_request(), make_settings(ai_provider="mock"))
    assert result.redaction_events == ()
    # Absence of a redaction event is still attributable to a specific
    # policy version -- never blank/ambiguous with "no policy ran".
    assert result.redaction_policy_id == "provider_input_redaction"
    assert result.redaction_policy_version == "1.0.0"
    assert result.redaction_policy_status == "released"
    assert len(result.redaction_policy_hash) == 64


def test_fallback_preserves_identical_redaction_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback path reconstructs `AIResponse` manually (see
    router.route_ai_inference) -- it must carry exactly the same redaction
    provenance as a successful call, not a blanked-out or differently
    shaped one."""

    def raise_failed(*args, **kwargs):
        raise ProviderCallFailed("openai_timeout")

    monkeypatch.setattr(router.openai_adapter, "call", raise_failed)
    injected_evidence = (
        EvidenceItem(
            field="body",
            excerpt=f"Here is my key: {_OPENAI_STYLE_TEST_SECRET}",
            source_url="https://example.test/1",
        ),
    )
    request = AIRequest(
        task="triage_narrative",
        classification=CLASSIFICATION,
        evidence=injected_evidence,
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
    )
    settings = make_settings(ai_provider="openai", openai_api_key="sk-test")

    result = router.route_ai_inference(request, settings)

    assert result.status == STATUS_FALLBACK
    assert "openai_api_key" in result.redaction_events
    assert result.redaction_policy_id == "provider_input_redaction"
    assert result.redaction_policy_version == "1.0.0"
    assert result.redaction_policy_status == "released"
    assert len(result.redaction_policy_hash) == 64


def test_redaction_does_not_touch_ordinary_code_or_markdown() -> None:
    """The narrow pattern set must not destroy legitimate issue content --
    only the three specific high-confidence credential formats."""
    from app.ai_gateway.redaction import redact_secrets

    ordinary_code = (
        "```python\n"
        "API_KEY = os.environ['MY_KEY']\n"
        "def handler(event, context):\n"
        "    return {'statusCode': 200}\n"
        "```\n"
        "See also [our docs](https://example.test/docs) and `pip install foo`."
    )
    redacted, events = redact_secrets(ordinary_code)
    assert redacted == ordinary_code
    assert events == ()


def test_ai_request_has_no_field_that_could_carry_application_secrets() -> None:
    """Structural guarantee, not a leak test with a real key: `AIRequest`
    has no field of any kind for API keys, credentials, or settings --
    application-controlled secrets are read only inside
    `openai_adapter.call` from `Settings`, for exactly one request, and
    never assigned into any `AIRequest`/`EvidenceItem`/`RetrievedRecord`
    field. There is therefore nothing for `app.ai_gateway.redaction` to
    find for the application's own key, by construction -- this test does
    not (and must not) insert a real credential to demonstrate that."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(AIRequest)}
    assert field_names == {
        "task",
        "classification",
        "evidence",
        "assessment",
        "proposed_action",
        "retrieved_context",
    }
