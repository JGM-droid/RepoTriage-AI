"""Typed retrieval contracts.

`RetrievalRequest` in, `RetrievalResult` out. Every field on `RetrievedRecord`
is enough, on its own, to trace one supplied excerpt back to a stored
`Issue` or `RepositoryDocument` row.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

SOURCE_ISSUE = "issue"
SOURCE_DOCUMENT = "document"


@dataclass(frozen=True)
class RetrievalRequest:
    """A bounded, repository-scoped retrieval query."""

    repository_id: UUID
    exclude_issue_id: UUID
    query_text: str
    max_results: int
    max_excerpt_chars: int
    min_similarity: float = 0.0
    # Two-band confidence model (see app.retrieval.service):
    #   similarity < min_similarity                          -> rejected
    #   min_similarity <= similarity < high_confidence_similarity
    #       -> "medium confidence": requires nonzero *discriminative*
    #          lexical overlap with the query (see
    #          app.retrieval.query_terms.extract_discriminative_terms)
    #   similarity >= high_confidence_similarity              -> accepted on
    #       semantic score alone
    # None means "not configured": every candidate clearing min_similarity
    # is treated as high-confidence (no medium band, no lexical check) —
    # this is the back-compatible default for callers that only care about
    # a single similarity floor.
    high_confidence_similarity: float | None = None

    def __post_init__(self) -> None:
        # Deliberately does NOT reject an empty/whitespace-only query_text
        # here: with the query now built from issue-specific content only
        # (see app.retrieval.stage.build_query_text), a genuinely empty
        # title+body+keywords is possible for a degenerate issue, and must
        # flow through the same graceful `insufficient_query` outcome as a
        # punctuation-only query (checked by
        # `has_sufficient_query_content` inside `retrieve_related_evidence`)
        # rather than raise here, before the caller can even construct a
        # RetrievalResult to record it.
        if self.max_results < 1:
            raise ValueError("RetrievalRequest.max_results must be at least 1.")
        if self.max_excerpt_chars < 1:
            raise ValueError("RetrievalRequest.max_excerpt_chars must be at least 1.")
        # min_similarity is deliberately not range-checked here: a caller may
        # legitimately use an out-of-[0, 1] value (e.g. > 1.0) to express "no
        # real candidate can ever clear this" in a test. high_confidence_
        # similarity, when configured, must not sit below min_similarity —
        # that would make the "high confidence" band unreachable-but-wider
        # than the "medium confidence" band, an inverted, nonsensical config.
        if (
            self.high_confidence_similarity is not None
            and self.high_confidence_similarity < self.min_similarity
        ):
            raise ValueError(
                "RetrievalRequest.high_confidence_similarity must be >= min_similarity."
            )


@dataclass(frozen=True)
class RetrievedRecord:
    """One retrieved, cite-able excerpt."""

    identifier: str  # stable citation id, e.g. "issue:5755" or "doc:quickstart#sessions"
    source_type: str  # "issue" | "document"
    external_number: int | None  # set for issues, None for documents
    title: str
    excerpt: str
    source_url: str
    similarity_score: float  # 1 - cosine distance; higher is more similar
    relevance_explanation: str
    embedding_model: str
    embedding_version: str

    def __post_init__(self) -> None:
        if self.source_type not in (SOURCE_ISSUE, SOURCE_DOCUMENT):
            raise ValueError(f"Unknown RetrievedRecord.source_type: {self.source_type!r}")
        if not self.identifier:
            raise ValueError("RetrievedRecord.identifier must not be empty.")


@dataclass(frozen=True)
class RetrievalResult:
    """The outcome of one retrieval call. An empty `items` tuple is a valid,
    explicit outcome — never fabricated evidence."""

    items: tuple[RetrievedRecord, ...]
    query_summary: str
    candidates_considered: int
    total_excerpt_chars: int
    mechanism: str  # e.g. "pgvector-cosine:BAAI/bge-small-en-v1.5"
    status: str  # "ok" | "empty" | "insufficient_query" | "failed"
    # When status != "ok": "insufficient_query_content" | "no_same_repository_candidates" |
    # "below_similarity_threshold" | an actual failure's exception message.
    failure_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0
