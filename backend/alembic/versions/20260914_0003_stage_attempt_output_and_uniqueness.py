"""stage attempt output and per-attempt uniqueness

Additive follow-up to 0002: adds `stage_attempts.output` (persisted
deterministic stage output, so a redelivered task resumes from the first
incomplete stage instead of re-running ones that already succeeded) and a
database-level uniqueness invariant on (analysis_id, stage, attempt_number).

Kept as its own migration rather than folded into 0002 because 0002 was
already applied, in its original shape, to a running database before this
was designed; editing an already-applied migration in place would desync
that database's real schema from what its recorded alembic_version implies.

Revision ID: 20260914_0003
Revises: 20260914_0002
Create Date: 2026-09-14 00:00:01

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260914_0003"
down_revision: Union[str, Sequence[str], None] = "20260914_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stage_attempts", sa.Column("output", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_stage_attempts_analysis_stage_attempt",
        "stage_attempts",
        ["analysis_id", "stage", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_stage_attempts_analysis_stage_attempt", "stage_attempts", type_="unique")
    op.drop_column("stage_attempts", "output")
