"""Retrieval/vector tenant-isolation tests (Milestone 3.1 Slice 2; see ADR
0014) -- the sharpest form of Critical Gate G4's evidence requirement.

Proves the real `app.retrieval.service.retrieve_related_evidence` query
never returns another organization's chunks, even in the adversarial case
where the other organization's content is *maximally* similar (identical
text, embedded with the deterministic fake adapter -- guaranteeing
cosine similarity 1.0, the highest possible score, would otherwise win
outright on relevance). Also proves the boundary is enforced inside the
retrieval query itself, not merely in how an API response is later
formatted, by calling `run_retrieve_related_evidence_stage`/
`retrieve_related_evidence` directly -- the same functions
`app.workflow.tasks.process_workflow_run` calls -- and by driving a real
end-to-end workflow run and inspecting the persisted recommendation's
evidence, citations, and rendered prompt.

Requires isolated PostgreSQL -- skips cleanly if unavailable. Never runs
against the live demo database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.core import (
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORGANIZATION_ID,
    ISOLATION_DEMO_ORGANIZATION_ID,
    Analysis,
    Issue,
    Recommendation,
    Repository,
    RetrievalChunk,
)
from app.retrieval.contracts import RetrievalRequest
from app.retrieval.embedding import FakeEmbeddingAdapter
from app.retrieval.service import retrieve_related_evidence
from app.retrieval.stage import run_retrieve_related_evidence_stage
from app.triage.rules import Classification
from app.workflow.tasks import process_workflow_run
from tests.db_maintenance import truncate_for_test

TRUNCATE_TABLES = (
    "audit_events, human_decisions, recommendations, stage_attempts, analyses, "
    "retrieval_chunks, repository_documents, issues, repositories CASCADE"
)

# Deliberately identical across both organizations: the fake embedding
# adapter is a deterministic hash of the input text (see
# app.retrieval.embedding.FakeEmbeddingAdapter), so a chunk embedded from
# this exact text is the best possible candidate a query built from the
# same text could ever match. If tenant filtering happened anywhere other
# than the query's own WHERE clause -- e.g. only when formatting an API
# response -- this is the case most likely to leak: a same-content chunk
# from another organization would be indistinguishable from, or outrank,
# a legitimate same-tenant match.
MAXIMALLY_SIMILAR_CONTENT = (
    "Flask raises an unhandled AttributeError when the requested report id "
    "does not exist, instead of returning a clean 404 response to the caller."
)


@pytest.fixture()
def database_session() -> Iterator[Session]:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for retrieval tenant-isolation tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            truncate_for_test(connection, f"TRUNCATE {TRUNCATE_TABLES}")
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _add_repository(session: Session, *, organization_id, name: str) -> Repository:
    repository = Repository(
        tenant_id=organization_id, name=name, source_url=f"https://github.com/{name}"
    )
    session.add(repository)
    session.flush()
    return repository


def _add_closed_issue(
    session: Session, repository: Repository, *, external_number: int, title: str, body: str
) -> Issue:
    issue = Issue(
        repository_id=repository.id,
        external_number=external_number,
        title=title,
        body=body,
        state="closed",
        source_url=f"{repository.source_url}/issues/{external_number}",
    )
    session.add(issue)
    session.commit()
    return issue


def _add_chunk(
    session: Session,
    repository: Repository,
    issue: Issue,
    adapter: FakeEmbeddingAdapter,
    *,
    content: str,
) -> RetrievalChunk:
    chunk = RetrievalChunk(
        repository_id=repository.id,
        source_type="issue",
        issue_id=issue.id,
        chunk_index=0,
        external_number=issue.external_number,
        title=issue.title,
        source_url=issue.source_url,
        state=issue.state,
        content=content,
        content_hash="0" * 64,
        embedding=adapter.embed([content])[0],
        embedding_model=adapter.model_name,
        embedding_version=adapter.model_version,
    )
    session.add(chunk)
    session.commit()
    return chunk


def test_organization_a_cannot_retrieve_organization_bs_maximally_similar_chunk(
    database_session: Session,
) -> None:
    adapter = FakeEmbeddingAdapter()
    org_a_repo = _add_repository(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/retrieval"
    )
    org_b_repo = _add_repository(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/retrieval"
    )
    org_a_analyzed_issue = _add_closed_issue(
        database_session,
        org_a_repo,
        external_number=1,
        title="Report page crashes",
        body="Investigating.",
    )
    # A legitimate, same-tenant candidate exists too -- this proves org
    # B's chunk is excluded even when org A already has a real result to
    # return, not merely because org A's repository happens to be empty.
    org_a_other_issue = _add_closed_issue(
        database_session,
        org_a_repo,
        external_number=2,
        title="Same content, same tenant",
        body=MAXIMALLY_SIMILAR_CONTENT,
    )
    _add_chunk(
        database_session, org_a_repo, org_a_other_issue, adapter, content=MAXIMALLY_SIMILAR_CONTENT
    )
    org_b_issue = _add_closed_issue(
        database_session,
        org_b_repo,
        external_number=1,
        title="Identical evidence",
        body=MAXIMALLY_SIMILAR_CONTENT,
    )
    _add_chunk(
        database_session, org_b_repo, org_b_issue, adapter, content=MAXIMALLY_SIMILAR_CONTENT
    )

    request = RetrievalRequest(
        repository_id=org_a_repo.id,
        exclude_issue_id=org_a_analyzed_issue.id,
        query_text=MAXIMALLY_SIMILAR_CONTENT,
        max_results=3,
        max_excerpt_chars=320,
        min_similarity=0.0,  # permissive: proves exclusion is tenant-based, not score-based
        high_confidence_similarity=None,
    )

    result = retrieve_related_evidence(database_session, request, adapter)

    assert result.status == "ok"
    assert len(result.items) == 1
    assert result.items[0].identifier == f"issue:{org_a_other_issue.external_number}"
    assert all(item.identifier != f"issue:{org_b_issue.external_number}" for item in result.items)


def test_organization_bs_chunk_never_enters_organization_as_evidence_citations_or_prompt(
    database_session: Session,
) -> None:
    """The full, real workflow path: org A's analysis run must never cite,
    quote, or otherwise surface org B's maximally-similar chunk anywhere
    in the persisted recommendation -- evidence, citations, or the
    rendered AI prompt."""
    adapter = FakeEmbeddingAdapter()
    org_a_repo = _add_repository(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/full-run"
    )
    org_b_repo = _add_repository(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/full-run"
    )
    # Org A's own analyzed issue is deliberately worded *differently* from
    # org B's chunk -- if it reused MAXIMALLY_SIMILAR_CONTENT verbatim as
    # its own body, that text would legitimately appear in org A's own
    # deterministic evidence section regardless of retrieval, producing a
    # false positive for the leak check below.
    org_a_issue = _add_closed_issue(
        database_session,
        org_a_repo,
        external_number=1,
        title="Something entirely unrelated",
        body="A totally different problem report, worded nothing like org B's content.",
    )
    org_a_issue.state = (
        "open"  # the issue being analyzed; resolved-only ingestion is irrelevant here
    )
    database_session.commit()
    org_b_issue = _add_closed_issue(
        database_session,
        org_b_repo,
        external_number=1,
        title="Identical evidence",
        body=MAXIMALLY_SIMILAR_CONTENT,
    )
    _add_chunk(
        database_session, org_b_repo, org_b_issue, adapter, content=MAXIMALLY_SIMILAR_CONTENT
    )

    analysis = Analysis(
        issue_id=org_a_issue.id, status="queued", initiating_actor_id=DEFAULT_ORG_REVIEWER_ACTOR_ID
    )
    database_session.add(analysis)
    database_session.commit()

    process_workflow_run(database_session, analysis.id)

    recommendation = database_session.query(Recommendation).filter_by(analysis_id=analysis.id).one()
    content = json.loads(recommendation.content)

    retrieved_identifiers = {item["identifier"] for item in content["retrieved_evidence"]["items"]}
    assert f"issue:{org_b_issue.external_number}" not in retrieved_identifiers
    assert content["retrieved_evidence"]["items"] == []

    citations = content["ai_inference"]["citations"]
    assert not any("org-b" in citation for citation in citations)

    # The org B chunk's distinctive content never appears anywhere in the
    # persisted recommendation at all -- not evidence, not the AI
    # narrative, nowhere.
    assert MAXIMALLY_SIMILAR_CONTENT not in json.dumps(content)


def test_tenant_filtering_happens_inside_the_retrieval_query_not_only_the_api_response(
    database_session: Session,
) -> None:
    """Calls `run_retrieve_related_evidence_stage` directly -- the exact
    function the workflow calls -- proving the SQL query itself excludes
    another organization's chunks, rather than some later formatting step
    trimming them out of what an endpoint returns."""
    adapter = FakeEmbeddingAdapter()
    org_a_repo = _add_repository(
        database_session, organization_id=DEFAULT_ORGANIZATION_ID, name="org-a/stage-scope"
    )
    org_b_repo = _add_repository(
        database_session, organization_id=ISOLATION_DEMO_ORGANIZATION_ID, name="org-b/stage-scope"
    )
    org_a_issue = _add_closed_issue(
        database_session,
        org_a_repo,
        external_number=1,
        title="Analyzed issue",
        body=MAXIMALLY_SIMILAR_CONTENT,
    )
    org_b_issue = _add_closed_issue(
        database_session,
        org_b_repo,
        external_number=1,
        title="Identical",
        body=MAXIMALLY_SIMILAR_CONTENT,
    )
    _add_chunk(
        database_session, org_b_repo, org_b_issue, adapter, content=MAXIMALLY_SIMILAR_CONTENT
    )

    classification = Classification(label="bug", matched_rule="test", matched_keywords=())

    result = run_retrieve_related_evidence_stage(database_session, org_a_issue, classification)

    assert result.items == ()
