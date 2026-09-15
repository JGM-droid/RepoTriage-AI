"""Calibration evidence for `retrieval_min_similarity` (Milestone 2.3
correction; see ADR 0009 and `tests/calibration/bge_similarity_calibration.json`).

This is deliberately NOT a live-model test: it validates the *chosen
threshold* against a frozen, versioned snapshot of real scores measured
once against the real BAAI/bge-small-en-v1.5 model and the real demo
corpus (with the BGE query instruction applied, exactly as
`app.retrieval.embedding.embed_query` does). Keeping the evidence frozen
means this test is fast and makes no model/network call itself, while
still being real evidence rather than an assumption — the measurement
procedure that produced the JSON is documented in ADR 0009 and is
reproducible against any environment that has the real model (e.g. the
Docker image), it just is not part of the routine test suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings

CALIBRATION_PATH = Path(__file__).parent / "calibration" / "bge_similarity_calibration.json"


def load_calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))


def test_calibration_fixture_is_well_formed() -> None:
    data = load_calibration()
    assert data["model"] == "BAAI/bge-small-en-v1.5"
    assert data["query_instruction_applied"] is True
    assert len(data["positive_examples"]) >= 4
    assert len(data["negative_examples"]) >= 3
    # The specific confirmed near-duplicate this milestone was built around.
    assert any(
        example["query_source"] == "issue:5755" and example["target"] == "issue:5756"
        for example in data["positive_examples"]
    )


def test_configured_threshold_matches_the_calibration_fixture() -> None:
    """The shipped default must be the exact value the calibration evidence
    supports — this test breaks loudly if someone changes one without the
    other."""
    data = load_calibration()
    assert get_settings().retrieval_min_similarity == data["chosen_threshold"]


def test_configured_high_confidence_similarity_matches_the_calibration_fixture() -> None:
    data = load_calibration()
    assert get_settings().retrieval_high_confidence_similarity == data["high_confidence_similarity"]


def test_settings_reject_an_inverted_confidence_band_configuration() -> None:
    """Both thresholds are validated at the Settings level too: a
    high_confidence_similarity below min_similarity is a nonsensical,
    inverted configuration and must fail fast at startup, not silently
    produce a two-band model with the bands swapped."""
    with pytest.raises(ValidationError, match="retrieval_high_confidence_similarity"):
        Settings(retrieval_min_similarity=0.90, retrieval_high_confidence_similarity=0.65)


def test_calibrated_threshold_retains_every_confident_positive_example() -> None:
    """Every positive example NOT explicitly flagged as a known below-threshold
    exception (see the issue<->document overlap documented in
    `known_limitations`) must clear the chosen threshold."""
    data = load_calibration()
    threshold = data["chosen_threshold"]
    confident_positives = [
        example
        for example in data["positive_examples"]
        if not example.get("below_chosen_threshold")
    ]
    assert confident_positives, "expected at least one confident (non-exception) positive example"
    failures = [example for example in confident_positives if example["score"] < threshold]
    assert not failures, (
        f"confident positive example(s) incorrectly excluded by {threshold}: {failures}"
    )


def test_calibrated_threshold_rejects_every_measured_negative_example() -> None:
    data = load_calibration()
    threshold = data["chosen_threshold"]
    failures = [example for example in data["negative_examples"] if example["score"] >= threshold]
    assert not failures, f"negative example(s) incorrectly retained by {threshold}: {failures}"


def test_issue_to_issue_calibration_shows_a_real_gap_between_positive_and_negative_scores() -> None:
    """Guards against silently re-approving an overlapping, indefensible
    threshold in the future: within the issue<->issue category (the category
    with a defensible gap — see the issue<->document limitation documented
    below), the weakest positive must still clear the strongest negative."""
    data = load_calibration()
    issue_positives = [
        e["score"] for e in data["positive_examples"] if e["category"] == "issue-issue"
    ]
    issue_negatives = [
        e["score"] for e in data["negative_examples"] if e["category"] == "issue-issue"
    ]
    assert issue_positives and issue_negatives
    weakest_positive = min(issue_positives)
    strongest_negative = max(issue_negatives)
    assert weakest_positive > strongest_negative
    assert strongest_negative < data["chosen_threshold"] <= weakest_positive


def test_issue_to_document_overlap_is_a_known_documented_limitation() -> None:
    """The one measured issue<->document positive and negative overlap (the
    negative scores *higher* than the positive) — there is no defensible
    document-specific threshold with this small sample. The chosen threshold
    resolves this honestly by excluding both rather than guessing a cutoff,
    which this test pins down so the overlap can't silently disappear or
    silently get worse without the fixture being revisited."""
    data = load_calibration()
    threshold = data["chosen_threshold"]
    document_positives = [e for e in data["positive_examples"] if e["category"] == "issue-document"]
    document_negatives = [e for e in data["negative_examples"] if e["category"] == "issue-document"]
    assert document_positives and document_negatives
    assert all(e["score"] < threshold for e in document_positives), (
        "a document positive now clears the threshold — the known limitation may be "
        "resolved; update this test and the fixture's known_limitations accordingly"
    )
    assert all(e["score"] < threshold for e in document_negatives)
    assert max(e["score"] for e in document_negatives) > min(e["score"] for e in document_positives)


def test_lexical_overlap_check_does_not_reliably_resolve_the_ambiguous_document_pair() -> None:
    """Documents why the medium-confidence band (app.retrieval.service)
    requires *discriminative* (non-generic) terms rather than a blanket
    lexical-overlap rule: a naive lexical-overlap comparison for the one
    measured ambiguous issue<->document pair runs backwards — the negative
    has higher overlap than the positive."""
    data = load_calibration()
    overlap = data["lexical_overlap_check"]
    positive_overlap = overlap[
        "issue:5786 -> doc:quickstart#sessions (the measured positive, score 0.6153)"
    ]
    negative_overlap = overlap[
        "issue:5746 -> doc:quickstart#sessions (the measured negative, score 0.6239)"
    ]
    assert positive_overlap < negative_overlap


def test_a_degenerate_query_is_rejected_before_any_embedding_call() -> None:
    """Documents why the meaningful-query gate (rejecting a degenerate query
    before embedding) is the correct fix for punctuation-only content, not a
    similarity floor: the fixture records the gate's outcome directly."""
    data = load_calibration()
    degenerate = data["rejected_before_embedding_examples"][0]
    assert degenerate["status"] == "insufficient_query"


# --- false-positive regression (Jesse's visible demo, #5942) -----------------


def _false_positive_regression_examples() -> list[dict]:
    return load_calibration()["false_positive_regression_examples"]["examples"]


def _find_example(target: str) -> dict:
    examples = _false_positive_regression_examples()
    matches = [e for e in examples if e["target"] == target]
    assert len(matches) == 1, f"expected exactly one regression example for {target}"
    return matches[0]


def test_regression_fixture_decisions_are_self_consistent_with_the_two_band_model() -> None:
    """Every recorded regression example's (score, tier, decision,
    shared_discriminative_terms) tuple must be internally consistent with
    the two-band rule itself — catches a fixture data-entry mistake even
    though this test makes no live model call."""
    data = load_calibration()
    min_similarity = data["chosen_threshold"]
    high_confidence_similarity = data["high_confidence_similarity"]

    for example in _false_positive_regression_examples():
        score = example["score"]
        assert score >= min_similarity, f"{example['target']}: score below min_similarity entirely"
        if score >= high_confidence_similarity:
            assert example["tier"] == "high_confidence"
            assert example["decision"] == "accepted"
        else:
            assert example["tier"] == "medium_confidence"
            if example["shared_discriminative_terms"]:
                assert example["decision"] == "accepted"
            else:
                assert example["decision"] == "rejected"


def test_regression_fixture_confirms_5756_accepted_high_confidence() -> None:
    example = _find_example("issue:5756")
    assert example["query_source"] == "issue:5755"
    assert example["decision"] == "accepted"
    assert example["tier"] == "high_confidence"
    assert example["score"] >= load_calibration()["high_confidence_similarity"]


def test_regression_fixture_confirms_5942_false_positive_is_rejected() -> None:
    """The exact issue reported from Jesse's visible demo: #5942 concerns
    `flask run`/PEP 723 packaging, not #5755's security-API problem, and
    must be rejected — sharing only the corpus-generic word 'flask' is not
    genuine lexical support."""
    example = _find_example("issue:5942")
    assert example["query_source"] == "issue:5755"
    assert example["decision"] == "rejected"
    assert example["shared_discriminative_terms"] == []
    threshold = load_calibration()["chosen_threshold"]
    high_confidence_similarity = load_calibration()["high_confidence_similarity"]
    assert threshold <= example["score"] < high_confidence_similarity  # in the medium band


def test_regression_fixture_confirms_5804_boilerplate_false_positive_is_rejected() -> None:
    """A second false positive caught during THIS correction's own live
    verification (not the originally reported one): #5804 was briefly
    accepted on nothing but shared 'environment'/'version' boilerplate from
    the standard bug-report template, because an earlier draft of the
    corpus-generic term list omitted them despite both measuring above its
    own documented 10% cutoff. Must stay rejected."""
    example = _find_example("issue:5804")
    assert example["query_source"] == "issue:5755"
    assert example["decision"] == "rejected"
    assert example["shared_discriminative_terms"] == []


def test_regression_fixture_confirms_5836_low_content_word_false_positive_is_rejected() -> None:
    """A third false positive caught during this correction's own live
    verification: 'using' is a common English gerund, not a discriminative
    concept, but measured only 5.7% document frequency (below the 10%
    corpus-generic cutoff), so document frequency alone missed it. Must stay
    rejected via the general low-content-word addendum."""
    example = _find_example("issue:5836")
    assert example["query_source"] == "issue:5755"
    assert example["decision"] == "rejected"
    assert example["shared_discriminative_terms"] == []


def test_regression_fixture_confirms_5863_accepted_only_via_genuine_lexical_support() -> None:
    """#5863 is accepted only because 'security' is a real, measured
    discriminative term shared with #5755 — not because it was special-cased
    by issue id."""
    example = _find_example("issue:5863")
    assert example["decision"] == "accepted"
    assert example["tier"] == "medium_confidence"
    assert example["shared_discriminative_terms"], "must be supported by a genuine shared term"
    assert "flask" not in example["shared_discriminative_terms"]  # generic terms don't count


def test_discriminative_terms_extraction_matches_the_frozen_regression_evidence() -> None:
    """Re-derives the #5755<->#5942 and #5755<->#5863 discriminative-term
    overlap live from the real query_terms module (fast, deterministic, no
    model/network call), using representative excerpts of the real issue
    text captured during this correction's live investigation, and checks
    it matches what the frozen fixture claims — this is the one piece of
    the regression evidence that doesn't require a live embedding call to
    re-verify."""
    from app.retrieval.query_terms import extract_discriminative_terms

    # Verbatim app.retrieval.stage.build_query_text output for #5755,
    # captured live against the real demo DB during this correction.
    issue_5755_query = (
        "Flask cannot find /security/logic API We are using airflow 2.8.4, and "
        "recently we upgrade it to 2.10.5 Environment for Airflow 2.8.4 - Python "
        "version: 3.10 - Flask version: ``` Flask-AppBuilder==4.5.2 "
        "Flask-Babel==2.0.0 Flask-Bcrypt==1.0.1 Flask-Caching==2.3.0 "
        "Flask-JWT-Extended==4.7.1 Flask-Limiter==3.10.1 Flask-Login==0.6.3 "
        "Flask-SQLA"
    )
    # Representative excerpt of #5942's real body (title + opening
    # paragraphs, same live capture) — the reported false positive.
    issue_5942_content = (
        "flask run should support pep723 Because no one wants to use: native "
        "pkgutil-style, declarative, non-declarative, setup.py, setup.cfg, "
        "pyproject.toml, pep517, setuptools, build, pip, namespace packages"
    )
    # Representative excerpt of #5863's real body (title + opening
    # paragraphs; the real production query text for #5863 also ends with
    # an appended "security" keyword, confirmed during this correction's
    # live investigation).
    issue_5863_content = (
        "Modern CSRF Protection Using Sec-Fetch-Site Header Flask's documentation "
        "states that CSRF protection requires a form validation framework, "
        "necessitating one-time tokens stored in cookies for security"
    )

    # Verbatim excerpt of #5804's real title + opening body, captured live
    # against the real demo DB during this correction — the second false
    # positive this correction caught, via shared "environment"/"version"
    # bug-report-template boilerplate.
    issue_5804_content = (
        "3.1.2 regression: stream_with_context triggers teardown_request() "
        "calls before response generation Hello, I believe the changes to "
        "stream_with_context() introduced a bug where the teardown_request() "
        "callables are invoked too early in the request/response lifecycle"
    )

    query_terms = set(extract_discriminative_terms(issue_5755_query))
    assert "flask" not in query_terms  # generic term correctly stripped
    assert "environment" not in query_terms  # bug-report-template boilerplate
    assert "version" not in query_terms  # bug-report-template boilerplate
    assert "we" not in query_terms  # personal pronoun, no discriminative content
    assert "using" not in query_terms  # low-content gerund, no discriminative content

    shared_with_5942 = query_terms & set(extract_discriminative_terms(issue_5942_content))
    assert shared_with_5942 == set()  # matches the frozen fixture: rejected, no shared terms

    shared_with_5863 = query_terms & set(extract_discriminative_terms(issue_5863_content))
    assert "security" in shared_with_5863  # matches the frozen fixture: accepted via "security"

    shared_with_5804 = query_terms & set(extract_discriminative_terms(issue_5804_content))
    assert shared_with_5804 == set()  # matches the frozen fixture: rejected, no shared terms
