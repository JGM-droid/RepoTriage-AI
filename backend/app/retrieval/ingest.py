"""Deterministic, idempotent ingestion of resolved issues and documentation
excerpts into `retrieval_chunks`.

Re-running ingestion never duplicates a chunk (enforced by the unique
constraints on `(issue_id, chunk_index)` / `(document_id, chunk_index)`) and
re-embeds a chunk only when its content hash or the configured embedding
model/version has changed — an unchanged chunk is never re-embedded, so a
repeat run of the default local embedder makes no new model calls beyond
whatever content actually changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import Issue, Repository, RepositoryDocument, RetrievalChunk
from app.retrieval.chunking import chunk_text, content_hash
from app.retrieval.embedding import EmbeddingAdapter

RESOLVED_ISSUE_STATES = ("closed",)
DOCUMENT_FIXTURE_FORMAT_VERSION = "1.0"


class DocumentFixtureIntegrityError(RuntimeError):
    """The document fixture's checksum, count, or format version does not
    match its manifest."""


@dataclass(frozen=True)
class IngestSummary:
    chunks_created: int
    chunks_updated: int
    chunks_unchanged: int
    chunks_removed: int


def _upsert_chunks(
    session: Session,
    *,
    repository_id,
    source_type: str,
    issue_id,
    document_id,
    external_number: int | None,
    title: str,
    source_url: str,
    state: str,
    text: str,
    adapter: EmbeddingAdapter,
) -> IngestSummary:
    chunks = chunk_text(text)
    filter_column = RetrievalChunk.issue_id if issue_id is not None else RetrievalChunk.document_id
    filter_value = issue_id if issue_id is not None else document_id
    existing_rows = {
        row.chunk_index: row
        for row in session.scalars(select(RetrievalChunk).where(filter_column == filter_value))
    }

    created = updated = unchanged = 0
    seen_indices: set[int] = set()
    for index, chunk in enumerate(chunks):
        seen_indices.add(index)
        digest = content_hash(chunk)
        existing = existing_rows.get(index)
        if (
            existing is not None
            and existing.content_hash == digest
            and existing.embedding_model == adapter.model_name
            and existing.embedding_version == adapter.model_version
        ):
            unchanged += 1
            continue

        vector = adapter.embed([chunk])[0]
        if existing is not None:
            existing.content = chunk
            existing.content_hash = digest
            existing.embedding = vector
            existing.embedding_model = adapter.model_name
            existing.embedding_version = adapter.model_version
            existing.external_number = external_number
            existing.title = title
            existing.source_url = source_url
            existing.state = state
            updated += 1
        else:
            session.add(
                RetrievalChunk(
                    repository_id=repository_id,
                    source_type=source_type,
                    issue_id=issue_id,
                    document_id=document_id,
                    chunk_index=index,
                    external_number=external_number,
                    title=title,
                    source_url=source_url,
                    state=state,
                    content=chunk,
                    content_hash=digest,
                    embedding=vector,
                    embedding_model=adapter.model_name,
                    embedding_version=adapter.model_version,
                )
            )
            created += 1

    stale = [row for index, row in existing_rows.items() if index not in seen_indices]
    for row in stale:
        session.delete(row)

    return IngestSummary(
        chunks_created=created,
        chunks_updated=updated,
        chunks_unchanged=unchanged,
        chunks_removed=len(stale),
    )


def _combine(a: IngestSummary, b: IngestSummary) -> IngestSummary:
    return IngestSummary(
        chunks_created=a.chunks_created + b.chunks_created,
        chunks_updated=a.chunks_updated + b.chunks_updated,
        chunks_unchanged=a.chunks_unchanged + b.chunks_unchanged,
        chunks_removed=a.chunks_removed + b.chunks_removed,
    )


def ingest_issues(
    session: Session, repository: Repository, adapter: EmbeddingAdapter
) -> IngestSummary:
    """Chunk and embed every resolved (closed) issue in `repository`.

    Non-resolved issues are never ingested — this is the authorization/state
    filter for issue-sourced evidence (see ADR 0009); the current fixture
    happens to be all-closed, but this stays correct if that ever changes.
    """
    summary = IngestSummary(0, 0, 0, 0)
    issues = session.scalars(
        select(Issue).where(
            Issue.repository_id == repository.id, Issue.state.in_(RESOLVED_ISSUE_STATES)
        )
    )
    for issue in issues:
        outcome = _upsert_chunks(
            session,
            repository_id=repository.id,
            source_type="issue",
            issue_id=issue.id,
            document_id=None,
            external_number=issue.external_number,
            title=issue.title,
            source_url=issue.source_url,
            state=issue.state,
            text=f"{issue.title}\n\n{issue.body}",
            adapter=adapter,
        )
        summary = _combine(summary, outcome)
    session.commit()
    return summary


def load_document_fixture(fixture_dir: Path) -> list[dict[str, Any]]:
    manifest_path = fixture_dir / "documents_manifest.json"
    documents_path = fixture_dir / "documents.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents_text = documents_path.read_text(encoding="utf-8")
    documents = json.loads(documents_text)

    canonical_bytes = documents_text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    actual_checksum = hashlib.sha256(canonical_bytes).hexdigest()
    if manifest.get("content_checksum") != actual_checksum:
        raise DocumentFixtureIntegrityError(
            "document fixture content checksum does not match its manifest"
        )
    if manifest.get("document_count") != len(documents):
        raise DocumentFixtureIntegrityError(
            "document fixture manifest count does not match fixture content"
        )
    if manifest.get("fixture_format_version") != DOCUMENT_FIXTURE_FORMAT_VERSION:
        raise DocumentFixtureIntegrityError("document fixture format version is not supported")

    return documents


def ingest_documents(
    session: Session,
    repository: Repository,
    adapter: EmbeddingAdapter,
    fixture_dir: Path,
) -> IngestSummary:
    """Upsert the bounded documentation fixture, then chunk and embed it.

    Performs no network call and does not depend on the live GitHub API —
    the fixture is a committed, offline JSON file (see
    backend/fixtures/pallets_flask/documents.json)."""
    raw_documents = load_document_fixture(fixture_dir)
    summary = IngestSummary(0, 0, 0, 0)
    for raw in raw_documents:
        digest = content_hash(raw["content"])
        existing = session.scalar(
            select(RepositoryDocument).where(
                RepositoryDocument.repository_id == repository.id,
                RepositoryDocument.path == raw["path"],
            )
        )
        if existing is None:
            existing = RepositoryDocument(
                repository_id=repository.id,
                path=raw["path"],
                title=raw["title"],
                source_url=raw["source_url"],
                license_note=raw.get("license_note"),
                content=raw["content"],
                content_hash=digest,
            )
            session.add(existing)
            session.flush()
        elif existing.content_hash != digest:
            existing.content = raw["content"]
            existing.content_hash = digest
            existing.title = raw["title"]
            existing.source_url = raw["source_url"]
            existing.license_note = raw.get("license_note")

        outcome = _upsert_chunks(
            session,
            repository_id=repository.id,
            source_type="document",
            issue_id=None,
            document_id=existing.id,
            external_number=None,
            title=raw["title"],
            source_url=raw["source_url"],
            state="published",
            text=raw["content"],
            adapter=adapter,
        )
        summary = _combine(summary, outcome)
    session.commit()
    return summary
