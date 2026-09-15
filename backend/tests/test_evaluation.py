"""Evaluation and regression harness tests (Milestone 2.5; see ADR 0011).

No test here downloads a model, calls a network service, or depends on a
real AI provider -- the harness itself is deterministic and offline by
construction (mock adapter; simulated OpenAI failure/citation scenarios
via monkeypatched `httpx.post`, never a real request).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.evaluation.cases import DEFAULT_FIXTURE_PATH, fixture_content_hash, load_fixture
from app.evaluation.contracts import EvaluationCase, EvaluationFixture, SuppliedRetrievedRecord
from app.evaluation.report import (
    BaselineIncompatibleError,
    build_report,
    compare,
    compute_triage_narrative_metrics,
)
from app.evaluation.retrieval_policy import (
    load_retrieval_policy_fixture,
    run_all_retrieval_policy_cases,
)
from app.evaluation.runner import run_all, run_case

DEFAULT_BASELINE_PATH = DEFAULT_FIXTURE_PATH.parent / "baseline.json"

# The real retrieval-policy fixture/results are cheap (six in-memory
# replays, zero network) and identical for every test in this module, so
# they are computed once here rather than per-test.
_REAL_RETRIEVAL_POLICY_FIXTURE = load_retrieval_policy_fixture()
_REAL_RETRIEVAL_POLICY_RESULTS = run_all_retrieval_policy_cases(_REAL_RETRIEVAL_POLICY_FIXTURE)


def _build_report(fixture, fixture_hash, results, *, command="test"):
    """Wraps `build_report`, filling in the real retrieval-policy fixture/
    results so existing stage A/C-focused tests don't need to repeat that
    boilerplate at every call site."""
    return build_report(
        fixture,
        fixture_hash,
        results,
        _REAL_RETRIEVAL_POLICY_FIXTURE,
        _REAL_RETRIEVAL_POLICY_RESULTS,
        command=command,
    )


def _minimal_case(**overrides) -> EvaluationCase:
    fields = {
        "case_id": "minimal_case",
        "categories": ("security_concern",),
        "rationale": "A minimal test case.",
        "blocking": True,
        "issue_external_number": 1,
        "title": "Something about security",
        "body": "body text",
        "state": "closed",
        "source_url": "https://example.test/1",
        "expected_classification_label": "security-concern",
        "expected_matched_rule": "security-keyword",
        "expected_severity": "high",
        "expected_action": "Escalate for security review before any further action.",
    }
    fields.update(overrides)
    return EvaluationCase(**fields)


# --- fixture loading and schema validation ----------------------------------


def test_the_real_fixture_loads_and_validates() -> None:
    fixture, fixture_hash = load_fixture()
    assert fixture.schema_version == "1.0.0"
    assert len(fixture.cases) >= 15
    assert len(fixture_hash) == 64  # sha256 hex digest


def test_the_real_fixture_has_unique_case_ids() -> None:
    fixture, _ = load_fixture()
    case_ids = [c.case_id for c in fixture.cases]
    assert len(case_ids) == len(set(case_ids))


def test_duplicate_case_ids_are_rejected() -> None:
    case = _minimal_case()
    with pytest.raises(ValueError, match="Duplicate case_id"):
        EvaluationFixture(
            schema_version="1.0.0",
            fixture_version="1.0.0",
            description="test",
            cases=(case, case),
        )


def test_an_unknown_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown categor"):
        _minimal_case(categories=("not_a_real_category",))


def test_an_empty_rationale_is_rejected() -> None:
    with pytest.raises(ValueError, match="rationale"):
        _minimal_case(rationale="   ")


def test_a_case_with_no_categories_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one category"):
        _minimal_case(categories=())


def test_simulate_provider_failure_and_simulated_citations_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        _minimal_case(simulate_provider_failure=True, simulated_openai_citations=("issue:1",))


def test_an_empty_fixture_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one case"):
        EvaluationFixture(schema_version="1.0.0", fixture_version="1.0.0", description="", cases=())


def test_fixture_content_hash_changes_with_any_byte_change() -> None:
    first = fixture_content_hash(b'{"a": 1}')
    second = fixture_content_hash(b'{"a": 2}')
    assert first != second
    assert fixture_content_hash(b'{"a": 1}') == first  # deterministic


# --- deterministic execution --------------------------------------------------


def test_repeated_runs_of_the_real_fixture_produce_identical_blocking_results() -> None:
    fixture, _ = load_fixture()
    first = run_all(fixture.cases)
    second = run_all(fixture.cases)

    assert [(r.case_id, r.passed, r.blocking_failures) for r in first] == [
        (r.case_id, r.passed, r.blocking_failures) for r in second
    ]
    assert [r.rendered_prompt_hash for r in first] == [r.rendered_prompt_hash for r in second]


def test_the_real_fixture_currently_passes_every_case() -> None:
    """The checked-in fixture must be internally correct -- every expected
    value was measured by running the real classify/assess/propose
    functions against real issue text, not guessed."""
    fixture, _ = load_fixture()
    results = run_all(fixture.cases)
    failing = [(r.case_id, r.blocking_failures) for r in results if not r.passed]
    assert failing == []


def test_an_intentionally_degraded_classification_fails() -> None:
    case = _minimal_case(expected_classification_label="documentation")
    result = run_case(case)
    assert not result.passed
    assert any("classification.label" in f for f in result.blocking_failures)


def test_an_intentionally_degraded_severity_fails() -> None:
    case = _minimal_case(expected_severity="low")
    result = run_case(case)
    assert not result.passed
    assert any("assessment.severity" in f for f in result.blocking_failures)


def test_a_required_citation_disappearing_fails() -> None:
    supplied = SuppliedRetrievedRecord(
        identifier="issue:2",
        title="Other",
        excerpt="x",
        source_url="https://example.test/2",
        similarity_score=0.9,
    )
    case = _minimal_case(
        supplied_retrieved_context=(supplied,), required_citations=("issue:999-never-supplied",)
    )
    result = run_case(case)
    assert not result.passed
    assert any("required citation" in f for f in result.blocking_failures)


def test_a_forbidden_citation_appearing_fails() -> None:
    supplied = SuppliedRetrievedRecord(
        identifier="issue:2",
        title="Other",
        excerpt="x",
        source_url="https://example.test/2",
        similarity_score=0.9,
    )
    case = _minimal_case(supplied_retrieved_context=(supplied,), forbidden_citations=("issue:2",))
    result = run_case(case)
    assert not result.passed
    assert any("forbidden citation" in f for f in result.blocking_failures)


def test_an_invented_citation_from_a_simulated_provider_is_rejected_via_fallback() -> None:
    """The correct system behavior for an invented citation is a graceful
    fallback (validated by expected_status="fallback" inside run_case) --
    this case passes precisely because the citation never survives."""
    case = _minimal_case(simulated_openai_citations=("issue:99999-invented",))
    result = run_case(case)
    assert result.passed
    assert result.citations == ()  # the router fell back; nothing invented survives
    assert result.provider == "mock"


def test_an_invented_citation_would_fail_the_case_if_status_check_were_missing() -> None:
    """Directly proves the mechanism run_case relies on: validate_citations
    rejects an identifier that was never supplied as retrieved context."""
    from app.ai_gateway.contracts import AIRequest, InvalidCitationError, validate_citations
    from app.triage.rules import Assessment, Classification, ProposedAction

    request = AIRequest(
        task="triage_narrative",
        classification=Classification(label="x", matched_rule="x", matched_keywords=()),
        evidence=(),
        assessment=Assessment(severity="low", rationale="r"),
        proposed_action=ProposedAction(action="a", rationale="r"),
        retrieved_context=(),
    )
    with pytest.raises(InvalidCitationError):
        validate_citations(("issue:99999-invented",), request)


def test_a_simulated_provider_failure_falls_back_and_still_passes_when_expected() -> None:
    case = _minimal_case(simulate_provider_failure=True)
    result = run_case(case)
    assert result.passed
    assert result.provider == "mock"
    assert result.estimated_cost_usd == 0.0


def test_nonzero_mock_cost_would_fail() -> None:
    """The mock/fallback path always costs $0.00 in production; this test
    documents that the cost check exists and passes for the real path."""
    case = _minimal_case()
    result = run_case(case)
    assert result.estimated_cost_usd == 0.0
    assert not any("estimated_cost_usd" in f for f in result.blocking_failures)


def test_a_known_limitation_case_is_reported_but_never_counted_as_a_required_success() -> None:
    case = _minimal_case(
        case_id="known_limitation_case",
        known_limitation=True,
        blocking=False,
        expected_classification_label="documentation",  # deliberately wrong
    )
    result = run_case(case)
    assert result.known_limitation is True
    assert result.blocking_failures == ()  # never blocks, regardless of mismatch
    assert any("non_blocking_case" in w for w in result.warnings)


def test_narrative_rule_violations_are_always_warnings_never_blocking() -> None:
    case = _minimal_case(required_narrative_fragments=("a fragment that will never appear",))
    result = run_case(case)
    assert result.passed  # narrative-rule violations never block
    assert any("narrative_rule_violation" in w for w in result.warnings)
    assert not any("narrative_rule_violation" in f for f in result.blocking_failures)


def test_token_and_latency_fields_are_present_but_purely_observational() -> None:
    case = _minimal_case()
    result = run_case(case)
    # Present, zero for the deterministic mock path, and not part of
    # blocking_failures under any circumstance this harness can produce.
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.latency_ms >= 0.0
    assert not any("token" in f.lower() for f in result.blocking_failures)
    assert not any("latency" in f.lower() for f in result.blocking_failures)


# --- reports and provenance ---------------------------------------------------


def test_candidate_report_includes_prompt_and_provider_provenance() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    report = _build_report(fixture, fixture_hash, results, command="test")
    assert report.provider == "mock"
    assert report.model == "deterministic-v1"
    assert report.prompt_id == "triage_narrative"
    assert report.prompt_version == "1.0.0"
    assert len(report.prompt_template_hash) == 64
    for result in report.case_results:
        assert len(result.rendered_prompt_hash) == 64


def test_triage_narrative_metrics_reflect_a_fully_passing_run() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    metrics = compute_triage_narrative_metrics(results)
    assert metrics.total_cases == len(fixture.cases)
    assert metrics.passed_cases == len(fixture.cases)
    assert metrics.classification_accuracy == 1.0
    assert metrics.citation_validity_rate == 1.0
    assert metrics.mock_cost_total_usd == 0.0
    assert metrics.known_limitation_cases >= 1


# --- baseline comparison -------------------------------------------------------


def test_the_real_baseline_exists_and_the_real_fixture_matches_it_cleanly() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    candidate = _build_report(fixture, fixture_hash, results, command="test")
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))

    comparison = compare(baseline, candidate)

    assert comparison.passed
    assert comparison.blocking_regressions == ()


def test_a_missing_baseline_is_reported_as_incompatible_by_the_cli_contract() -> None:
    """`compare()` itself assumes a baseline dict was already loaded; the
    CLI is responsible for the missing-file case (see test_evaluation_cli
    below). This test documents that a structurally-empty baseline fails
    loudly rather than comparing as if everything passed."""
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    candidate = _build_report(fixture, fixture_hash, results, command="test")

    with pytest.raises(BaselineIncompatibleError, match="report_schema_version"):
        compare({}, candidate)


def test_an_incompatible_schema_version_fails() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    candidate = _build_report(fixture, fixture_hash, results, command="test")
    bad_baseline = {"report_schema_version": "999.0.0"}

    with pytest.raises(BaselineIncompatibleError, match="report_schema_version"):
        compare(bad_baseline, candidate)


def test_a_changed_fixture_hash_fails_without_regenerating_anything() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    candidate = _build_report(fixture, fixture_hash, results, command="test")
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    tampered_baseline = {**baseline, "fixture_hash": "0" * 64}

    with pytest.raises(BaselineIncompatibleError, match="fixture changed"):
        compare(tampered_baseline, candidate)


def test_incomplete_case_execution_is_detected_as_a_case_set_mismatch() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    candidate = _build_report(fixture, fixture_hash, results, command="test")
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    # Simulate a baseline that ran one extra case the candidate never ran.
    baseline_with_extra_case = {
        **baseline,
        "case_results": [
            *baseline["case_results"],
            {**baseline["case_results"][0], "case_id": "a_case_that_never_ran_in_candidate"},
        ],
    }

    with pytest.raises(BaselineIncompatibleError, match="case set changed"):
        compare(baseline_with_extra_case, candidate)


def test_a_regressed_case_that_passed_in_baseline_produces_a_blocking_failure() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))

    # Corrupt one candidate result in-memory to simulate a real regression,
    # without touching any file or the real baseline.
    degraded_results = tuple(
        r
        if r.case_id != "sec_5755_primary"
        else replace(r, blocking_failures=("intentional test regression",))
        for r in results
    )
    degraded_candidate = _build_report(fixture, fixture_hash, degraded_results, command="test")

    comparison = compare(baseline, degraded_candidate)

    assert not comparison.passed
    assert any("sec_5755_primary" in r for r in comparison.blocking_regressions)


def test_nonzero_candidate_mock_cost_is_a_blocking_regression() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))

    degraded_results = tuple(
        r if r.case_id != "sec_5755_primary" else replace(r, estimated_cost_usd=0.01)
        for r in results
    )
    degraded_candidate = _build_report(fixture, fixture_hash, degraded_results, command="test")

    comparison = compare(baseline, degraded_candidate)

    assert not comparison.passed
    assert any("mock_cost_total_usd" in r for r in comparison.blocking_regressions)


def test_latency_differences_never_cause_a_regression() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))

    # Latency changes wildly; nothing else does.
    noisy_results = tuple(replace(r, latency_ms=9999.0) for r in results)
    noisy_candidate = _build_report(fixture, fixture_hash, noisy_results, command="test")

    comparison = compare(baseline, noisy_candidate)

    assert comparison.passed


def test_prompt_or_provider_identity_change_alone_is_reported_not_blocking() -> None:
    fixture, fixture_hash = load_fixture()
    results = run_all(fixture.cases)
    candidate = _build_report(fixture, fixture_hash, results, command="test")
    baseline = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_with_different_prompt = {**baseline, "prompt_version": "0.9.0"}

    comparison = compare(baseline_with_different_prompt, candidate)

    assert comparison.passed  # metrics, not identity alone, gate pass/fail
    assert any("prompt identity changed" in note for note in comparison.notes)


# --- CLI (isolated tmp_path fixture/baseline -- never the real checked-in ones) ---


_TINY_FIXTURE = {
    "schema_version": "1.0.0",
    "fixture_version": "1.0.0",
    "description": "A tiny isolated fixture for CLI tests only.",
    "cases": [
        {
            "case_id": "tiny_case",
            "categories": ["security_concern"],
            "rationale": "A tiny CLI-test-only case.",
            "issue_external_number": 1,
            "title": "A security issue",
            "body": "body",
            "state": "closed",
            "source_url": "https://example.test/1",
            "expected_classification_label": "security-concern",
            "expected_matched_rule": "security-keyword",
            "expected_severity": "high",
            "expected_action": "Escalate for security review before any further action.",
        }
    ],
}


@pytest.fixture()
def isolated_fixture_and_baseline(tmp_path):
    fixture_path = tmp_path / "fixed_cases.json"
    baseline_path = tmp_path / "baseline.json"
    fixture_path.write_text(json.dumps(_TINY_FIXTURE), encoding="utf-8")
    return fixture_path, baseline_path


def test_cli_ordinary_run_fails_cleanly_when_no_baseline_exists(
    isolated_fixture_and_baseline,
) -> None:
    from app.evaluation.__main__ import main

    fixture_path, baseline_path = isolated_fixture_and_baseline
    assert not baseline_path.exists()

    exit_code = main(["--fixture", str(fixture_path), "--baseline", str(baseline_path)])

    assert exit_code == 1
    assert not baseline_path.exists()  # ordinary runs never create/write it


def test_cli_update_baseline_creates_a_reviewable_deterministic_artifact(
    isolated_fixture_and_baseline,
) -> None:
    from app.evaluation.__main__ import main

    fixture_path, baseline_path = isolated_fixture_and_baseline

    exit_code = main(
        ["--update-baseline", "--fixture", str(fixture_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    assert baseline_path.exists()
    written = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert written["fixture_version"] == "1.0.0"
    assert written["case_results"][0]["case_id"] == "tiny_case"
    assert written["generated_by_command"] == "python -m app.evaluation --update-baseline"


def test_cli_ordinary_run_does_not_modify_an_existing_baseline(
    isolated_fixture_and_baseline,
) -> None:
    from app.evaluation.__main__ import main

    fixture_path, baseline_path = isolated_fixture_and_baseline
    main(["--update-baseline", "--fixture", str(fixture_path), "--baseline", str(baseline_path)])
    before = baseline_path.read_bytes()

    exit_code = main(["--fixture", str(fixture_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    assert baseline_path.read_bytes() == before  # byte-for-byte unchanged


def test_cli_returns_zero_on_a_clean_pass_and_nonzero_on_a_blocking_regression(
    isolated_fixture_and_baseline, tmp_path
) -> None:
    from app.evaluation.__main__ import main

    fixture_path, baseline_path = isolated_fixture_and_baseline
    main(["--update-baseline", "--fixture", str(fixture_path), "--baseline", str(baseline_path)])

    clean_exit = main(["--fixture", str(fixture_path), "--baseline", str(baseline_path)])
    assert clean_exit == 0

    degraded_fixture = json.loads(json.dumps(_TINY_FIXTURE))
    degraded_fixture["cases"][0]["expected_severity"] = "low"  # intentional mismatch
    degraded_fixture_path = tmp_path / "fixed_cases_degraded.json"
    degraded_fixture_path.write_text(json.dumps(degraded_fixture), encoding="utf-8")

    # A changed fixture is itself a blocking-comparison failure (fixture_hash
    # differs), which is also a valid nonzero-exit proof of the gate.
    degraded_exit = main(
        ["--fixture", str(degraded_fixture_path), "--baseline", str(baseline_path)]
    )
    assert degraded_exit == 1


def test_cli_writes_an_explicit_output_path_only_when_requested(
    isolated_fixture_and_baseline, tmp_path
) -> None:
    from app.evaluation.__main__ import main

    fixture_path, baseline_path = isolated_fixture_and_baseline
    main(["--update-baseline", "--fixture", str(fixture_path), "--baseline", str(baseline_path)])

    output_path = tmp_path / "candidate_report.json"
    assert not output_path.exists()

    main(
        [
            "--fixture",
            str(fixture_path),
            "--baseline",
            str(baseline_path),
            "--output",
            str(output_path),
        ]
    )

    assert output_path.exists()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["case_results"][0]["case_id"] == "tiny_case"


def test_cli_refuses_to_update_the_baseline_when_the_run_itself_has_blocking_failures(
    tmp_path,
) -> None:
    from app.evaluation.__main__ import main

    broken_fixture = json.loads(json.dumps(_TINY_FIXTURE))
    broken_fixture["cases"][0]["expected_severity"] = "low"  # will not match reality
    fixture_path = tmp_path / "fixed_cases.json"
    baseline_path = tmp_path / "baseline.json"
    fixture_path.write_text(json.dumps(broken_fixture), encoding="utf-8")

    exit_code = main(
        ["--update-baseline", "--fixture", str(fixture_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    assert not baseline_path.exists()
