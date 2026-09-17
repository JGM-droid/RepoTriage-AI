"""Privileged cleanup helpers for disposable test databases only.

Production code has no bypass. Test cleanup temporarily disables the two
stable append-only triggers using table-owner DDL, truncates isolated test
state, and immediately restores both triggers in the same transaction.
"""

from sqlalchemy import Connection, text

ROW_TRIGGER_NAME = "trg_audit_events_reject_update_delete"
TRUNCATE_TRIGGER_NAME = "trg_audit_events_reject_truncate"


def truncate_for_test(connection: Connection, statement: str) -> None:
    present = connection.execute(
        text(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid = 'audit_events'::regclass AND NOT tgisinternal "
            "AND tgname IN (:row_trigger, :truncate_trigger)"
        ),
        {"row_trigger": ROW_TRIGGER_NAME, "truncate_trigger": TRUNCATE_TRIGGER_NAME},
    ).scalar_one()
    if present == 2:
        connection.execute(text(f"ALTER TABLE audit_events DISABLE TRIGGER {ROW_TRIGGER_NAME}"))
        connection.execute(
            text(f"ALTER TABLE audit_events DISABLE TRIGGER {TRUNCATE_TRIGGER_NAME}")
        )
    connection.execute(text(statement))
    if present == 2:
        connection.execute(text(f"ALTER TABLE audit_events ENABLE TRIGGER {ROW_TRIGGER_NAME}"))
        connection.execute(text(f"ALTER TABLE audit_events ENABLE TRIGGER {TRUNCATE_TRIGGER_NAME}"))
