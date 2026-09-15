"""durable workflow state: stage attempts and idempotency

Revision ID: 20260914_0002
Revises: 20260831_0001
Create Date: 2026-09-14 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260914_0002"
down_revision: Union[str, Sequence[str], None] = "20260831_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column("analyses", sa.Column("current_stage", sa.String(length=64), nullable=True))
    op.add_column(
        "analyses",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_unique_constraint("uq_analyses_idempotency_key", "analyses", ["idempotency_key"])

    op.create_table(
        "stage_attempts",
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
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
        "ix_stage_attempts_analysis_id", "stage_attempts", ["analysis_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_stage_attempts_analysis_id", table_name="stage_attempts")
    op.drop_table("stage_attempts")
    op.drop_constraint("uq_analyses_idempotency_key", "analyses", type_="unique")
    op.drop_column("analyses", "attempt_count")
    op.drop_column("analyses", "current_stage")
    op.drop_column("analyses", "idempotency_key")
