"""repository-grounded retrieval: pgvector chunks and documents

Additive migration (Milestone 2.3). Enables the `vector` extension and adds
two new tables: `repository_documents` (a bounded, offline documentation
fixture, mirroring how `issues` already stores the resolved-issue fixture)
and `retrieval_chunks` (one embedded, retrievable excerpt of either a
resolved issue or a document, scoped by `repository_id`). Does not touch
0001-0003 or any existing table.

No ANN index (ivfflat/hnsw) is created: the current corpus is a few hundred
rows for a single repository, where a plain sequential scan ordered by
vector distance is already fast and exact; an approximate index tuned for
a much larger corpus would be premature infrastructure at this scale (see
ADR 0009).

Revision ID: 20260916_0004
Revises: 20260914_0003
Create Date: 2026-09-16 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "20260916_0004"
down_revision: Union[str, Sequence[str], None] = "20260914_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSION = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "repository_documents",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("license_note", sa.String(length=500), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "path"),
    )

    op.create_table(
        "retrieval_chunks",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("external_number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSION), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["repository_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issue_id", "chunk_index", name="uq_retrieval_chunks_issue_chunk"),
        sa.UniqueConstraint(
            "document_id", "chunk_index", name="uq_retrieval_chunks_document_chunk"
        ),
        sa.CheckConstraint(
            "(issue_id IS NOT NULL AND document_id IS NULL) OR "
            "(issue_id IS NULL AND document_id IS NOT NULL)",
            name="ck_retrieval_chunks_exactly_one_source",
        ),
    )
    op.create_index(
        "ix_retrieval_chunks_repository_id", "retrieval_chunks", ["repository_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_chunks_repository_id", table_name="retrieval_chunks")
    op.drop_table("retrieval_chunks")
    op.drop_table("repository_documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
