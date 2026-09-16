"""Loads and validates the versioned, checked-in evaluation fixture.

The canonical form is JSON (`backend/tests/evaluation/fixed_cases.json`)
so it is reviewable in a normal git diff, exactly like the retrieval
calibration fixture. This module only parses and validates it into typed
`EvaluationCase` objects -- `app.evaluation.runner` does the actual
execution.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.canonical import canonical_json_hash
from app.evaluation.contracts import EvaluationCase, EvaluationFixture, SuppliedRetrievedRecord

DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "evaluation" / "fixed_cases.json"
)


def fixture_content_hash(raw_bytes: bytes) -> str:
    """SHA-256 of the fixture's canonical JSON form (see
    `app.evaluation.canonical`) -- platform-independent: line endings,
    indentation, key order, and trailing whitespace never change this; any
    real change to the fixture's content always does. Deliberate: the
    baseline comparison must treat a genuine fixture change as something
    requiring an intentional, reviewed baseline update, never a silent
    pass-through -- but must never itself become a platform-dependent
    false positive (see `app.evaluation.canonical`'s module docstring for
    the CI failure this corrected)."""
    return canonical_json_hash(json.loads(raw_bytes))


def _parse_retrieved_record(data: dict) -> SuppliedRetrievedRecord:
    return SuppliedRetrievedRecord(
        identifier=data["identifier"],
        title=data["title"],
        excerpt=data["excerpt"],
        source_url=data["source_url"],
        similarity_score=data["similarity_score"],
    )


def _parse_case(data: dict) -> EvaluationCase:
    return EvaluationCase(
        case_id=data["case_id"],
        categories=tuple(data["categories"]),
        rationale=data["rationale"],
        blocking=data.get("blocking", True),
        issue_external_number=data["issue_external_number"],
        title=data["title"],
        body=data["body"],
        state=data.get("state", "closed"),
        source_url=data["source_url"],
        expected_classification_label=data.get("expected_classification_label"),
        expected_matched_rule=data.get("expected_matched_rule"),
        expected_severity=data.get("expected_severity"),
        expected_action=data.get("expected_action"),
        supplied_retrieved_context=tuple(
            _parse_retrieved_record(r) for r in data.get("supplied_retrieved_context", [])
        ),
        required_citations=tuple(data.get("required_citations", [])),
        forbidden_citations=tuple(data.get("forbidden_citations", [])),
        expect_sufficient_query=data.get("expect_sufficient_query"),
        simulate_provider_failure=data.get("simulate_provider_failure", False),
        simulated_openai_citations=(
            tuple(data["simulated_openai_citations"])
            if data.get("simulated_openai_citations") is not None
            else None
        ),
        required_narrative_fragments=tuple(data.get("required_narrative_fragments", [])),
        forbidden_narrative_fragments=tuple(data.get("forbidden_narrative_fragments", [])),
        assert_human_review_boundary=data.get("assert_human_review_boundary", False),
        known_limitation=data.get("known_limitation", False),
    )


def load_fixture(path: Path | None = None) -> tuple[EvaluationFixture, str]:
    """Returns (parsed fixture, sha256 of the raw file bytes)."""
    fixture_path = path or DEFAULT_FIXTURE_PATH
    raw_bytes = fixture_path.read_bytes()
    data = json.loads(raw_bytes)

    fixture = EvaluationFixture(
        schema_version=data["schema_version"],
        fixture_version=data["fixture_version"],
        description=data.get("description", ""),
        cases=tuple(_parse_case(c) for c in data["cases"]),
    )
    return fixture, fixture_content_hash(raw_bytes)
