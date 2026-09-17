"""database-enforced append-only audit events

Revision ID: 20260919_0007
Revises: 20260918_0006
Create Date: 2026-09-19 00:00:00

Audit rows remain insertable and readable, but PostgreSQL rejects UPDATE,
DELETE, and TRUNCATE. Named RESTRICT foreign keys make parent-deletion
behavior explicit. Downgrade refuses while any protected history exists.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260919_0007"
down_revision: Union[str, Sequence[str], None] = "20260918_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FUNCTION_NAME = "repotriage_reject_audit_event_mutation"
ROW_TRIGGER_NAME = "trg_audit_events_reject_update_delete"
TRUNCATE_TRIGGER_NAME = "trg_audit_events_reject_truncate"


class DowngradeWouldRemoveAuditProtectionError(RuntimeError):
    """Raised rather than silently making existing history mutable."""


def upgrade() -> None:
    # Replace the anonymous 0001 constraints with stable, explicit names.
    op.drop_constraint("audit_events_issue_id_fkey", "audit_events", type_="foreignkey")
    op.drop_constraint("audit_events_repository_id_fkey", "audit_events", type_="foreignkey")
    op.create_foreign_key(
        "fk_audit_events_issue_id_issues",
        "audit_events",
        "issues",
        ["issue_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_audit_events_repository_id_repositories",
        "audit_events",
        "repositories",
        ["repository_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION {FUNCTION_NAME}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'audit_events is append-only: UPDATE, DELETE, and '
                              || 'TRUNCATE are prohibited';
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {ROW_TRIGGER_NAME}
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}()
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {TRUNCATE_TRIGGER_NAME}
            BEFORE TRUNCATE ON audit_events
            FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION_NAME}()
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    audit_count = connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one()
    if audit_count:
        raise DowngradeWouldRemoveAuditProtectionError(
            f"Refusing to downgrade migration 20260919_0007: {audit_count} protected "
            "audit event(s) exist. Downgrade would make that history mutable. Export and "
            "explicitly remove the rows before retrying, or keep this migration applied."
        )

    op.execute(sa.text(f"DROP TRIGGER {TRUNCATE_TRIGGER_NAME} ON audit_events"))
    op.execute(sa.text(f"DROP TRIGGER {ROW_TRIGGER_NAME} ON audit_events"))
    op.execute(sa.text(f"DROP FUNCTION {FUNCTION_NAME}()"))

    op.drop_constraint(
        "fk_audit_events_repository_id_repositories", "audit_events", type_="foreignkey"
    )
    op.drop_constraint("fk_audit_events_issue_id_issues", "audit_events", type_="foreignkey")
    op.create_foreign_key(
        "audit_events_repository_id_fkey",
        "audit_events",
        "repositories",
        ["repository_id"],
        ["id"],
    )
    op.create_foreign_key(
        "audit_events_issue_id_fkey", "audit_events", "issues", ["issue_id"], ["id"]
    )
