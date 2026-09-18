"""persist analysis correlation and W3C trace context

Revision ID: 20260920_0008
Revises: 20260919_0007
Create Date: 2026-09-20 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260920_0008"
down_revision: Union[str, Sequence[str], None] = "20260919_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.add_column("analyses", sa.Column("traceparent", sa.String(55), nullable=True))
    op.create_index("ix_analyses_correlation_id", "analyses", ["correlation_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_analyses_correlation_id", table_name="analyses")
    op.drop_column("analyses", "traceparent")
    op.drop_column("analyses", "correlation_id")
