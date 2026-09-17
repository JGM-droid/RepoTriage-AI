"""The `retrieve_related_evidence` workflow stage.

The only place `app.retrieval` is wired into the triage pipeline (see
`app.workflow.tasks`). Builds a bounded, issue-specific query from
already-computed deterministic values, then retrieves. A genuine retrieval
failure (e.g. a transient database error inside the query) degrades to an
explicit empty result rather than failing the whole workflow attempt —
`EmbeddingDimensionMismatchError` (a configuration/schema problem) and
`RetrievalTenantMismatchError` (a tenant-isolation invariant violation;
Milestone 3.1 Slice 2, see ADR 0014) are never treated as controlled,
degrade-to-empty failures: both are re-raised so the whole workflow
attempt fails loudly, through the same bounded-retry/stage-attempt
machinery every other stage failure uses, rather than silently
continuing toward AI inference and recommendation creation on a
security-relevant condition.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.retrieval.contracts import RetrievalRequest, RetrievalResult
from app.retrieval.embedding import EmbeddingDimensionMismatchError, build_embedding_adapter
from app.retrieval.service import RetrievalTenantMismatchError, retrieve_related_evidence
from app.triage.rules import Classification

STATUS_FAILED = "failed"

# Bounded, not the same limit as the retrieved-evidence excerpt shown to a
# reviewer (`Settings.retrieval_max_excerpt_chars`) — this one bounds how
# much of the *analyzed issue's own* body goes into the query, a distinct
# concern.
QUERY_BODY_EXCERPT_CHARS = 300

_WHITESPACE_PATTERN = re.compile(r"\s+")
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def _bounded_excerpt(text: str, limit: int) -> str:
    normalized = _normalize_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


def _deduplicated_keywords(existing_text: str, keywords: tuple[str, ...]) -> list[str]:
    """Matched keywords already present (case-insensitively) in `existing_text`
    are dropped, so a keyword already spelled out in the title/body excerpt
    is never embedded a second time."""
    seen = set(_WORD_PATTERN.findall(existing_text.lower()))
    deduped: list[str] = []
    for keyword in keywords:
        normalized = keyword.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(keyword)
    return deduped


def build_query_text(issue: object, classification: Classification) -> str:
    """Issue-specific retrieval query: normalized title, a bounded body
    excerpt, and deduplicated matched keywords not already present in
    either. Deliberately excludes severity, rationale, proposed-action
    prose, and the classification label — calibration showed all four are
    shared boilerplate phrases across many issues (see
    `app/triage/rules.py`'s `_SEVERITY_BY_LABEL`/`_ACTION_BY_LABEL`) that
    homogenize every issue's query into the same narrow score range,
    defeating the similarity threshold (see ADR 0009). Those deterministic
    values are unchanged and remain available to `ai_inference` — they are
    simply never embedded into the retrieval query.
    """
    title = _normalize_whitespace(issue.title)
    body_excerpt = _bounded_excerpt(issue.body, QUERY_BODY_EXCERPT_CHARS)
    keywords = _deduplicated_keywords(f"{title} {body_excerpt}", classification.matched_keywords)
    parts = [part for part in (title, body_excerpt, *keywords) if part]
    return " ".join(parts).strip()


def run_retrieve_related_evidence_stage(
    session: Session,
    issue: object,
    classification: Classification,
    settings: Settings | None = None,
) -> RetrievalResult:
    settings = settings or get_settings()
    adapter = build_embedding_adapter(settings)
    request = RetrievalRequest(
        repository_id=issue.repository_id,
        exclude_issue_id=issue.id,
        query_text=build_query_text(issue, classification),
        max_results=settings.retrieval_max_results,
        max_excerpt_chars=settings.retrieval_max_excerpt_chars,
        min_similarity=settings.retrieval_min_similarity,
        high_confidence_similarity=settings.retrieval_high_confidence_similarity,
    )
    try:
        return retrieve_related_evidence(session, request, adapter)
    except (EmbeddingDimensionMismatchError, RetrievalTenantMismatchError):
        raise
    except Exception as exc:
        return RetrievalResult(
            items=(),
            query_summary=f"retrieval failed for query: {request.query_text[:200]!r}",
            candidates_considered=0,
            total_excerpt_chars=0,
            mechanism=f"pgvector-cosine:{adapter.model_name}",
            status=STATUS_FAILED,
            failure_reason=str(exc),
        )
