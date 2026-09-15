"""Repository-grounded retrieval tests (Milestone 2.3): chunking, the fake
embedder, idempotent ingestion, and pgvector-backed ranking/filtering.

No test here uses the real local embedding model or makes a network call —
`FakeEmbeddingAdapter` (hash-based) or a small hand-crafted adapter with
designed vectors stands in throughout, matching the project's established
"deterministic by default" test pattern.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.core import Issue, Repository, RepositoryDocument, RetrievalChunk
from app.retrieval.chunking import chunk_text, content_hash
from app.retrieval.contracts import RetrievalRequest
from app.retrieval.embedding import EmbeddingDimensionMismatchError, FakeEmbeddingAdapter
from app.retrieval.ingest import (
    DocumentFixtureIntegrityError,
    ingest_documents,
    ingest_issues,
    load_document_fixture,
)
from app.retrieval.service import retrieve_related_evidence

TRUNCATE_TABLES = (
    "audit_events, human_decisions, recommendations, stage_attempts, analyses, "
    "retrieval_chunks, repository_documents, issues, repositories CASCADE"
)
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pallets_flask"


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for retrieval tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {TRUNCATE_TABLES}"))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def add_repository(
    session: Session, source_url: str = "https://github.com/pallets/flask"
) -> Repository:
    repository = Repository(name="pallets/flask", source_url=source_url)
    session.add(repository)
    session.commit()
    return repository


def add_issue(
    session: Session,
    repository: Repository,
    *,
    external_number: int,
    title: str,
    body: str,
    state: str = "closed",
) -> Issue:
    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title=title,
        body=body,
        state=state,
        source_url=f"https://github.com/pallets/flask/issues/{external_number}",
    )
    session.add(issue)
    session.commit()
    return issue


# --- chunking ----------------------------------------------------------


def test_chunk_text_is_deterministic_bounded_and_deduplicated() -> None:
    text_input = (
        ("Paragraph one.\n\n" * 1) + ("Paragraph two, quite short.\n\n") + ("Repeat me.\n\n" * 2)
    )
    first = chunk_text(text_input, max_chars=200)
    second = chunk_text(text_input, max_chars=200)

    assert first == second  # deterministic
    assert all(len(chunk) <= 200 for chunk in first)  # bounded
    assert all(chunk.strip() for chunk in first)  # no empty chunks
    assert len(first) == len(set(first))  # no duplicate content survives


def test_chunk_text_hard_wraps_a_single_oversized_paragraph() -> None:
    # Genuinely non-repeating content (unique 4-digit index per position):
    # repeated content would produce identical, and therefore deduplicated,
    # chunks, which is a different property than what this test checks.
    huge_paragraph = "".join(f"{i:04d}" for i in range(300))
    chunks = chunk_text(huge_paragraph, max_chars=500)
    assert len(chunks) == 3
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_chunk_text_returns_empty_list_for_blank_input() -> None:
    assert chunk_text("   \n\n  ") == []


def test_content_hash_is_stable_and_sensitive_to_changes() -> None:
    assert content_hash("same") == content_hash("same")
    assert content_hash("same") != content_hash("different")


# --- fake embedder -------------------------------------------------------


def test_fake_embedder_is_deterministic_and_matches_configured_dimension() -> None:
    adapter = FakeEmbeddingAdapter(dimension=384)
    first = adapter.embed(["hello world"])[0]
    second = adapter.embed(["hello world"])[0]

    assert first == second
    assert len(first) == 384


# --- ingestion: issues ----------------------------------------------------


def test_ingest_issues_only_chunks_resolved_closed_issues(database_session: Session) -> None:
    repository = add_repository(database_session)
    add_issue(
        database_session,
        repository,
        external_number=1,
        title="Closed bug",
        body="Traceback here",
        state="closed",
    )
    add_issue(
        database_session,
        repository,
        external_number=2,
        title="Still open",
        body="Not resolved yet",
        state="open",
    )
    adapter = FakeEmbeddingAdapter(dimension=384)

    ingest_issues(database_session, repository, adapter)

    chunks = database_session.query(RetrievalChunk).all()
    assert len(chunks) == 1
    assert chunks[0].external_number == 1


def test_ingest_issues_is_idempotent_and_never_duplicates(database_session: Session) -> None:
    repository = add_repository(database_session)
    add_issue(
        database_session, repository, external_number=1, title="Closed bug", body="Traceback here"
    )
    adapter = FakeEmbeddingAdapter(dimension=384)

    ingest_issues(database_session, repository, adapter)
    first_count = database_session.query(RetrievalChunk).count()
    ingest_issues(database_session, repository, adapter)
    second_count = database_session.query(RetrievalChunk).count()

    assert first_count == second_count > 0


def test_ingest_issues_skips_re_embedding_unchanged_content(database_session: Session) -> None:
    repository = add_repository(database_session)
    add_issue(
        database_session, repository, external_number=1, title="Closed bug", body="Traceback here"
    )

    call_count = {"value": 0}
    adapter = FakeEmbeddingAdapter(dimension=384)
    original_embed = adapter.embed

    def counting_embed(texts):
        call_count["value"] += len(texts)
        return original_embed(texts)

    adapter.embed = counting_embed  # type: ignore[method-assign]

    ingest_issues(database_session, repository, adapter)
    calls_after_first_run = call_count["value"]
    assert calls_after_first_run > 0

    ingest_issues(database_session, repository, adapter)
    assert call_count["value"] == calls_after_first_run  # no new embed calls: unchanged content


def test_ingest_issues_re_embeds_when_content_changes(database_session: Session) -> None:
    repository = add_repository(database_session)
    issue = add_issue(
        database_session, repository, external_number=1, title="Closed bug", body="Original body"
    )
    adapter = FakeEmbeddingAdapter(dimension=384)
    ingest_issues(database_session, repository, adapter)
    original_hash = database_session.query(RetrievalChunk).one().content_hash

    issue.body = "Updated body describing a different problem entirely"
    database_session.commit()
    ingest_issues(database_session, repository, adapter)

    updated_hash = database_session.query(RetrievalChunk).one().content_hash
    assert updated_hash != original_hash


# --- ingestion: documents --------------------------------------------------


def test_load_document_fixture_matches_its_manifest() -> None:
    documents = load_document_fixture(FIXTURE_DIR)
    assert len(documents) == 6
    assert all(
        doc["source_url"].startswith("https://flask.palletsprojects.com/") for doc in documents
    )


def test_load_document_fixture_rejects_a_tampered_checksum(tmp_path: Path) -> None:
    import json
    import shutil

    fixture_copy = tmp_path / "pallets_flask"
    fixture_copy.mkdir()
    shutil.copy(FIXTURE_DIR / "documents_manifest.json", fixture_copy / "documents_manifest.json")
    documents = json.loads((FIXTURE_DIR / "documents.json").read_text(encoding="utf-8"))
    documents[0]["content"] = "tampered content"
    (fixture_copy / "documents.json").write_text(json.dumps(documents), encoding="utf-8")

    with pytest.raises(DocumentFixtureIntegrityError):
        load_document_fixture(fixture_copy)


def test_ingest_documents_is_idempotent_and_never_duplicates(database_session: Session) -> None:
    repository = add_repository(database_session)
    adapter = FakeEmbeddingAdapter(dimension=384)

    ingest_documents(database_session, repository, adapter, FIXTURE_DIR)
    first_doc_count = database_session.query(RepositoryDocument).count()
    first_chunk_count = (
        database_session.query(RetrievalChunk).filter_by(source_type="document").count()
    )

    ingest_documents(database_session, repository, adapter, FIXTURE_DIR)
    second_doc_count = database_session.query(RepositoryDocument).count()
    second_chunk_count = (
        database_session.query(RetrievalChunk).filter_by(source_type="document").count()
    )

    assert first_doc_count == second_doc_count == 6
    assert first_chunk_count == second_chunk_count > 0


# --- retrieval service -----------------------------------------------------


def _pad(vector: list[float]) -> list[float]:
    return vector + [0.0] * (384 - len(vector))


class _HandCraftedAdapter:
    """A tiny embedding adapter with designed vectors so similarity ordering
    can be verified exactly, unlike the hash-based fake adapter. Vectors are
    given as short 3-value directions and zero-padded up to the real
    384-dimension column so they can actually be stored/queried."""

    model_name = "hand-crafted-test-adapter"
    model_version = "1"
    dimension = 384
    # Empty on purpose: this adapter tests ranking/filtering behavior, not
    # the BGE instruction mechanism (see the dedicated instruction tests),
    # so its designed vectors are keyed by the raw, unprefixed query text.
    query_instruction = ""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = {key: _pad(value) for key, value in vectors.items()}

    def embed(self, texts):
        return [self._vectors[text] for text in texts]


def _seed_two_repositories_with_chunks(session: Session) -> tuple[Repository, Repository, Issue]:
    repo_a = add_repository(session, source_url="https://github.com/pallets/flask")
    repo_b = add_repository(session, source_url="https://github.com/pallets/werkzeug")
    analyzed_issue = add_issue(
        session, repo_a, external_number=100, title="query", body="query body"
    )
    close_match = add_issue(
        session, repo_a, external_number=101, title="close match", body="close match body"
    )
    far_match = add_issue(
        session, repo_a, external_number=102, title="far match", body="far match body"
    )
    other_repo_issue = add_issue(
        session, repo_b, external_number=200, title="other repo", body="other repo body"
    )

    adapter = _HandCraftedAdapter(
        {
            "close match\n\nclose match body": [0.99, 0.01, 0.0],
            "far match\n\nfar match body": [0.0, 1.0, 0.0],
            "other repo\n\nother repo body": [1.0, 0.0, 0.0],
        }
    )
    for issue in (close_match, far_match, other_repo_issue):
        text_key = f"{issue.title}\n\n{issue.body}"
        vector = adapter.embed([text_key])[0]
        session.add(
            RetrievalChunk(
                repository_id=issue.repository_id,
                source_type="issue",
                issue_id=issue.id,
                chunk_index=0,
                external_number=issue.external_number,
                title=issue.title,
                source_url=issue.source_url,
                state=issue.state,
                content=issue.body,
                content_hash=content_hash(issue.body),
                embedding=vector,
                embedding_model=adapter.model_name,
                embedding_version=adapter.model_version,
            )
        )
    session.commit()
    return repo_a, repo_b, analyzed_issue


def test_retrieval_is_scoped_to_the_same_repository(database_session: Session) -> None:
    repo_a, repo_b, analyzed_issue = _seed_two_repositories_with_chunks(database_session)
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=10,
        max_excerpt_chars=200,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    identifiers = {item.identifier for item in result.items}
    assert "issue:200" not in identifiers  # repo_b's issue never leaks in
    assert identifiers == {"issue:101", "issue:102"}


def test_retrieval_excludes_the_analyzed_issue_itself(database_session: Session) -> None:
    repo_a, _repo_b, analyzed_issue = _seed_two_repositories_with_chunks(database_session)
    # Also chunk the analyzed issue itself, to prove it's filtered even when present.
    database_session.add(
        RetrievalChunk(
            repository_id=repo_a.id,
            source_type="issue",
            issue_id=analyzed_issue.id,
            chunk_index=0,
            external_number=analyzed_issue.external_number,
            title=analyzed_issue.title,
            source_url=analyzed_issue.source_url,
            state=analyzed_issue.state,
            content=analyzed_issue.body,
            content_hash=content_hash(analyzed_issue.body),
            embedding=_pad([1.0, 0.0, 0.0]),
            embedding_model="hand-crafted-test-adapter",
            embedding_version="1",
        )
    )
    database_session.commit()
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=10,
        max_excerpt_chars=200,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert f"issue:{analyzed_issue.external_number}" not in {
        item.identifier for item in result.items
    }


def test_retrieval_ranks_by_similarity_deterministically(database_session: Session) -> None:
    repo_a, _repo_b, analyzed_issue = _seed_two_repositories_with_chunks(database_session)
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=10,
        max_excerpt_chars=200,
    )

    first = retrieve_related_evidence(database_session, request, query_adapter)
    second = retrieve_related_evidence(database_session, request, query_adapter)

    assert [item.identifier for item in first.items] == [item.identifier for item in second.items]
    assert [item.identifier for item in first.items] == [
        "issue:101",
        "issue:102",
    ]  # close match ranks first
    assert first.items[0].similarity_score > first.items[1].similarity_score


def test_retrieval_respects_max_results(database_session: Session) -> None:
    repo_a, _repo_b, analyzed_issue = _seed_two_repositories_with_chunks(database_session)
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=1,
        max_excerpt_chars=200,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert len(result.items) == 1
    assert result.items[0].identifier == "issue:101"
    assert result.candidates_considered == 2  # both same-repo candidates were seen, only 1 selected


def test_retrieval_deduplicates_multiple_chunks_from_the_same_issue(
    database_session: Session,
) -> None:
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="q", body="q body"
    )
    related_issue = add_issue(
        database_session, repository, external_number=2, title="related", body="related body"
    )
    for index, vector in enumerate([[0.9, 0.1, 0.0], [0.8, 0.2, 0.0]]):
        database_session.add(
            RetrievalChunk(
                repository_id=repository.id,
                source_type="issue",
                issue_id=related_issue.id,
                chunk_index=index,
                external_number=related_issue.external_number,
                title=related_issue.title,
                source_url=related_issue.source_url,
                state=related_issue.state,
                content=f"chunk {index}",
                content_hash=content_hash(f"chunk {index}"),
                embedding=_pad(vector),
                embedding_model="hand-crafted-test-adapter",
                embedding_version="1",
            )
        )
    database_session.commit()
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=10,
        max_excerpt_chars=200,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert len(result.items) == 1  # only the best of the two chunks from the same issue


def test_retrieval_excludes_candidates_below_the_minimum_similarity(
    database_session: Session,
) -> None:
    """A low-scoring candidate is not "relevant evidence" just because it
    was the best available — with hundreds of candidates in a real corpus,
    without this the system would always force some result into the
    top-k regardless of actual relevance, and "no relevant evidence found"
    would only ever be reachable in the degenerate zero-candidate case."""
    repo_a, _repo_b, analyzed_issue = _seed_two_repositories_with_chunks(database_session)
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=10,
        max_excerpt_chars=200,
        min_similarity=0.95,  # only issue:101 (similarity 0.99) clears this
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert [item.identifier for item in result.items] == ["issue:101"]

    strict_request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=10,
        max_excerpt_chars=200,
        min_similarity=1.1,  # nothing can clear an impossible threshold
    )
    empty_result = retrieve_related_evidence(database_session, strict_request, query_adapter)
    assert empty_result.is_empty
    assert empty_result.status == "empty"
    assert empty_result.candidates_considered == 2  # still reports what it saw


def test_retrieval_returns_an_explicit_empty_result_when_nothing_matches(
    database_session: Session,
) -> None:
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="q", body="q body"
    )
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=3,
        max_excerpt_chars=200,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert result.is_empty
    assert result.status == "empty"
    assert result.items == ()


def test_retrieved_record_provenance_traces_back_to_the_stored_issue(
    database_session: Session,
) -> None:
    repo_a, _repo_b, analyzed_issue = _seed_two_repositories_with_chunks(database_session)
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=1,
        max_excerpt_chars=200,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    item = result.items[0]
    assert item.identifier == "issue:101"
    assert item.source_url == "https://github.com/pallets/flask/issues/101"
    assert item.external_number == 101


class _FakeVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _WrongDimensionModel:
    def embed(self, texts):
        return [_FakeVector([0.0, 0.0]) for _ in texts]


def test_dimension_mismatch_fails_clearly_instead_of_silently_truncating() -> None:
    from app.retrieval.embedding import LocalEmbeddingAdapter

    adapter = object.__new__(LocalEmbeddingAdapter)
    adapter.model_name = "test-model"
    adapter.model_version = "1"
    adapter.dimension = 384
    adapter._model = _WrongDimensionModel()

    with pytest.raises(EmbeddingDimensionMismatchError):
        adapter.embed(["some text"])


# --- meaningful-query validation --------------------------------------------


class TestMeaningfulQueryContent:
    """`app.retrieval.query_terms` — deterministic, no NLP dependency."""

    def test_empty_and_whitespace_only_are_insufficient(self) -> None:
        from app.retrieval.query_terms import has_sufficient_query_content

        assert not has_sufficient_query_content("")
        assert not has_sufficient_query_content("   \n\t  ")

    def test_punctuation_only_is_insufficient(self) -> None:
        from app.retrieval.query_terms import has_sufficient_query_content

        assert not has_sufficient_query_content(".")
        assert not has_sufficient_query_content("...!?")
        assert not has_sufficient_query_content("---///")

    def test_stop_words_only_is_insufficient(self) -> None:
        from app.retrieval.query_terms import has_sufficient_query_content

        assert not has_sufficient_query_content("the a of is")

    def test_a_single_meaningful_word_is_sufficient(self) -> None:
        from app.retrieval.query_terms import has_sufficient_query_content

        assert has_sufficient_query_content("session")
        assert has_sufficient_query_content("crash")

    def test_a_realistic_constructed_query_is_sufficient(self) -> None:
        from app.retrieval.query_terms import has_sufficient_query_content

        assert has_sufficient_query_content(
            ". general-triage medium Assign for manual triage; no specialized rule matched."
        )


class TestDiscriminativeTerms:
    """`app.retrieval.query_terms.extract_discriminative_terms` — the
    medium-confidence lexical-support check's vocabulary filter."""

    def test_corpus_generic_terms_are_excluded(self) -> None:
        from app.retrieval.query_terms import extract_discriminative_terms

        terms = set(extract_discriminative_terms("flask python code issue project"))
        assert terms == set()

    def test_personal_pronouns_and_modals_are_excluded(self) -> None:
        from app.retrieval.query_terms import extract_discriminative_terms

        terms = set(extract_discriminative_terms("we can use it but it would also help"))
        assert terms == {"help"}
        terms_without_content = set(extract_discriminative_terms("we can use it but it would"))
        assert terms_without_content == set()

    def test_standalone_numbers_are_excluded(self) -> None:
        from app.retrieval.query_terms import extract_discriminative_terms

        assert set(extract_discriminative_terms("version 34 39 11")) == set()

    def test_genuine_topical_words_are_kept(self) -> None:
        from app.retrieval.query_terms import extract_discriminative_terms

        terms = set(extract_discriminative_terms("session cookie csrf security redirect"))
        assert terms == {"session", "cookie", "csrf", "security", "redirect"}

    def test_a_mixed_sentence_keeps_only_the_discriminative_words(self) -> None:
        from app.retrieval.query_terms import extract_discriminative_terms

        terms = set(
            extract_discriminative_terms(
                "We are using flask and python but the csrf protection is broken"
            )
        )
        assert terms == {"csrf", "protection", "broken"}


def test_punctuation_only_query_returns_insufficient_query_status(
    database_session: Session,
) -> None:
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="anything", body="anything body"
    )
    related = add_issue(
        database_session, repository, external_number=2, title="related", body="related body"
    )
    adapter = FakeEmbeddingAdapter(dimension=384)
    ingest_issues(database_session, repository, adapter)
    del related  # exists only so a non-empty candidate set is available if the gate were skipped

    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text=".",
        max_results=3,
        max_excerpt_chars=200,
    )
    fresh_adapter = FakeEmbeddingAdapter(dimension=384)

    result = retrieve_related_evidence(database_session, request, fresh_adapter)

    assert result.is_empty
    assert result.status == "insufficient_query"
    assert result.failure_reason == "insufficient_query_content"
    assert result.candidates_considered == 0
    assert fresh_adapter.calls == []  # zero embedding calls


def test_whitespace_only_query_returns_insufficient_query_status(
    database_session: Session,
) -> None:
    """`RetrievalRequest` accepts a whitespace/empty query_text without
    raising (a genuinely empty issue title+body+keywords is possible once
    the query is built from issue-specific content only) — it flows
    through the same graceful `insufficient_query` outcome as
    punctuation-only content, not a hard construction-time exception."""
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="x", body="x body"
    )
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="   ",
        max_results=3,
        max_excerpt_chars=200,
    )
    adapter = FakeEmbeddingAdapter(dimension=384)

    result = retrieve_related_evidence(database_session, request, adapter)

    assert result.status == "insufficient_query"
    assert result.failure_reason == "insufficient_query_content"
    assert adapter.calls == []


def test_punctuation_only_query_makes_zero_embedding_and_db_calls(
    database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-empty but non-informative (passes the contract's `.strip()`
    check, fails the meaningful-content gate) must still short-circuit
    before any embedding call or vector query."""
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="x", body="x body"
    )
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="...!?",
        max_results=3,
        max_excerpt_chars=200,
    )
    adapter = FakeEmbeddingAdapter(dimension=384)

    def _must_not_query(*args, **kwargs):
        raise AssertionError("the vector query must not run for a rejected query")

    monkeypatch.setattr(database_session, "execute", _must_not_query)

    result = retrieve_related_evidence(database_session, request, adapter)

    assert result.status == "insufficient_query"
    assert adapter.calls == []


# --- BGE query instruction ---------------------------------------------------


def test_embed_query_applies_the_bge_instruction() -> None:
    from app.retrieval.embedding import BGE_QUERY_INSTRUCTION, embed_query

    adapter = FakeEmbeddingAdapter(dimension=384)
    embed_query(adapter, "session handling bug")

    assert adapter.calls == [f"{BGE_QUERY_INSTRUCTION}session handling bug"]


def test_retrieval_applies_the_query_instruction_only_to_the_query_not_to_stored_passages(
    database_session: Session,
) -> None:
    from app.retrieval.embedding import BGE_QUERY_INSTRUCTION

    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="analyzed", body="analyzed body"
    )
    add_issue(
        database_session, repository, external_number=2, title="candidate", body="candidate body"
    )
    ingest_adapter = FakeEmbeddingAdapter(dimension=384)
    ingest_issues(database_session, repository, ingest_adapter)
    # Passage embedding (ingestion) never receives the query instruction.
    assert all(not call.startswith(BGE_QUERY_INSTRUCTION) for call in ingest_adapter.calls)

    query_adapter = FakeEmbeddingAdapter(dimension=384)
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="meaningful search text",
        max_results=3,
        max_excerpt_chars=200,
    )
    retrieve_related_evidence(database_session, request, query_adapter)

    # Query embedding (retrieval) always receives it, exactly once.
    assert query_adapter.calls == [f"{BGE_QUERY_INSTRUCTION}meaningful search text"]


# --- status/reason distinctions ----------------------------------------------


def test_status_distinguishes_no_candidates_from_below_threshold(
    database_session: Session,
) -> None:
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="lonely", body="lonely body"
    )
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})

    # No same-repository candidates exist at all.
    no_candidates_request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=3,
        max_excerpt_chars=200,
    )
    no_candidates_result = retrieve_related_evidence(
        database_session, no_candidates_request, query_adapter
    )
    assert no_candidates_result.status == "empty"
    assert no_candidates_result.failure_reason == "no_same_repository_candidates"

    # A candidate exists but scores below the threshold.
    weak_match = add_issue(
        database_session, repository, external_number=2, title="weak", body="weak body"
    )
    database_session.add(
        RetrievalChunk(
            repository_id=repository.id,
            source_type="issue",
            issue_id=weak_match.id,
            chunk_index=0,
            external_number=weak_match.external_number,
            title=weak_match.title,
            source_url=weak_match.source_url,
            state=weak_match.state,
            content=weak_match.body,
            content_hash=content_hash(weak_match.body),
            embedding=_pad([0.0, 1.0, 0.0]),  # orthogonal direction: ~0 similarity to the query
            embedding_model="hand-crafted-test-adapter",
            embedding_version="1",
        )
    )
    database_session.commit()
    below_threshold_request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=3,
        max_excerpt_chars=200,
        min_similarity=0.65,
    )
    below_threshold_result = retrieve_related_evidence(
        database_session, below_threshold_request, query_adapter
    )
    assert below_threshold_result.is_empty
    assert below_threshold_result.status == "empty"
    assert below_threshold_result.failure_reason == "below_similarity_threshold"
    assert below_threshold_result.candidates_considered == 1  # it was seen, just excluded


# --- two-band confidence model ------------------------------------------------


def _add_chunk_with_similarity(
    session: Session,
    issue: Issue,
    *,
    similarity: float,
    content: str,
) -> None:
    """Adds a chunk whose embedding has an *exact* cosine similarity to the
    query vector [1.0, 0.0, 0.0] used throughout this test module — lets a
    test pick a precise similarity score (e.g. 0.75, just above the old,
    too-narrow close-case ceiling) instead of an approximate one."""
    import math

    orthogonal_component = math.sqrt(max(0.0, 1.0 - similarity**2))
    session.add(
        RetrievalChunk(
            repository_id=issue.repository_id,
            source_type="issue",
            issue_id=issue.id,
            chunk_index=0,
            external_number=issue.external_number,
            title=issue.title,
            source_url=issue.source_url,
            state=issue.state,
            content=content,
            content_hash=content_hash(content),
            embedding=_pad([similarity, orthogonal_component, 0.0]),
            embedding_model="hand-crafted-test-adapter",
            embedding_version="1",
        )
    )


def test_medium_confidence_candidate_without_discriminative_overlap_is_rejected(
    database_session: Session,
) -> None:
    """The bug this correction fixes: a candidate at 0.75 — above the old,
    too-narrow close-case ceiling of `min_similarity + 0.05 = 0.70` — must
    NOT be accepted on semantic score alone. It sits in the medium-
    confidence band `[0.65, 0.90)` and needs a shared discriminative term,
    which this candidate does not have (see ADR 0009 / #5942)."""
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="lonely", body="lonely body"
    )
    unrelated = add_issue(
        database_session, repository, external_number=2, title="unrelated topic entirely", body=""
    )
    _add_chunk_with_similarity(
        database_session, unrelated, similarity=0.75, content="totally different vocabulary"
    )
    database_session.commit()
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=3,
        max_excerpt_chars=200,
        min_similarity=0.65,
        high_confidence_similarity=0.90,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert result.is_empty
    assert result.status == "empty"
    assert result.failure_reason == "below_similarity_threshold"


def test_medium_confidence_candidate_with_discriminative_overlap_is_accepted(
    database_session: Session,
) -> None:
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="lonely", body="lonely body"
    )
    related = add_issue(
        database_session, repository, external_number=2, title="search query match", body=""
    )
    _add_chunk_with_similarity(
        database_session,
        related,
        similarity=0.75,
        content="this content mentions the search query directly",
    )
    database_session.commit()
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=3,
        max_excerpt_chars=200,
        min_similarity=0.65,
        high_confidence_similarity=0.90,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert [item.identifier for item in result.items] == ["issue:2"]
    assert (
        "search" in result.items[0].relevance_explanation
        or "query" in result.items[0].relevance_explanation
    )


def test_generic_corpus_terms_alone_cannot_satisfy_medium_confidence_lexical_support(
    database_session: Session,
) -> None:
    """Sharing only ubiquitous, corpus-generic words (flask/python/code/...)
    must not count as discriminative lexical support — otherwise almost
    every same-repository candidate could "match" on vocabulary that says
    nothing about actual subject relevance."""
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session,
        repository,
        external_number=1,
        title="Flask python code",
        body="",
    )
    generic_only_match = add_issue(
        database_session,
        repository,
        external_number=2,
        title="unrelated topic entirely",
        body="",
    )
    _add_chunk_with_similarity(
        database_session,
        generic_only_match,
        similarity=0.75,
        content="This flask python code has nothing to do with the query subject.",
    )
    database_session.commit()
    query_adapter = _HandCraftedAdapter({"flask python code": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="flask python code",
        max_results=3,
        max_excerpt_chars=200,
        min_similarity=0.65,
        high_confidence_similarity=0.90,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert result.is_empty
    assert result.status == "empty"
    assert result.failure_reason == "below_similarity_threshold"


def test_very_high_confidence_candidate_needs_no_lexical_overlap(
    database_session: Session,
) -> None:
    """At or above `high_confidence_similarity`, semantic score alone
    qualifies — no candidates above this turn's chosen boundary automatically
    bypass lexical confirmation *unless* they clear the real, calibrated
    high-confidence threshold (see ADR 0009: #5755->#5756 measured ~0.928)."""
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="lonely", body="lonely body"
    )
    strong_match = add_issue(
        database_session, repository, external_number=2, title="zzz completely different", body=""
    )
    _add_chunk_with_similarity(
        database_session,
        strong_match,
        similarity=0.95,
        content="shares no vocabulary with the query at all",
    )
    database_session.commit()
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=3,
        max_excerpt_chars=200,
        min_similarity=0.65,
        high_confidence_similarity=0.90,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert [item.identifier for item in result.items] == ["issue:2"]
    assert "semantic similarity alone" in result.items[0].relevance_explanation


def test_high_confidence_similarity_of_none_disables_the_medium_band(
    database_session: Session,
) -> None:
    """`high_confidence_similarity=None` (the default) means "not
    configured": every candidate clearing `min_similarity` is treated as
    high-confidence, matching the pre-two-band vector-only behavior — no
    currently hand-crafted test vector should be affected by this default."""
    repo_a, _repo_b, analyzed_issue = _seed_two_repositories_with_chunks(database_session)
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repo_a.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=10,
        max_excerpt_chars=200,
    )
    assert request.high_confidence_similarity is None

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert {item.identifier for item in result.items} == {"issue:101", "issue:102"}


def test_high_confidence_similarity_below_min_similarity_is_rejected_at_construction() -> None:
    """Both thresholds are validated: an inverted configuration (the
    "high confidence" band starting below the "medium confidence" floor)
    is nonsensical and must fail fast, not silently misbehave."""
    from uuid import uuid4

    with pytest.raises(ValueError, match="high_confidence_similarity must be >= min_similarity"):
        RetrievalRequest(
            repository_id=uuid4(),
            exclude_issue_id=uuid4(),
            query_text="search query",
            max_results=3,
            max_excerpt_chars=200,
            min_similarity=0.80,
            high_confidence_similarity=0.70,
        )


def test_fewer_than_max_results_are_returned_without_backfilling(
    database_session: Session,
) -> None:
    """Top-k is a maximum, not a quota: when only one of several candidates
    qualifies, the result must contain exactly that one — never padded out
    with rejected candidates to reach max_results."""
    repository = add_repository(database_session)
    analyzed_issue = add_issue(
        database_session, repository, external_number=1, title="lonely", body="lonely body"
    )
    accepted = add_issue(
        database_session,
        repository,
        external_number=2,
        title="search query strongly matches",
        body="",
    )
    _add_chunk_with_similarity(
        database_session,
        accepted,
        similarity=0.95,
        content="genuinely matches the search query",
    )
    for number, sim in ((3, 0.80), (4, 0.75)):
        rejected_issue = add_issue(
            database_session, repository, external_number=number, title="unrelated", body=""
        )
        _add_chunk_with_similarity(
            database_session, rejected_issue, similarity=sim, content="no shared vocabulary at all"
        )
    database_session.commit()
    query_adapter = _HandCraftedAdapter({"search query": [1.0, 0.0, 0.0]})
    request = RetrievalRequest(
        repository_id=repository.id,
        exclude_issue_id=analyzed_issue.id,
        query_text="search query",
        max_results=3,  # a cap of 3, but only one candidate qualifies
        max_excerpt_chars=200,
        min_similarity=0.65,
        high_confidence_similarity=0.90,
    )

    result = retrieve_related_evidence(database_session, request, query_adapter)

    assert [item.identifier for item in result.items] == ["issue:2"]
    assert result.candidates_considered == 3  # all three were seen, only one selected


# --- production query builder (app.retrieval.stage) --------------------------


class _IssueLike:
    """Minimal stand-in for `Issue`: `build_query_text` only reads
    `.title`/`.body`."""

    def __init__(self, title: str, body: str) -> None:
        self.title = title
        self.body = body


def test_build_query_text_cannot_include_severity_rationale_or_proposed_action() -> None:
    """The removed fields aren't merely filtered out — `build_query_text`'s
    signature no longer accepts an `Assessment` or `ProposedAction` at all,
    so this boilerplate is structurally unavailable to the retrieval query."""
    import inspect

    from app.retrieval.stage import build_query_text

    parameters = set(inspect.signature(build_query_text).parameters)
    assert parameters == {"issue", "classification"}


def test_build_query_text_excludes_classification_label_and_generic_boilerplate() -> None:
    from app.retrieval.stage import build_query_text
    from app.triage.rules import Classification

    issue = _IssueLike(title="Crash on startup", body="The app crashes immediately on boot.")
    classification = Classification(
        label="general-triage",
        matched_rule="fallback",
        matched_keywords=("crash",),
    )

    query = build_query_text(issue, classification)

    assert "general-triage" not in query
    assert "manual triage" not in query
    assert "no specialized rule matched" not in query


def test_build_query_text_includes_normalized_title_and_bounded_body_excerpt() -> None:
    from app.retrieval.stage import QUERY_BODY_EXCERPT_CHARS, build_query_text
    from app.triage.rules import Classification

    long_body = "word " * 500  # far longer than the excerpt bound
    issue = _IssueLike(title="  Extra   whitespace   title  ", body=long_body)
    classification = Classification(label="x", matched_rule="x", matched_keywords=())

    query = build_query_text(issue, classification)

    assert query.startswith("Extra whitespace title")  # whitespace normalized, not truncated
    body_portion = query[len("Extra whitespace title") :]
    assert len(body_portion.strip()) <= QUERY_BODY_EXCERPT_CHARS


def test_build_query_text_deduplicates_keywords_already_present_in_title_or_body() -> None:
    from app.retrieval.stage import build_query_text
    from app.triage.rules import Classification

    issue = _IssueLike(title="Session handling issue", body="The redirect drops the cookie value.")
    classification = Classification(
        label="session-bug",
        matched_rule="r",
        matched_keywords=("session", "cookie", "redirect", "novel-keyword"),
    )

    query = build_query_text(issue, classification)

    assert query.lower().count("session") == 1
    assert query.lower().count("cookie") == 1
    assert query.lower().count("redirect") == 1
    assert "novel-keyword" in query  # a genuinely new keyword is still appended


def test_build_query_text_of_a_degenerate_issue_fails_the_meaningful_query_gate() -> None:
    from app.retrieval.query_terms import has_sufficient_query_content
    from app.retrieval.stage import build_query_text
    from app.triage.rules import Classification

    issue = _IssueLike(title=".", body="...")
    classification = Classification(label="x", matched_rule="x", matched_keywords=())

    query = build_query_text(issue, classification)

    assert not has_sufficient_query_content(query)
