"""Executes one `EvaluationCase` against the real, unmodified production
code: `app.triage.rules.classify/assess/propose/human_review`, `app.
retrieval.stage.build_query_text` and `app.retrieval.query_terms.
has_sufficient_query_content` (pure, no database/model needed), and `app.
ai_gateway.router.route_ai_inference` (the real router, real prompt
registry, real citation validation).

What this module does NOT do, and must never be described as doing:
retrieval selection. `case.supplied_retrieved_context` is a hand-built
stand-in for a `RetrievedRecord` tuple -- this module never calls
`app.retrieval.service.retrieve_related_evidence` or its
`_confidence_decision`/`_shared_discriminative_terms` selection functions,
never embeds text, and never queries pgvector. It proves citation
validation and AI-gateway wiring given an already-decided retrieval
result, not whether that result was the *correct* one to select. The
retrieval-*selection*-policy check lives entirely in
`app.evaluation.retrieval_policy`, which does call the real selection
functions, replayed against frozen calibration evidence.

Deterministic, offline AI-gateway simulation: `simulate_provider_failure`
and `simulated_openai_citations` drive `Settings(ai_provider="openai")`
through the real `openai_adapter.call` path with `httpx.post` monkeypatched
-- no real network call is ever attempted, `httpx.post` is replaced before
the router runs. This is deliberate production-code technique for this
harness, not test code smuggled into `app/`: it is the only way to
exercise the real fallback/citation-rejection paths deterministically and
offline.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from app.ai_gateway.contracts import AIRequest, InvalidCitationError, validate_citations
from app.ai_gateway.router import route_ai_inference
from app.config import Settings
from app.evaluation.contracts import CaseResult, EvaluationCase
from app.retrieval.contracts import RetrievedRecord
from app.retrieval.query_terms import has_sufficient_query_content
from app.retrieval.stage import build_query_text
from app.triage.rules import assess, classify, human_review, propose, retrieve_fixture_evidence

_SIMULATED_OPENAI_MODEL = "gpt-4o-mini-simulated"
# A visibly fake placeholder, matching the existing test-suite convention
# (tests/test_ai_gateway.py); httpx.post is monkeypatched before any
# request is built, so this value is never sent anywhere.
_SIMULATED_API_KEY = "sk-test"


class _IssueLike:
    """Minimal stand-in for the `Issue` ORM model: the deterministic
    triage rules and `build_query_text` only read these four attributes,
    so no database is needed to run an evaluation case."""

    def __init__(self, *, title: str, body: str, state: str, source_url: str) -> None:
        self.title = title
        self.body = body
        self.state = state
        self.source_url = source_url


def _to_retrieved_record(supplied) -> RetrievedRecord:
    return RetrievedRecord(
        identifier=supplied.identifier,
        source_type="issue",
        external_number=None,
        title=supplied.title,
        excerpt=supplied.excerpt,
        source_url=supplied.source_url,
        similarity_score=supplied.similarity_score,
        relevance_explanation=(
            "Hand-supplied by this evaluation case's fixture data to drive citation/AI-gateway "
            "wiring deterministically -- this is NOT the output of a real retrieval-selection "
            "decision; see app.evaluation.retrieval_policy for that."
        ),
        embedding_model="evaluation-fixture-supplied",
        embedding_version="n/a",
    )


def _simulated_openai_response(citations: tuple[str, ...]) -> httpx.Response:
    narrative = "A simulated grounded narrative for evaluation purposes."
    citations_line = f"\nCitations: {', '.join(citations)}" if citations else ""
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": f"{narrative}{citations_line}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": _SIMULATED_OPENAI_MODEL,
        },
    )


def _run_ai_stage(case: EvaluationCase, request: AIRequest):
    if case.simulate_provider_failure:
        settings = Settings(ai_provider="openai", openai_api_key=_SIMULATED_API_KEY)
        with patch(
            "app.ai_gateway.openai_adapter.httpx.post",
            side_effect=httpx.TimeoutException("simulated evaluation timeout (no real network)"),
        ):
            return route_ai_inference(request, settings)

    if case.simulated_openai_citations is not None:
        settings = Settings(ai_provider="openai", openai_api_key=_SIMULATED_API_KEY)
        response = _simulated_openai_response(case.simulated_openai_citations)
        with patch("app.ai_gateway.openai_adapter.httpx.post", return_value=response):
            return route_ai_inference(request, settings)

    settings = Settings(ai_provider="mock")
    return route_ai_inference(request, settings)


def run_case(case: EvaluationCase) -> CaseResult:
    blocking_failures: list[str] = []
    warnings: list[str] = []

    issue = _IssueLike(
        title=case.title, body=case.body, state=case.state, source_url=case.source_url
    )
    classification = classify(issue)
    evidence = retrieve_fixture_evidence(issue, classification)
    assessment = assess(issue, classification, evidence)
    proposal = propose(issue, classification, assessment)
    review = human_review(proposal)

    if (
        case.expected_classification_label is not None
        and classification.label != case.expected_classification_label
    ):
        blocking_failures.append(
            f"classification.label: expected {case.expected_classification_label!r}, "
            f"got {classification.label!r}"
        )
    if (
        case.expected_matched_rule is not None
        and classification.matched_rule != case.expected_matched_rule
    ):
        blocking_failures.append(
            f"classification.matched_rule: expected {case.expected_matched_rule!r}, "
            f"got {classification.matched_rule!r}"
        )
    if case.expected_severity is not None and assessment.severity != case.expected_severity:
        blocking_failures.append(
            f"assessment.severity: expected {case.expected_severity!r}, got {assessment.severity!r}"
        )
    if case.expected_action is not None and proposal.action != case.expected_action:
        blocking_failures.append(
            f"proposed_action.action: expected {case.expected_action!r}, got {proposal.action!r}"
        )

    if case.assert_human_review_boundary:
        if (
            review.recommendation_status != "proposed"
            or review.human_review_status != "awaiting_human_review"
            or review.decision is not None
        ):
            blocking_failures.append(
                "human_review boundary violated: expected recommendation_status='proposed', "
                "human_review_status='awaiting_human_review', decision=None; got "
                f"{review.recommendation_status!r}/{review.human_review_status!r}/{review.decision!r}"
            )

    if case.expect_sufficient_query is not None:
        query_text = build_query_text(issue, classification)
        actual_sufficient = has_sufficient_query_content(query_text)
        if actual_sufficient != case.expect_sufficient_query:
            blocking_failures.append(
                f"has_sufficient_query_content: expected {case.expect_sufficient_query!r}, "
                f"got {actual_sufficient!r} for query_text={query_text[:80]!r}"
            )

    retrieved_context = tuple(_to_retrieved_record(r) for r in case.supplied_retrieved_context)
    request = AIRequest(
        task="triage_narrative",
        classification=classification,
        evidence=evidence,
        assessment=assessment,
        proposed_action=proposal,
        retrieved_context=retrieved_context,
    )

    ai_response = _run_ai_stage(case, request)

    for identifier in case.required_citations:
        if identifier not in ai_response.citations:
            blocking_failures.append(
                f"required citation {identifier!r} is missing from result citations "
                f"{ai_response.citations!r}"
            )

    for identifier in case.forbidden_citations:
        if identifier in ai_response.citations:
            blocking_failures.append(
                f"forbidden citation {identifier!r} is present in result citations"
            )
        # Actively prove the safety mechanism itself, not just that this
        # run happened not to cite it: if a provider ever claimed this
        # identifier, validate_citations must reject it.
        try:
            validate_citations((identifier,), request)
        except InvalidCitationError:
            pass  # correctly rejected -- the expected outcome
        else:
            blocking_failures.append(
                f"forbidden citation {identifier!r} was NOT rejected by validate_citations "
                "-- the safety mechanism this case exists to prove is broken"
            )

    expected_status = (
        "fallback"
        if (case.simulate_provider_failure or case.simulated_openai_citations is not None)
        else "succeeded"
    )
    if ai_response.status != expected_status:
        blocking_failures.append(
            f"ai_response.status: expected {expected_status!r}, got {ai_response.status!r}"
        )

    # Mock cost is always exactly zero: the router's fallback path also
    # always calls the mock adapter, so this invariant holds unconditionally
    # for every deterministic-CI case, never just the non-simulated ones.
    if ai_response.estimated_cost_usd != 0.0:
        blocking_failures.append(
            f"estimated_cost_usd: expected 0.0, got {ai_response.estimated_cost_usd!r}"
        )

    for fragment in case.required_narrative_fragments:
        if fragment not in ai_response.narrative:
            warnings.append(f"narrative_rule_violation: missing expected fragment {fragment!r}")
    for fragment in case.forbidden_narrative_fragments:
        if fragment in ai_response.narrative:
            warnings.append(f"narrative_rule_violation: forbidden fragment {fragment!r} present")

    citation_relevant = bool(
        case.required_citations
        or case.forbidden_citations
        or case.simulated_openai_citations is not None
    )

    # A case marked `blocking: false` (e.g. a known, disclosed limitation)
    # never gates the regression check: whatever it would have failed on is
    # demoted to an informational warning instead, so the case stays
    # visible in every report without ever blocking CI.
    if not case.blocking:
        warnings = [*warnings, *(f"non_blocking_case: {f}" for f in blocking_failures)]
        blocking_failures = []

    return CaseResult(
        case_id=case.case_id,
        categories=case.categories,
        known_limitation=case.known_limitation,
        citation_relevant=citation_relevant,
        blocking_failures=tuple(blocking_failures),
        warnings=tuple(warnings),
        provider=ai_response.provider,
        model=ai_response.model,
        prompt_id=ai_response.prompt_id,
        prompt_version=ai_response.prompt_version,
        prompt_status=ai_response.prompt_status,
        prompt_template_hash=ai_response.prompt_template_hash,
        rendered_prompt_hash=ai_response.rendered_prompt_hash,
        latency_ms=ai_response.latency_ms,
        input_tokens=ai_response.input_tokens,
        output_tokens=ai_response.output_tokens,
        estimated_cost_usd=ai_response.estimated_cost_usd,
        citations=ai_response.citations,
    )


def run_all(cases) -> tuple[CaseResult, ...]:
    return tuple(run_case(case) for case in cases)
