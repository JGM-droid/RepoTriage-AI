"""pgvector-backed retrieval query.

Every query is filtered to one repository and excludes the analyzed issue's
own chunks before ranking. Multiple chunks from the same issue/document are
deduplicated down to their single best-scoring chunk before the top-k cut,
so near-duplicate adjacent chunks never crowd out other, more diverse
evidence. Ordering is deterministic: similarity descending, then a stable
per-source-type tie-break (issue external_number / document path), then
chunk_index. A query with no meaningful content is rejected before any
embedding call or vector query; a candidate below the calibrated minimum
similarity (see ADR 0009) is excluded before the top-k cut, not forced in
just because it was the best of an irrelevant set.

Two-band confidence model: pgvector similarity alone still generates and
ranks every candidate. Acceptance then depends on which band a candidate's
similarity falls into (see `RetrievalRequest.high_confidence_similarity`):
below `min_similarity` it is rejected outright; from `min_similarity` up to
(not including) `high_confidence_similarity` ("medium confidence") it
additionally needs at least one shared *discriminative* term with the query
— a deterministic lexical check (see `app.retrieval.query_terms.
extract_discriminative_terms`), not a reranker or a second model; at or
above `high_confidence_similarity` ("high confidence") the semantic score
alone qualifies. This replaced an earlier, narrower "close case" band after
a live demo produced a false positive just above that band's old ceiling —
see ADR 0009 and `config.retrieval_high_confidence_similarity` for the
measured evidence. Top-k is a maximum, never a quota: fewer than
`max_results` accepted candidates is a valid, expected outcome — rejected
candidates are never backfilled in to fill out the slate.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import RetrievalChunk
from app.retrieval.contracts import (
    SOURCE_ISSUE,
    RetrievalRequest,
    RetrievalResult,
    RetrievedRecord,
)
from app.retrieval.embedding import EmbeddingAdapter, embed_query
from app.retrieval.query_terms import extract_discriminative_terms, has_sufficient_query_content

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_INSUFFICIENT_QUERY = "insufficient_query"

REASON_INSUFFICIENT_QUERY = "insufficient_query_content"
REASON_NO_CANDIDATES = "no_same_repository_candidates"
REASON_BELOW_THRESHOLD = "below_similarity_threshold"

_TIER_HIGH_CONFIDENCE = "high_confidence"
_TIER_MEDIUM_CONFIDENCE = "medium_confidence"


def _tie_break_key(chunk: RetrievalChunk) -> tuple[str, object, int]:
    if chunk.source_type == SOURCE_ISSUE:
        return (chunk.source_type, chunk.external_number or 0, chunk.chunk_index)
    return (chunk.source_type, chunk.document.path if chunk.document else "", chunk.chunk_index)


def _identifier(chunk: RetrievalChunk) -> str:
    if chunk.source_type == SOURCE_ISSUE:
        return f"issue:{chunk.external_number}"
    path = chunk.document.path if chunk.document else str(chunk.document_id)
    return f"doc:{path}"


def _excerpt(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit].rstrip()}…"


def _relevance_explanation(
    source_type: str,
    similarity: float,
    tier: str,
    shared_terms: frozenset[str],
    high_confidence_similarity: float | None,
) -> str:
    kind = "a related resolved issue" if source_type == SOURCE_ISSUE else "a documentation excerpt"
    if tier == _TIER_HIGH_CONFIDENCE:
        threshold_note = (
            f" (at or above the {high_confidence_similarity:.2f} strong-confidence threshold)"
            if high_confidence_similarity is not None
            else ""
        )
        return (
            f"Ranked as {kind} with cosine similarity {similarity:.3f} to the analyzed "
            f"issue — accepted on semantic similarity alone{threshold_note}."
        )
    terms = ", ".join(sorted(shared_terms))
    return (
        f"Ranked as {kind} with cosine similarity {similarity:.3f} to the analyzed issue — "
        f"accepted at medium confidence because it shares meaningful, non-generic term(s) "
        f"with the query: {terms}."
    )


def _shared_discriminative_terms(query_text: str, candidate_text: str) -> frozenset[str]:
    query_terms = set(extract_discriminative_terms(query_text))
    if not query_terms:
        return frozenset()
    return frozenset(query_terms.intersection(extract_discriminative_terms(candidate_text)))


def _confidence_decision(
    chunk: RetrievalChunk, similarity: float, request: RetrievalRequest
) -> tuple[bool, str, frozenset[str]]:
    """Returns (accepted, tier, shared_discriminative_terms)."""
    if similarity < request.min_similarity:
        return False, "", frozenset()
    high_confidence_similarity = request.high_confidence_similarity
    if high_confidence_similarity is None or similarity >= high_confidence_similarity:
        return True, _TIER_HIGH_CONFIDENCE, frozenset()
    shared_terms = _shared_discriminative_terms(request.query_text, chunk.content)
    if shared_terms:
        return True, _TIER_MEDIUM_CONFIDENCE, shared_terms
    return False, "", frozenset()


def retrieve_related_evidence(
    session: Session, request: RetrievalRequest, adapter: EmbeddingAdapter
) -> RetrievalResult:
    mechanism = f"pgvector-cosine:{adapter.model_name}"

    if not has_sufficient_query_content(request.query_text):
        return RetrievalResult(
            items=(),
            query_summary=f"query rejected (insufficient content): {request.query_text[:200]!r}",
            candidates_considered=0,
            total_excerpt_chars=0,
            mechanism=mechanism,
            status=STATUS_INSUFFICIENT_QUERY,
            failure_reason=REASON_INSUFFICIENT_QUERY,
        )

    query_vector = embed_query(adapter, request.query_text)
    distance = RetrievalChunk.embedding.cosine_distance(query_vector)

    stmt = (
        select(RetrievalChunk, distance.label("distance"))
        .where(RetrievalChunk.repository_id == request.repository_id)
        .where(
            sa.or_(
                RetrievalChunk.issue_id.is_(None),
                RetrievalChunk.issue_id != request.exclude_issue_id,
            )
        )
    )
    rows = session.execute(stmt).all()
    query_summary = f"top-{request.max_results} match for: {request.query_text[:200]!r}"

    if not rows:
        return RetrievalResult(
            items=(),
            query_summary=query_summary,
            candidates_considered=0,
            total_excerpt_chars=0,
            mechanism=mechanism,
            status=STATUS_EMPTY,
            failure_reason=REASON_NO_CANDIDATES,
        )

    best_per_source: dict[tuple[str, object], tuple[RetrievalChunk, float]] = {}
    for chunk, chunk_distance in rows:
        key = (chunk.source_type, chunk.issue_id or chunk.document_id)
        current_best = best_per_source.get(key)
        if current_best is None or chunk_distance < current_best[1]:
            best_per_source[key] = (chunk, chunk_distance)

    ranked = sorted(best_per_source.values(), key=lambda pair: (pair[1], _tie_break_key(pair[0])))
    # A candidate below the calibrated minimum similarity is not "relevant
    # evidence" — excluded here, before the top-k cut, rather than forced in
    # just because it was the best of an irrelevant set. A medium-confidence
    # candidate additionally needs a shared discriminative term with the
    # query (see _confidence_decision). max_results is a cap, never a quota:
    # rejected candidates are never backfilled to reach it.
    evaluated = [
        (chunk, chunk_distance, *_confidence_decision(chunk, 1.0 - chunk_distance, request))
        for chunk, chunk_distance in ranked
    ]
    relevant = [
        (chunk, chunk_distance, tier, shared_terms)
        for chunk, chunk_distance, accepted, tier, shared_terms in evaluated
        if accepted
    ]
    selected = relevant[: request.max_results]

    if not selected:
        return RetrievalResult(
            items=(),
            query_summary=query_summary,
            candidates_considered=len(rows),
            total_excerpt_chars=0,
            mechanism=mechanism,
            status=STATUS_EMPTY,
            failure_reason=REASON_BELOW_THRESHOLD,
        )

    items = tuple(
        RetrievedRecord(
            identifier=_identifier(chunk),
            source_type=chunk.source_type,
            external_number=chunk.external_number,
            title=chunk.title,
            excerpt=_excerpt(chunk.content, request.max_excerpt_chars),
            source_url=chunk.source_url,
            similarity_score=1.0 - chunk_distance,
            relevance_explanation=_relevance_explanation(
                chunk.source_type,
                1.0 - chunk_distance,
                tier,
                shared_terms,
                request.high_confidence_similarity,
            ),
            embedding_model=chunk.embedding_model,
            embedding_version=chunk.embedding_version,
        )
        for chunk, chunk_distance, tier, shared_terms in selected
    )

    return RetrievalResult(
        items=items,
        query_summary=query_summary,
        candidates_considered=len(rows),
        total_excerpt_chars=sum(len(item.excerpt) for item in items),
        mechanism=mechanism,
        status=STATUS_OK,
    )
