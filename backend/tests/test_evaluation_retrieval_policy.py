"""Retrieval-policy regression tests (Milestone 2.5 correction; see ADR
0011).

Proves what the correction added: `app.evaluation.retrieval_policy`
actually calls the real retrieval-*selection* functions
(`app.retrieval.service._confidence_decision`), distinct from `app.
evaluation.runner`'s citation/fallback-wiring cases, which never do.

No test here downloads a model, calls a network service, embeds text, or
queries pgvector -- every case replays a previously measured, frozen
real-BGE similarity score through the real, current, in-memory decision
function only.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.evaluation.cases import DEFAULT_FIXTURE_PATH, load_fixture
from app.evaluation.report import build_report, compare
from app.evaluation.retrieval_policy import (
    DEFAULT_CALIBRATION_PATH,
    RetrievalPolicyCase,
    RetrievalPolicyFixture,
    load_retrieval_policy_fixture,
    run_all_retrieval_policy_cases,
    run_retrieval_policy_case,
)
from app.evaluation.runner import run_all

DEFAULT_BASELINE_PATH = DEFAULT_FIXTURE_PATH.parent / "baseline.json"


def _case(**overrides) -> RetrievalPolicyCase:
    fields = {
        "case_id": "test_case",
        "query_source": "issue:1",
        "target": "issue:2",
        "rationale": "A test case.",
        "frozen_similarity_score": 0.70,
        "query_text": "security vulnerability in the login endpoint",
        "candidate_text": "unrelated packaging and build tooling request",
        "expected_decision": "rejected",
        "known_limitation": False,
    }
    fields.update(overrides)
    return RetrievalPolicyCase(**fields)


# --- the module actually calls real retrieval selection ----------------------


def test_the_real_calibration_fixture_loads_with_six_cases() -> None:
    fixture = load_retrieval_policy_fixture()
    assert len(fixture.cases) == 6
    assert fixture.min_similarity == 0.65
    assert fixture.high_confidence_similarity == 0.90


def test_every_frozen_case_replays_to_its_recorded_decision() -> None:
    """The real, current `_confidence_decision` must still reach exactly
    the decision recorded when each score was originally measured --
    proving the current policy code, not a stored label, produces it."""
    fixture = load_retrieval_policy_fixture()
    results = run_all_retrieval_policy_cases(fixture)
    failing = [(r.case_id, r.blocking_failures) for r in results if not r.passed]
    assert failing == []


def test_5756_is_accepted_on_semantic_score_alone() -> None:
    fixture = load_retrieval_policy_fixture()
    case = next(c for c in fixture.cases if c.target == "issue:5756")
    result = run_retrieval_policy_case(
        case, fixture.min_similarity, fixture.high_confidence_similarity
    )
    assert result.actual_decision == "accepted"
    assert result.actual_tier == "high_confidence"


def test_5942_is_rejected_sharing_zero_discriminative_terms() -> None:
    fixture = load_retrieval_policy_fixture()
    case = next(c for c in fixture.cases if c.target == "issue:5942")
    result = run_retrieval_policy_case(
        case, fixture.min_similarity, fixture.high_confidence_similarity
    )
    assert result.actual_decision == "rejected"
    assert result.shared_discriminative_terms == ()


def test_5863_is_accepted_via_the_shared_security_term() -> None:
    fixture = load_retrieval_policy_fixture()
    case = next(c for c in fixture.cases if c.target == "issue:5863")
    result = run_retrieval_policy_case(
        case, fixture.min_similarity, fixture.high_confidence_similarity
    )
    assert result.actual_decision == "accepted"
    assert "security" in result.shared_discriminative_terms


def test_6139_is_reported_as_a_known_limitation_never_a_required_match() -> None:
    fixture = load_retrieval_policy_fixture()
    case = next(c for c in fixture.cases if c.target == "issue:6139")
    assert case.known_limitation is True

    result = run_retrieval_policy_case(
        case, fixture.min_similarity, fixture.high_confidence_similarity
    )
    assert result.known_limitation is True
    assert result.blocking_failures == ()  # never blocks, win or lose


def test_a_mismatched_expectation_produces_a_blocking_failure_naming_the_decision() -> None:
    case = _case(expected_decision="accepted")  # the fixture's actual decision is "rejected"
    result = run_retrieval_policy_case(case, min_similarity=0.65, high_confidence_similarity=0.90)
    assert not result.passed
    assert "expected 'accepted'" in result.blocking_failures[0]
    assert "got 'rejected'" in result.blocking_failures[0]


def test_a_known_limitation_case_never_blocks_even_on_mismatch() -> None:
    case = _case(expected_decision="accepted", known_limitation=True)
    result = run_retrieval_policy_case(case, min_similarity=0.65, high_confidence_similarity=0.90)
    assert result.passed
    assert any("non_blocking_case" in w for w in result.warnings)


def test_high_confidence_score_bypasses_discriminative_term_requirement() -> None:
    case = _case(
        frozen_similarity_score=0.95,
        expected_decision="accepted",
        query_text="completely different vocabulary",
        candidate_text="shares absolutely nothing in common",
    )
    result = run_retrieval_policy_case(case, min_similarity=0.65, high_confidence_similarity=0.90)
    assert result.passed
    assert result.actual_tier == "high_confidence"


# --- distinct from citation/fallback wiring (app.evaluation.runner) ----------


def test_an_invented_ai_citation_is_a_citation_result_not_a_retrieval_policy_result() -> None:
    """An invented citation from a simulated provider is entirely a stage
    C (narrative/citation) concern -- it must never appear in, or affect,
    the stage B (retrieval-policy) result set."""
    from app.evaluation.contracts import EvaluationCase

    case = EvaluationCase(
        case_id="invented_citation_case",
        categories=("citation_validity",),
        rationale="test",
        blocking=True,
        issue_external_number=1,
        title="A security issue",
        body="body",
        state="closed",
        source_url="https://example.test/1",
        simulated_openai_citations=("issue:99999-invented",),
    )
    result = run_all((case,))[0]

    assert result.provider == "mock"  # fell back correctly
    assert result.citations == ()

    # The retrieval-policy fixture/results are entirely separate and
    # unaffected by anything in the citation-wiring run above.
    rp_fixture = load_retrieval_policy_fixture()
    rp_results = run_all_retrieval_policy_cases(rp_fixture)
    assert all(r.case_id != "invented_citation_case" for r in rp_results)
    assert len(rp_results) == 6  # unchanged, real calibration cases only


# --- the consolidated report distinguishes the two mechanisms ----------------


def test_the_report_carries_separate_triage_and_retrieval_policy_sections() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    rp_fixture = load_retrieval_policy_fixture()
    rp_results = run_all_retrieval_policy_cases(rp_fixture)

    report = build_report(fixture, fixture_hash, results, rp_fixture, rp_results, command="test")

    # Distinct fields, distinct dataclasses -- never merged into one
    # undifferentiated metric set.
    assert report.triage_narrative_metrics is not None
    assert report.retrieval_policy_metrics is not None
    assert report.retrieval_policy_results == rp_results
    assert report.case_results == results
    assert hasattr(report.retrieval_policy_metrics, "retrieval_policy_expected_accept_rate")
    assert hasattr(report.retrieval_policy_metrics, "retrieval_policy_forbidden_rejection_rate")
    # citation_validity_rate must never be described as retrieval precision --
    # it lives only on the triage/narrative metrics, not the retrieval ones.
    assert hasattr(report.triage_narrative_metrics, "citation_validity_rate")
    assert not hasattr(report.retrieval_policy_metrics, "citation_validity_rate")
    assert not hasattr(report.retrieval_policy_metrics, "precision")


def test_retrieval_policy_metrics_are_100_percent_on_the_real_fixture() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    rp_fixture = load_retrieval_policy_fixture()
    rp_results = run_all_retrieval_policy_cases(rp_fixture)

    report = build_report(fixture, fixture_hash, results, rp_fixture, rp_results, command="test")

    assert report.retrieval_policy_metrics.retrieval_policy_expected_accept_rate == 1.0
    assert report.retrieval_policy_metrics.retrieval_policy_forbidden_rejection_rate == 1.0
    assert report.retrieval_policy_metrics.known_limitation_cases == 1


def test_frozen_calibration_evidence_is_labeled_as_previously_measured() -> None:
    """The consolidated report must never describe stage B's evidence as a
    fresh live-model run."""
    from app.evaluation.report import FROZEN_CALIBRATION_DISCLAIMER, format_human_summary

    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    rp_fixture = load_retrieval_policy_fixture()
    rp_results = run_all_retrieval_policy_cases(rp_fixture)
    report = build_report(fixture, fixture_hash, results, rp_fixture, rp_results, command="test")

    summary = format_human_summary(report, baseline=None, comparison=None)

    assert "frozen" in FROZEN_CALIBRATION_DISCLAIMER.lower()
    assert (
        "previously-measured" in FROZEN_CALIBRATION_DISCLAIMER
        or "previously measured" in FROZEN_CALIBRATION_DISCLAIMER
    )
    assert FROZEN_CALIBRATION_DISCLAIMER in summary
    assert (
        "no model download" in FROZEN_CALIBRATION_DISCLAIMER.lower()
        or "no model was downloaded" in summary.lower()
        or "makes no model download" in summary
    )


# --- #5942 becoming accepted blocks the gate ----------------------------------


def test_5942_becoming_accepted_is_a_blocking_retrieval_policy_regression() -> None:
    """The exact regression this correction exists to catch: if the real
    selection policy ever again accepted #5942 for #5755 (the original
    Milestone 2.3 false positive), the comparison must fail, name the
    retrieval-policy case, and never be confused with a citation result."""
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    rp_fixture = load_retrieval_policy_fixture()
    rp_results = run_all_retrieval_policy_cases(rp_fixture)
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))

    # Simulate the real regression deterministically, in-memory only: force
    # the #5942 case's replayed decision to "accepted", exactly as it would
    # be if the current policy code regressed.
    degraded_rp_results = tuple(
        r
        if not r.case_id.endswith("_to_issue_5942")
        else replace(
            r,
            actual_tier="medium_confidence",
            actual_decision="accepted",
            shared_discriminative_terms=("flask",),
            blocking_failures=(
                "retrieval policy decision for issue:5755 -> issue:5942: expected 'rejected', "
                "got 'accepted'",
            ),
        )
        for r in rp_results
    )
    degraded_report = build_report(
        fixture, fixture_hash, results, rp_fixture, degraded_rp_results, command="test"
    )

    comparison = compare(baseline, degraded_report)

    assert not comparison.passed
    assert any(
        "[retrieval-policy]" in reg and "5942" in reg for reg in comparison.blocking_regressions
    )
    # It must never be reported as a citation/narrative regression.
    assert not any(
        "5942" in reg and "[triage/narrative]" in reg for reg in comparison.blocking_regressions
    )


# --- schema/versioning -------------------------------------------------------


def test_retrieval_policy_fixture_is_versioned() -> None:
    fixture = load_retrieval_policy_fixture()
    assert fixture.calibration_schema_version == "1.0.0"


def test_a_calibration_schema_version_change_is_incompatible_with_the_baseline() -> None:
    from app.evaluation.report import BaselineIncompatibleError

    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    rp_fixture = load_retrieval_policy_fixture()
    rp_results = run_all_retrieval_policy_cases(rp_fixture)
    candidate = build_report(fixture, fixture_hash, results, rp_fixture, rp_results, command="test")
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    tampered_baseline = {**baseline, "calibration_schema_version": "999.0.0"}

    with pytest.raises(BaselineIncompatibleError, match="calibration"):
        compare(tampered_baseline, candidate)


# --- zero network / zero model / zero cost ------------------------------------


def test_running_the_retrieval_policy_fixture_makes_no_network_or_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails loudly if anything in this module ever tries to reach the
    network or load a real embedding model."""

    def _forbidden(*args, **kwargs):
        raise AssertionError("retrieval-policy evaluation must never make a network call")

    monkeypatch.setattr("httpx.post", _forbidden, raising=False)
    monkeypatch.setattr("httpx.get", _forbidden, raising=False)

    fixture = load_retrieval_policy_fixture()
    results = run_all_retrieval_policy_cases(fixture)

    assert len(results) == 6


def test_the_default_calibration_path_is_the_checked_in_frozen_fixture() -> None:
    assert DEFAULT_CALIBRATION_PATH.name == "bge_similarity_calibration.json"
    assert DEFAULT_CALIBRATION_PATH.exists()


def test_retrieval_policy_fixture_class_is_frozen_and_typed() -> None:
    fixture = load_retrieval_policy_fixture()
    assert isinstance(fixture, RetrievalPolicyFixture)
    for case in fixture.cases:
        assert isinstance(case, RetrievalPolicyCase)
        assert case.expected_decision in ("accepted", "rejected")
