from dataclasses import dataclass

from app.triage.rules import assess, classify, human_review, propose, retrieve_fixture_evidence


@dataclass
class FakeIssue:
    title: str
    body: str
    state: str
    source_url: str = "https://example.test/issues/1"


def test_classify_matches_crash_keyword_before_other_rules() -> None:
    issue = FakeIssue(
        title="App crash on startup",
        body="See the traceback below; also mentions security in passing",
        state="open",
    )

    classification = classify(issue)

    assert classification.label == "bug-crash"
    assert classification.matched_rule == "crash-keyword"


def test_classify_matches_security_before_documentation() -> None:
    issue = FakeIssue(
        title="Security vulnerability report",
        body="Potential CVE affecting docs generation",
        state="open",
    )

    classification = classify(issue)

    assert classification.label == "security-concern"


def test_classify_falls_back_to_closed_state_when_no_keyword_matches() -> None:
    issue = FakeIssue(title="Old ticket", body="Nothing special here", state="closed")

    classification = classify(issue)

    assert classification.label == "closed-needs-verification"
    assert classification.matched_rule == "closed-state"


def test_classify_falls_back_to_general_triage() -> None:
    issue = FakeIssue(title="Ticket", body="Nothing special here", state="open")

    classification = classify(issue)

    assert classification.label == "general-triage"
    assert classification.matched_rule == "fallback"
    assert classification.matched_keywords == ()


def test_evidence_is_traceable_to_stored_issue_fields() -> None:
    issue = FakeIssue(
        title="Docs typo",
        body="Fix documentation wording",
        state="open",
        source_url="https://example.test/issues/9",
    )
    classification = classify(issue)

    evidence = retrieve_fixture_evidence(issue, classification)

    assert {item.field for item in evidence} == {"title", "state", "body"}
    assert all(item.source_url == issue.source_url for item in evidence)


def test_assess_and_propose_reflect_the_classification() -> None:
    issue = FakeIssue(title="App crash", body="Traceback attached", state="open")
    classification = classify(issue)
    evidence = retrieve_fixture_evidence(issue, classification)

    assessment = assess(issue, classification, evidence)
    proposal = propose(issue, classification, assessment)

    assert assessment.severity == "high"
    assert "crash" in proposal.rationale.lower() or "bug-crash" in proposal.rationale
    assert proposal.action


def test_stage_pipeline_is_deterministic_for_the_same_issue() -> None:
    issue = FakeIssue(title="Crash on save", body="Traceback included", state="open")

    def run_pipeline():
        classification = classify(issue)
        evidence = retrieve_fixture_evidence(issue, classification)
        assessment = assess(issue, classification, evidence)
        proposal = propose(issue, classification, assessment)
        review = human_review(proposal)
        return classification, evidence, assessment, proposal, review

    result_a = run_pipeline()
    result_b = run_pipeline()

    assert result_a == result_b


def test_human_review_confirms_proposed_without_deciding() -> None:
    issue = FakeIssue(title="App crash", body="Traceback attached", state="open")
    classification = classify(issue)
    evidence = retrieve_fixture_evidence(issue, classification)
    assessment = assess(issue, classification, evidence)
    proposal = propose(issue, classification, assessment)

    review = human_review(proposal)

    assert review.recommendation_status == "proposed"
    assert review.human_review_status == "awaiting_human_review"
    assert review.decision is None
