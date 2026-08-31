"""initial core data model

Revision ID: 20260831_0001
Revises:
Create Date: 2026-08-31 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url"),
    )
    op.create_index("ix_repositories_tenant_id", "repositories", ["tenant_id"], unique=False)
    op.create_table(
        "issues",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("external_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
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
        sa.UniqueConstraint("repository_id", "external_number"),
    )
    op.create_table(
        "analyses",
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyses_issue_id", "analyses", ["issue_id"], unique=False)
    op.create_table(
        "recommendations",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recommendations_analysis_id", "recommendations", ["analysis_id"], unique=False
    )
    op.create_table(
        "human_decisions",
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_human_decisions_actor_id", "human_decisions", ["actor_id"], unique=False)
    op.create_index(
        "ix_human_decisions_recommendation_id",
        "human_decisions",
        ["recommendation_id"],
        unique=False,
    )
    op.create_table(
        "audit_events",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"]),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"], unique=False)
    op.create_index("ix_audit_events_issue_id", "audit_events", ["issue_id"], unique=False)
    op.create_index(
        "ix_audit_events_repository_id", "audit_events", ["repository_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_repository_id", table_name="audit_events")
    op.drop_index("ix_audit_events_issue_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_human_decisions_recommendation_id", table_name="human_decisions")
    op.drop_index("ix_human_decisions_actor_id", table_name="human_decisions")
    op.drop_table("human_decisions")
    op.drop_index("ix_recommendations_analysis_id", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_index("ix_analyses_issue_id", table_name="analyses")
    op.drop_table("analyses")
    op.drop_table("issues")
    op.drop_index("ix_repositories_tenant_id", table_name="repositories")
    op.drop_table("repositories")
