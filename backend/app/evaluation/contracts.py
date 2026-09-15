"""Typed contracts for the fixed evaluation set, its execution results, and
the baseline/candidate comparison.

An `EvaluationCase` is a bounded, deterministic scenario run through the
real `classify`/`assess`/`propose`/`human_review` functions and the real
AI gateway (`route_ai_inference`) -- never a re-implementation of that
logic. Expectations are exact-match where the underlying logic is
deterministic; retrieval *semantic quality* is never re-measured here (see
`app.evaluation.calibration_summary`) -- `supplied_retrieved_context` is a
hand-supplied stand-in used only to drive the AI-gateway/citation-
validation *wiring* deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_VERSION = "1.0.0"

_VALID_CATEGORIES = frozenset(
    {
        "security_concern",
        "bug",
        "documentation_support",
        "closed_needs_verification",
        "duplicate_near_duplicate",
        "insufficient_query",
        "no_qualifying_evidence",
        "expected_citation",
        "forbidden_citation",
        "fallback_behavior",
        "citation_validity",
        "narrative_requirements",
        "human_decision_boundary",
        "regression",
        "known_limitation",
    }
)


@dataclass(frozen=True)
class SuppliedRetrievedRecord:
    """A hand-supplied stand-in for one `app.retrieval.contracts.
    RetrievedRecord`, used only to drive the AI-gateway/citation-validation
    wiring deterministically -- not a re-measurement of real retrieval
    quality (see the frozen calibration fixture for that)."""

    identifier: str
    title: str
    excerpt: str
    source_url: str
    similarity_score: float


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    categories: tuple[str, ...]
    rationale: str
    blocking: bool

    # Issue-like input. `classify`/`assess`/`propose`/`retrieve_fixture_
    # evidence` only need `.title`/`.body`/`.state`/`.source_url`.
    issue_external_number: int
    title: str
    body: str
    state: str
    source_url: str

    # Exact deterministic expectations (None = not checked for this case).
    expected_classification_label: str | None = None
    expected_matched_rule: str | None = None
    expected_severity: str | None = None
    expected_action: str | None = None

    # Retrieval-wiring (not quality) expectations.
    supplied_retrieved_context: tuple[SuppliedRetrievedRecord, ...] = ()
    required_citations: tuple[str, ...] = ()
    forbidden_citations: tuple[str, ...] = ()

    # Query-sufficiency gate: pure, deterministic, no DB/model involved.
    expect_sufficient_query: bool | None = None

    # Deterministic, offline AI-gateway simulation (see app.evaluation.runner):
    # `simulate_provider_failure` forces a simulated OpenAI timeout (no real
    # network call is ever made -- httpx.post is monkeypatched before any
    # request would be sent); `simulated_openai_citations`, if not None,
    # simulates a successful OpenAI response citing exactly these
    # identifiers (which may include ones never supplied, to prove
    # citation validation rejects them).
    simulate_provider_failure: bool = False
    simulated_openai_citations: tuple[str, ...] | None = None

    # Narrative content checks against the *mock* adapter's deterministic
    # template only -- simple substring presence/absence, never a claim of
    # semantic factuality measurement. Violations are always warnings, per
    # ADR 0011, never blocking.
    required_narrative_fragments: tuple[str, ...] = ()
    forbidden_narrative_fragments: tuple[str, ...] = ()

    # Cross-cutting boundary check: the workflow's fifth stage must never
    # auto-create a HumanDecision or otherwise leave "proposed"/"awaiting
    # human review".
    assert_human_review_boundary: bool = False

    # Documentation-only: this case exists to keep a known, disclosed
    # limitation visible in every report. It is never blocking and never
    # asserts a specific retrieval citation outcome for the limitation
    # itself -- see ADR 0009/ADR-referenced Milestone 2.3 record.
    known_limitation: bool = False

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("EvaluationCase.case_id must not be empty.")
        if not self.categories:
            raise ValueError(f"{self.case_id}: at least one category is required.")
        unknown = set(self.categories) - _VALID_CATEGORIES
        if unknown:
            raise ValueError(f"{self.case_id}: unknown categor(y/ies): {sorted(unknown)!r}")
        if not self.rationale.strip():
            raise ValueError(f"{self.case_id}: rationale must not be empty.")
        if self.simulate_provider_failure and self.simulated_openai_citations is not None:
            raise ValueError(
                f"{self.case_id}: simulate_provider_failure and simulated_openai_citations "
                "are mutually exclusive simulation modes."
            )


@dataclass(frozen=True)
class EvaluationFixture:
    schema_version: str
    fixture_version: str
    description: str
    cases: tuple[EvaluationCase, ...]

    def __post_init__(self) -> None:
        case_ids = [c.case_id for c in self.cases]
        if len(case_ids) != len(set(case_ids)):
            duplicates = sorted({cid for cid in case_ids if case_ids.count(cid) > 1})
            raise ValueError(f"Duplicate case_id(s) in fixture: {duplicates!r}")
        if not self.cases:
            raise ValueError("EvaluationFixture must contain at least one case.")


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    categories: tuple[str, ...]
    known_limitation: bool
    citation_relevant: bool = False
    blocking_failures: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # Provenance (Milestone 2.4 fields, reused verbatim -- never
    # recomputed independently here).
    provider: str = ""
    model: str = ""
    prompt_id: str = ""
    prompt_version: str = ""
    prompt_status: str = ""
    prompt_template_hash: str = ""
    rendered_prompt_hash: str = ""

    # Observational only -- never gates pass/fail.
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    citations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.blocking_failures
