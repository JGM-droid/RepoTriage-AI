"""Idempotent seed for the isolation-demo organization's synthetic data
(Milestone 3.1 Slice 2; see ADR 0014).

Reuses the exact same importer/ingestion code paths as the primary demo
(`app.importer.service.import_fixture`, `app.retrieval.ingest.
ingest_issues`/`ingest_documents`) against a small, entirely synthetic
fixture (`backend/fixtures/isolation_demo/`) -- fictional company/repo
names, hand-authored issues and documentation, no real customer or
repository data anywhere in it. Its only purpose is to make a second,
real tenant provably present with enough data (a repository, a few
issues, a couple of embedded documentation chunks) to demonstrate and test
retrieval/vector isolation, not to be a realistic demo corpus on its own.

Idempotent like every path it reuses: re-running this makes no duplicate
rows and, once already ingested with unchanged content, embeds nothing
again. Never touches the default organization's data.

Never run against the live demo database by anything in this codebase --
this module exists so Jesse can choose to run it deliberately later, as a
separate, explicit decision, not as a side effect of any test or of this
slice's implementation.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.importer.service import ImportSummary, import_fixture
from app.models.core import ISOLATION_DEMO_ORGANIZATION_ID, Repository
from app.retrieval.embedding import EmbeddingAdapter
from app.retrieval.ingest import IngestSummary, ingest_documents, ingest_issues

ISOLATION_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "isolation_demo"


def seed_isolation_demo_organization(
    session: Session, adapter: EmbeddingAdapter, fixture_dir: Path | None = None
) -> tuple[ImportSummary, IngestSummary, IngestSummary]:
    """Import the synthetic fixture (owned by the isolation-demo
    organization) and embed its issues and documents. Returns the
    import summary plus the two ingest summaries (issues, documents)."""
    fixture_dir = fixture_dir or ISOLATION_FIXTURE_DIR
    import_summary = import_fixture(
        session, fixture_dir=fixture_dir, tenant_id=ISOLATION_DEMO_ORGANIZATION_ID
    )
    repository = session.get(Repository, import_summary.repository_id)
    issue_summary = ingest_issues(session, repository, adapter)
    document_summary = ingest_documents(session, repository, adapter, fixture_dir)
    return import_summary, issue_summary, document_summary
