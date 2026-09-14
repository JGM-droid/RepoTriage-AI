"""Deterministic classification, evidence, assessment, and proposal rules.

Every function here is a pure function of its stored-issue input: the same
issue always produces the same result, with no network access, AI/LLM
calls, embeddings, randomness, or dependence on the current time.
"""

from __future__ import annotations

from dataclasses import dataclass

TRIAGE_RULESET_VERSION = "1.0"

RECOMMENDATION_STATUS_PROPOSED = "proposed"
HUMAN_REVIEW_STATUS_AWAITING = "awaiting_human_review"

_CRASH_KEYWORDS = ("crash", "traceback", "segfault", "exception")
_SECURITY_KEYWORDS = ("security", "vulnerability", "cve")
_REGRESSION_KEYWORDS = ("regression",)
_DOCUMENTATION_KEYWORDS = ("docs", "documentation", "typo")
_ENHANCEMENT_KEYWORDS = ("feature", "enhancement", "request")

# Ordered highest to lowest priority; the first match wins.
_KEYWORD_RULES = (
    ("bug-crash", "crash-keyword", _CRASH_KEYWORDS),
    ("security-concern", "security-keyword", _SECURITY_KEYWORDS),
    ("regression", "regression-keyword", _REGRESSION_KEYWORDS),
    ("documentation", "documentation-keyword", _DOCUMENTATION_KEYWORDS),
    ("enhancement", "enhancement-keyword", _ENHANCEMENT_KEYWORDS),
)

_SEVERITY_BY_LABEL = {
    "bug-crash": "high",
    "security-concern": "high",
    "regression": "medium",
    "documentation": "low",
    "enhancement": "low",
    "closed-needs-verification": "low",
    "general-triage": "medium",
}

_ACTION_BY_LABEL = {
    "bug-crash": "Prioritize for immediate triage and request reproduction steps.",
    "security-concern": "Escalate for security review before any further action.",
    "regression": "Flag as a regression and request the last known good version.",
    "documentation": "Route to documentation maintainers for a low-risk update.",
    "enhancement": "Queue as a candidate enhancement for roadmap review.",
    "closed-needs-verification": "Verify the closure is still valid before archiving.",
    "general-triage": "Assign for manual triage; no specialized rule matched.",
}


@dataclass(frozen=True)
class Classification:
    label: str
    matched_rule: str
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceItem:
    field: str
    excerpt: str
    source_url: str


@dataclass(frozen=True)
class Assessment:
    severity: str
    rationale: str


@dataclass(frozen=True)
class ProposedAction:
    action: str
    rationale: str


@dataclass(frozen=True)
class HumanReviewBoundary:
    recommendation_status: str
    human_review_status: str
    decision: str | None


def _matched_keywords(haystack: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(keyword for keyword in keywords if keyword in haystack)


def classify(issue: object) -> Classification:
    """Classify a stored issue using explicit, ordered keyword rules.

    Falls back to the issue's stored state, and finally to a safe default,
    when no keyword rule matches.
    """
    haystack = f"{issue.title}\n{issue.body}".lower()

    for label, rule_name, keywords in _KEYWORD_RULES:
        matches = _matched_keywords(haystack, keywords)
        if matches:
            return Classification(label=label, matched_rule=rule_name, matched_keywords=matches)

    if issue.state == "closed":
        return Classification(
            label="closed-needs-verification", matched_rule="closed-state", matched_keywords=()
        )

    return Classification(label="general-triage", matched_rule="fallback", matched_keywords=())


def _excerpt(text: str, limit: int = 240) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}…"


def retrieve_fixture_evidence(
    issue: object, classification: Classification
) -> tuple[EvidenceItem, ...]:
    """Retrieve evidence strictly from the stored issue; nothing is invented."""
    del classification  # evidence retrieval is not rule-dependent in this milestone
    return (
        EvidenceItem(field="title", excerpt=_excerpt(issue.title), source_url=issue.source_url),
        EvidenceItem(field="state", excerpt=issue.state, source_url=issue.source_url),
        EvidenceItem(field="body", excerpt=_excerpt(issue.body), source_url=issue.source_url),
    )


def assess(
    issue: object,
    classification: Classification,
    evidence: tuple[EvidenceItem, ...],
) -> Assessment:
    """Derive a deterministic severity assessment from the classification."""
    del issue  # severity depends only on the classification and gathered evidence
    severity = _SEVERITY_BY_LABEL.get(classification.label, "medium")
    rationale = (
        f"Matched rule '{classification.matched_rule}' for classification "
        f"'{classification.label}' based on {len(evidence)} stored evidence item(s)."
    )
    return Assessment(severity=severity, rationale=rationale)


def propose(
    issue: object,
    classification: Classification,
    assessment: Assessment,
) -> ProposedAction:
    """Propose a deterministic next action. This is a proposal only, not a decision."""
    del issue  # the proposed action depends only on classification and assessment
    action = _ACTION_BY_LABEL.get(classification.label, _ACTION_BY_LABEL["general-triage"])
    rationale = (
        f"Derived from classification '{classification.label}' at '{assessment.severity}' severity."
    )
    return ProposedAction(action=action, rationale=rationale)


def human_review(proposal: ProposedAction) -> HumanReviewBoundary:
    """Confirm the fifth workflow stage: the proposal awaits an explicit human decision.

    This stage never approves, rejects, revises, or otherwise creates a human
    decision; it only deterministically confirms that the recommendation
    remains proposed and is awaiting review.
    """
    del proposal  # the boundary confirmation does not depend on proposal content
    return HumanReviewBoundary(
        recommendation_status=RECOMMENDATION_STATUS_PROPOSED,
        human_review_status=HUMAN_REVIEW_STATUS_AWAITING,
        decision=None,
    )
