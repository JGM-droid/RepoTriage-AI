"""Offline ingestion command: `python -m app.retrieval`.

Chunks and embeds the already-imported resolved issues plus the committed
documentation fixture for the `pallets/flask` repository. Requires that
`python -m app.importer` has already run (the repository/issues must
already exist). Performs no network call: the default local embedder
requires no paid API and no live GitHub access; re-running is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.importer.service import DEFAULT_FIXTURE_DIR
from app.models.core import Repository
from app.retrieval.embedding import build_embedding_adapter
from app.retrieval.ingest import ingest_documents, ingest_issues

DEFAULT_REPOSITORY_SOURCE_URL = "https://github.com/pallets/flask"


def main() -> int:
    settings = get_settings()
    session = SessionLocal()
    try:
        repository = session.scalar(
            select(Repository).where(Repository.source_url == DEFAULT_REPOSITORY_SOURCE_URL)
        )
        if repository is None:
            print(
                "Ingestion failed: no repository found. Run `python -m app.importer` first.",
                file=sys.stderr,
            )
            return 1

        adapter = build_embedding_adapter(settings)
        issue_summary = ingest_issues(session, repository, adapter)
        document_summary = ingest_documents(session, repository, adapter, Path(DEFAULT_FIXTURE_DIR))
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()

    print(
        f"Issues: {issue_summary.chunks_created} chunk(s) created, "
        f"{issue_summary.chunks_updated} updated, {issue_summary.chunks_unchanged} unchanged, "
        f"{issue_summary.chunks_removed} removed."
    )
    print(
        f"Documents: {document_summary.chunks_created} chunk(s) created, "
        f"{document_summary.chunks_updated} updated, "
        f"{document_summary.chunks_unchanged} unchanged, "
        f"{document_summary.chunks_removed} removed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
