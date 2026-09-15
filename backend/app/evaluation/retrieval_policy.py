"""Zero-network retrieval-*policy* regression evaluation (Milestone 2.5
correction; see ADR 0011).

This is deliberately separate from `app.evaluation.runner`'s narrative/
citation cases. Those cases supply a hand-built `retrieved_context`
directly to the AI gateway and prove citation validation/fallback
behavior -- they never call the retrieval-selection policy and must never
be described as testing retrieval quality.

This module does call the real, current selection functions --
`app.retrieval.service._confidence_decision` and
`_shared_discriminative_terms` -- the same functions
`retrieve_related_evidence` calls internally to decide accept/reject.
It replays them against previously measured, frozen real-BGE similarity
scores (never recomputed) and the bounded, real query/candidate text that
produced the recorded outcome (see `tests/calibration/
bge_similarity_calibration.json`). It never embeds text, queries
pgvector, opens a database session, or downloads a model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.retrieval.contracts import RetrievalRequest
from app.retrieval.service import _confidence_decision  # noqa: SLF001 -- deliberate direct reuse

DEFAULT_CALIBRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "calibration"
    / "bge_similarity_calibration.json"
)

# Never used for filtering (RetrievalRequest.repository_id/exclude_issue_id
# are not read by _confidence_decision), only to satisfy the dataclass's
# required fields.
_PLACEHOLDER_REPOSITORY_ID = UUID("00000000-0000-0000-0000-000000000000")
_PLACEHOLDER_ISSUE_ID = UUID("00000000-0000-0000-0000-000000000001")


class _ChunkLike:
    """Minimal stand-in for `RetrievalChunk`: `_confidence_decision` and
    `_shared_discriminative_terms` only ever read `.content`."""

    def __init__(self, content: str) -> None:
        self.content = content


@dataclass(frozen=True)
class RetrievalPolicyCase:
    case_id: str
    query_source: str
    target: str
    rationale: str
    frozen_similarity_score: float
    query_text: str
    candidate_text: str
    expected_decision: str  # "accepted" | "rejected"
    known_limitation: bool = False


@dataclass(frozen=True)
class RetrievalPolicyFixture:
    calibration_schema_version: str
    min_similarity: float
    high_confidence_similarity: float
    cases: tuple[RetrievalPolicyCase, ...]


@dataclass(frozen=True)
class RetrievalPolicyResult:
    case_id: str
    known_limitation: bool
    expected_decision: str
    frozen_similarity_score: float
    actual_tier: str
    actual_decision: str
    shared_discriminative_terms: tuple[str, ...]
    blocking_failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.blocking_failures


def load_retrieval_policy_fixture(path: Path | None = None) -> RetrievalPolicyFixture:
    calibration_path = path or DEFAULT_CALIBRATION_PATH
    data = json.loads(calibration_path.read_text(encoding="utf-8"))
    fp = data["false_positive_regression_examples"]
    shared_query_text = fp["query_text"]

    cases = tuple(
        RetrievalPolicyCase(
            case_id=(
                f"retrieval_policy_{example['query_source'].replace(':', '_')}"
                f"_to_{example['target'].replace(':', '_')}"
            ),
            query_source=example["query_source"],
            target=example["target"],
            rationale=example["description"],
            frozen_similarity_score=example["score"],
            query_text=shared_query_text,
            candidate_text=example["candidate_text"],
            expected_decision=example["decision"],
            known_limitation=example.get("known_limitation", False),
        )
        for example in fp["examples"]
    )

    return RetrievalPolicyFixture(
        calibration_schema_version=data["calibration_schema_version"],
        min_similarity=data["chosen_threshold"],
        high_confidence_similarity=data["high_confidence_similarity"],
        cases=cases,
    )


def run_retrieval_policy_case(
    case: RetrievalPolicyCase, min_similarity: float, high_confidence_similarity: float
) -> RetrievalPolicyResult:
    """Replays the real `_confidence_decision` against this case's frozen
    score and bounded text -- proving the *current* selection policy still
    reaches the recorded outcome, not just that the outcome was once true."""
    request = RetrievalRequest(
        repository_id=_PLACEHOLDER_REPOSITORY_ID,
        exclude_issue_id=_PLACEHOLDER_ISSUE_ID,
        query_text=case.query_text,
        max_results=3,
        max_excerpt_chars=320,
        min_similarity=min_similarity,
        high_confidence_similarity=high_confidence_similarity,
    )
    chunk = _ChunkLike(case.candidate_text)

    accepted, tier, shared_terms = _confidence_decision(
        chunk, case.frozen_similarity_score, request
    )
    actual_decision = "accepted" if accepted else "rejected"

    blocking_failures: list[str] = []
    warnings: list[str] = []
    failure = None
    if actual_decision != case.expected_decision:
        failure = (
            f"retrieval policy decision for {case.query_source} -> {case.target}: "
            f"expected {case.expected_decision!r} at frozen score "
            f"{case.frozen_similarity_score}, got {actual_decision!r} (tier={tier or 'none'!r}, "
            f"shared_discriminative_terms={sorted(shared_terms)!r})"
        )

    if case.known_limitation:
        if failure is not None:
            warnings.append(f"non_blocking_case: {failure}")
    elif failure is not None:
        blocking_failures.append(failure)

    return RetrievalPolicyResult(
        case_id=case.case_id,
        known_limitation=case.known_limitation,
        expected_decision=case.expected_decision,
        frozen_similarity_score=case.frozen_similarity_score,
        actual_tier=tier or "none",
        actual_decision=actual_decision,
        shared_discriminative_terms=tuple(sorted(shared_terms)),
        blocking_failures=tuple(blocking_failures),
        warnings=tuple(warnings),
    )


def run_all_retrieval_policy_cases(
    fixture: RetrievalPolicyFixture,
) -> tuple[RetrievalPolicyResult, ...]:
    return tuple(
        run_retrieval_policy_case(case, fixture.min_similarity, fixture.high_confidence_similarity)
        for case in fixture.cases
    )
