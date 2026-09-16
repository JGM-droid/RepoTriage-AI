"""Section D: versioned adversarial-guardrail regression evaluation
(Milestone 2.6; see ADR 0012).

Kept deliberately separate from `app.evaluation.runner` (stages A + C) and
`app.evaluation.retrieval_policy` (stage B): those prove deterministic
triage correctness and citation/retrieval-selection-policy behavior on
well-formed input. This module proves that a fixed set of *adversarial*
inputs -- prompt-injection strings, fabricated citations, malformed/
oversized provider output, high-confidence secret patterns, oversized or
unusual issue content, provider timeouts -- degrade safely: deterministic
classification/severity/action are unaffected, no `HumanDecision` is ever
auto-created, unknown citations are rejected, malformed/oversized provider
output falls back, secret-shaped text is redacted before reaching a
provider, and prompt-registry provenance survives every one of those paths.

Like `app.evaluation.runner`, every case runs the real, unmodified
production code (`classify`/`assess`/`propose`/`human_review`,
`route_ai_inference`, and therefore also `app.ai_gateway.redaction`) --
never a reimplementation. `simulate_provider_failure`,
`simulated_openai_citations`, `simulated_openai_content`, and
`simulated_openai_malformed` drive the real `openai_adapter.call` path with
`httpx.post` monkeypatched before any request would be built; no real
network call is ever attempted.

IMPORTANT -- what this section does NOT prove: these are deterministic
checks against this application's own guardrail code (redaction, citation
validation, output bounds, fallback wiring), run only against the
deterministic mock provider and a monkeypatched simulation of the OpenAI
adapter's HTTP boundary. They do not exercise a real language model, and
passing every case here is not evidence that any real model will resist
every prompt-injection technique against it -- see
`ADVERSARIAL_ROBUSTNESS_DISCLAIMER` and ADR 0012.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import httpx

from app.ai_gateway.contracts import STATUS_FALLBACK, STATUS_SUCCEEDED, AIRequest
from app.ai_gateway.router import route_ai_inference
from app.config import Settings
from app.evaluation.canonical import canonical_json_hash
from app.evaluation.contracts import SuppliedRetrievedRecord
from app.retrieval.contracts import RetrievedRecord
from app.triage.rules import assess, classify, human_review, propose, retrieve_fixture_evidence

ADVERSARIAL_SCHEMA_VERSION = "1.0.0"

DEFAULT_ADVERSARIAL_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "evaluation" / "adversarial_cases.json"
)

ADVERSARIAL_ROBUSTNESS_DISCLAIMER = (
    "These deterministic adversarial checks validate application guardrails; "
    "they do not prove that every real model will resist every "
    "prompt-injection technique."
)

_SIMULATED_OPENAI_MODEL = "gpt-4o-mini-simulated"
# Visibly fake, test-only, never a real credential (see AGENTS.md and ADR
# 0012's redaction constraints) -- used only to prove the mock/simulated
# paths never echo a settings-level value back into narrative, without
# ever configuring a real key.
_SIMULATED_API_KEY = "sk-test-adversarial-eval-key-should-never-leak-000"


class _IssueLike:
    """Minimal stand-in for the `Issue` ORM model, identical in spirit to
    `app.evaluation.runner._IssueLike` -- no database is needed."""

    def __init__(self, *, title: str, body: str, state: str, source_url: str) -> None:
        self.title = title
        self.body = body
        self.state = state
        self.source_url = source_url


def _to_retrieved_record(supplied: SuppliedRetrievedRecord) -> RetrievedRecord:
    return RetrievedRecord(
        identifier=supplied.identifier,
        source_type="issue",
        external_number=None,
        title=supplied.title,
        excerpt=supplied.excerpt,
        source_url=supplied.source_url,
        similarity_score=supplied.similarity_score,
        relevance_explanation="Hand-supplied adversarial fixture data (Section D).",
        embedding_model="evaluation-fixture-supplied",
        embedding_version="n/a",
    )


def _simulated_openai_response(
    *,
    citations: tuple[str, ...] | None,
    raw_content: str | None,
    malformed: bool,
) -> httpx.Response:
    if malformed:
        return httpx.Response(200, json={"unexpected": "shape"})
    if raw_content is not None:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": raw_content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": _SIMULATED_OPENAI_MODEL,
            },
        )
    narrative = "A simulated grounded narrative for adversarial-evaluation purposes."
    citations_line = f"\nCitations: {', '.join(citations)}" if citations else ""
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": f"{narrative}{citations_line}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": _SIMULATED_OPENAI_MODEL,
        },
    )


@dataclass(frozen=True)
class AdversarialCase:
    case_id: str
    category: str
    rationale: str

    title: str
    body: str
    state: str = "open"
    source_url: str = "https://example.test/adversarial"

    # Deterministic-invariant expectations, exact-match like
    # app.evaluation.contracts.EvaluationCase -- proves the AI stage never
    # changes these regardless of adversarial content.
    expected_classification_label: str | None = None
    expected_severity: str | None = None
    expected_action: str | None = None

    supplied_retrieved_context: tuple[SuppliedRetrievedRecord, ...] = ()

    # Deterministic, offline AI-gateway simulation -- mutually exclusive.
    simulate_provider_failure: bool = False
    simulated_openai_citations: tuple[str, ...] | None = None
    simulated_openai_content: str | None = None
    simulated_openai_malformed: bool = False

    expect_status: str = STATUS_SUCCEEDED
    expected_fallback_reason_contains: str | None = None
    expect_redaction_pattern_names: tuple[str, ...] = ()
    forbidden_narrative_substrings: tuple[str, ...] = ()
    forbidden_citations: tuple[str, ...] = ()
    assert_human_review_boundary: bool = True

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("AdversarialCase.case_id must not be empty.")
        modes = [
            self.simulate_provider_failure,
            self.simulated_openai_citations is not None,
            self.simulated_openai_content is not None,
            self.simulated_openai_malformed,
        ]
        if sum(bool(m) for m in modes) > 1:
            raise ValueError(f"{self.case_id}: at most one AI-gateway simulation mode may be set.")


@dataclass(frozen=True)
class AdversarialFixture:
    schema_version: str
    fixture_version: str
    description: str
    cases: tuple[AdversarialCase, ...]

    def __post_init__(self) -> None:
        case_ids = [c.case_id for c in self.cases]
        if len(case_ids) != len(set(case_ids)):
            duplicates = sorted({cid for cid in case_ids if case_ids.count(cid) > 1})
            raise ValueError(f"Duplicate case_id(s) in adversarial fixture: {duplicates!r}")
        if not self.cases:
            raise ValueError("AdversarialFixture must contain at least one case.")


@dataclass(frozen=True)
class AdversarialResult:
    case_id: str
    category: str
    blocking_failures: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    redaction_events: tuple[str, ...] = field(default_factory=tuple)
    fallback_occurred: bool = False
    prompt_id: str = ""
    prompt_version: str = ""
    prompt_status: str = ""

    @property
    def passed(self) -> bool:
        return not self.blocking_failures


def _parse_retrieved_record(data: dict) -> SuppliedRetrievedRecord:
    return SuppliedRetrievedRecord(
        identifier=data["identifier"],
        title=data["title"],
        excerpt=data["excerpt"],
        source_url=data["source_url"],
        similarity_score=data["similarity_score"],
    )


def _parse_case(data: dict) -> AdversarialCase:
    return AdversarialCase(
        case_id=data["case_id"],
        category=data["category"],
        rationale=data["rationale"],
        title=data["title"],
        body=data["body"],
        state=data.get("state", "open"),
        source_url=data.get("source_url", "https://example.test/adversarial"),
        expected_classification_label=data.get("expected_classification_label"),
        expected_severity=data.get("expected_severity"),
        expected_action=data.get("expected_action"),
        supplied_retrieved_context=tuple(
            _parse_retrieved_record(r) for r in data.get("supplied_retrieved_context", [])
        ),
        simulate_provider_failure=data.get("simulate_provider_failure", False),
        simulated_openai_citations=(
            tuple(data["simulated_openai_citations"])
            if data.get("simulated_openai_citations") is not None
            else None
        ),
        simulated_openai_content=data.get("simulated_openai_content"),
        simulated_openai_malformed=data.get("simulated_openai_malformed", False),
        expect_status=data.get("expect_status", STATUS_SUCCEEDED),
        expected_fallback_reason_contains=data.get("expected_fallback_reason_contains"),
        expect_redaction_pattern_names=tuple(data.get("expect_redaction_pattern_names", [])),
        forbidden_narrative_substrings=tuple(data.get("forbidden_narrative_substrings", [])),
        forbidden_citations=tuple(data.get("forbidden_citations", [])),
        assert_human_review_boundary=data.get("assert_human_review_boundary", True),
    )


def load_adversarial_fixture(path: Path | None = None) -> tuple[AdversarialFixture, str]:
    """Returns (parsed fixture, canonical-JSON sha256 of its content) --
    mirrors `app.evaluation.cases.load_fixture`'s hash-pinning discipline,
    using the same platform-independent `app.evaluation.canonical` hash
    (see its module docstring for why raw-file-bytes hashing is unsafe
    here: it broke CI on this exact fixture)."""
    fixture_path = path or DEFAULT_ADVERSARIAL_FIXTURE_PATH
    raw_bytes = fixture_path.read_bytes()
    data = json.loads(raw_bytes)
    fixture = AdversarialFixture(
        schema_version=data["schema_version"],
        fixture_version=data["fixture_version"],
        description=data.get("description", ""),
        cases=tuple(_parse_case(c) for c in data["cases"]),
    )
    return fixture, canonical_json_hash(data)


def _run_ai_stage(case: AdversarialCase, request: AIRequest):
    simulating = (
        case.simulate_provider_failure
        or case.simulated_openai_citations is not None
        or case.simulated_openai_content is not None
        or case.simulated_openai_malformed
    )
    if not simulating:
        return route_ai_inference(request, Settings(ai_provider="mock"))

    settings = Settings(ai_provider="openai", openai_api_key=_SIMULATED_API_KEY)
    if case.simulate_provider_failure:
        with patch(
            "app.ai_gateway.openai_adapter.httpx.post",
            side_effect=httpx.TimeoutException("simulated adversarial-evaluation timeout"),
        ):
            return route_ai_inference(request, settings)

    response = _simulated_openai_response(
        citations=case.simulated_openai_citations,
        raw_content=case.simulated_openai_content,
        malformed=case.simulated_openai_malformed,
    )
    with patch("app.ai_gateway.openai_adapter.httpx.post", return_value=response):
        return route_ai_inference(request, settings)


def run_adversarial_case(case: AdversarialCase) -> AdversarialResult:
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
            f"got {classification.label!r} -- adversarial content must never change "
            "deterministic classification"
        )
    if case.expected_severity is not None and assessment.severity != case.expected_severity:
        blocking_failures.append(
            f"assessment.severity: expected {case.expected_severity!r}, got {assessment.severity!r}"
        )
    if case.expected_action is not None and proposal.action != case.expected_action:
        blocking_failures.append(
            f"proposed_action.action: expected {case.expected_action!r}, got {proposal.action!r}"
        )

    if case.assert_human_review_boundary and (
        review.recommendation_status != "proposed"
        or review.human_review_status != "awaiting_human_review"
        or review.decision is not None
    ):
        blocking_failures.append(
            "human_review boundary violated: adversarial input must never auto-create a "
            f"HumanDecision; got recommendation_status={review.recommendation_status!r}, "
            f"human_review_status={review.human_review_status!r}, decision={review.decision!r}"
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

    if ai_response.status != case.expect_status:
        blocking_failures.append(
            f"ai_response.status: expected {case.expect_status!r}, got {ai_response.status!r}"
        )
    if case.expected_fallback_reason_contains is not None and (
        ai_response.fallback_reason is None
        or case.expected_fallback_reason_contains not in ai_response.fallback_reason
    ):
        blocking_failures.append(
            f"fallback_reason: expected to contain {case.expected_fallback_reason_contains!r}, "
            f"got {ai_response.fallback_reason!r}"
        )

    actual_redaction_names = set(ai_response.redaction_events)
    expected_redaction_names = set(case.expect_redaction_pattern_names)
    if actual_redaction_names != expected_redaction_names:
        blocking_failures.append(
            f"redaction_events: expected pattern names {sorted(expected_redaction_names)!r}, "
            f"got {sorted(actual_redaction_names)!r}"
        )

    for substring in case.forbidden_narrative_substrings:
        if substring in ai_response.narrative:
            blocking_failures.append(
                f"forbidden substring {substring!r} is present in narrative -- a secret or "
                "raw injected credential must never survive into persisted narrative"
            )

    for identifier in case.forbidden_citations:
        if identifier in ai_response.citations:
            blocking_failures.append(f"forbidden citation {identifier!r} present in citations")

    # AIResponse.__post_init__ already enforces the narrative length bound
    # for every constructed response (see app.ai_gateway.contracts) -- this
    # is a belt-and-suspenders re-check specific to this section's report,
    # not a second, independent implementation of the bound.
    from app.ai_gateway.contracts import MAX_NARRATIVE_LENGTH

    if len(ai_response.narrative) > MAX_NARRATIVE_LENGTH:
        blocking_failures.append(
            f"narrative length {len(ai_response.narrative)} exceeds {MAX_NARRATIVE_LENGTH}"
        )

    return AdversarialResult(
        case_id=case.case_id,
        category=case.category,
        blocking_failures=tuple(blocking_failures),
        warnings=tuple(warnings),
        redaction_events=ai_response.redaction_events,
        fallback_occurred=(ai_response.status == STATUS_FALLBACK),
        prompt_id=ai_response.prompt_id,
        prompt_version=ai_response.prompt_version,
        prompt_status=ai_response.prompt_status,
    )


def run_all_adversarial_cases(fixture: AdversarialFixture) -> tuple[AdversarialResult, ...]:
    return tuple(run_adversarial_case(case) for case in fixture.cases)
