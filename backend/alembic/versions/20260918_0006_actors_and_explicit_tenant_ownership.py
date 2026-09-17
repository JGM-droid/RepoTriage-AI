"""actors and explicit tenant ownership

Additive migration (Milestone 3.1 Slice 2; see ADR 0014). Adds a new
`actors` table -- synthetic demo identities, NOT production user accounts
-- and removes `repositories.tenant_id`'s temporary Slice 1 `server_default`
so every repository-creation path must supply an explicit organization from
this point on. Does not touch 0001-0005 or any other existing table.

Data steps, in dependency order:

  1. Create `actors`: `organization_id` (FK to `organizations`, RESTRICT),
     `slug` (unique), `display_name`, `role` (CHECK-constrained to
     viewer/reviewer/administrator), `is_enabled`.
  2. Insert exactly five deterministic, well-known actor rows (never
     randomly generated -- see `app.models.core`'s `*_ACTOR_ID` constants,
     the same literals used here): three in the default organization (one
     per role) and two in the isolation-demo organization (viewer and
     administrator), enough to prove cross-tenant denial for every role
     without a third role that adds no new isolation coverage.
  3. Drop `repositories.tenant_id`'s server-side default. Every row that
     already has a `tenant_id` value (backfilled by migration 0005) is
     completely unaffected -- this only changes what happens on a *future*
     insert that omits the column, which now fails closed (NOT NULL
     violation) instead of silently landing in the default organization.
  4. Add `analyses.initiating_actor_id` (nullable FK to `actors`,
     RESTRICT): the durable, immutable record of which actor initiated
     each analysis, added here (rather than as a separate migration)
     because it depends on `actors` existing and this migration has not
     yet shipped. The worker re-resolves this reference from PostgreSQL
     on every attempt -- see `app.workflow.tasks._authorize_workflow_run`
     -- rather than trusting anything in the Celery task payload, which
     carries only the analysis id both before and after this migration.

Scope boundary: this migration adds identity, ownership, and
workflow-authorization *data*, not enforcement by itself. Role/tenant
checks live in `app.api.identity`/`app.api.scoping`/
`app.workflow.tasks`, not in the database schema -- the CHECK constraint
on `role` only prevents an invalid role value from ever being stored, and
the `initiating_actor_id` foreign key only prevents it from pointing at a
nonexistent actor; neither enforces authorization on its own.

Revision ID: 20260918_0006
Revises: 20260917_0005
Create Date: 2026-09-18 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260918_0006"
down_revision: Union[str, Sequence[str], None] = "20260917_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Deterministic, well-known ids -- must exactly match the corresponding
# constants in app.models.core. Migrations are intentionally self-contained
# (literal values, not an import of application code that may change
# independently later), so these are restated here rather than imported.
DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000101"
ISOLATION_DEMO_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000102"

DEFAULT_ORG_VIEWER_ACTOR_ID = "00000000-0000-0000-0000-000000000201"
DEFAULT_ORG_REVIEWER_ACTOR_ID = "00000000-0000-0000-0000-000000000202"
DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID = "00000000-0000-0000-0000-000000000203"
ISOLATION_ORG_VIEWER_ACTOR_ID = "00000000-0000-0000-0000-000000000204"
ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID = "00000000-0000-0000-0000-000000000205"

_DETERMINISTIC_ACTOR_IDS = (
    DEFAULT_ORG_VIEWER_ACTOR_ID,
    DEFAULT_ORG_REVIEWER_ACTOR_ID,
    DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID,
    ISOLATION_ORG_VIEWER_ACTOR_ID,
    ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
)

ROLE_VIEWER = "viewer"
ROLE_REVIEWER = "reviewer"
ROLE_ADMINISTRATOR = "administrator"


class DowngradeWouldDestroyActorDataError(RuntimeError):
    """Raised when downgrading this migration would drop a custom actor
    that this migration has no way to reconstruct -- only the five
    deterministic built-in actors survive a downgrade/upgrade cycle,
    because only their ids are known in advance."""


def upgrade() -> None:
    op.create_table(
        "actors",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.UniqueConstraint("slug"),
        sa.CheckConstraint(
            f"role IN ('{ROLE_VIEWER}', '{ROLE_REVIEWER}', '{ROLE_ADMINISTRATOR}')",
            name="ck_actors_role",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_actors_organization_id_organizations",
            ondelete="RESTRICT",
        ),
    )

    actors = sa.table(
        "actors",
        sa.column("id", sa.Uuid()),
        sa.column("organization_id", sa.Uuid()),
        sa.column("slug", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("role", sa.String()),
    )
    op.bulk_insert(
        actors,
        [
            {
                "id": DEFAULT_ORG_VIEWER_ACTOR_ID,
                "organization_id": DEFAULT_ORGANIZATION_ID,
                "slug": "default-demo-viewer",
                "display_name": "Default Demo Viewer",
                "role": ROLE_VIEWER,
            },
            {
                "id": DEFAULT_ORG_REVIEWER_ACTOR_ID,
                "organization_id": DEFAULT_ORGANIZATION_ID,
                "slug": "default-demo-reviewer",
                "display_name": "Default Demo Reviewer",
                "role": ROLE_REVIEWER,
            },
            {
                "id": DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID,
                "organization_id": DEFAULT_ORGANIZATION_ID,
                "slug": "default-demo-administrator",
                "display_name": "Default Demo Administrator",
                "role": ROLE_ADMINISTRATOR,
            },
            {
                "id": ISOLATION_ORG_VIEWER_ACTOR_ID,
                "organization_id": ISOLATION_DEMO_ORGANIZATION_ID,
                "slug": "isolation-demo-viewer",
                "display_name": "Isolation Demo Viewer",
                "role": ROLE_VIEWER,
            },
            {
                "id": ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID,
                "organization_id": ISOLATION_DEMO_ORGANIZATION_ID,
                "slug": "isolation-demo-administrator",
                "display_name": "Isolation Demo Administrator",
                "role": ROLE_ADMINISTRATOR,
            },
        ],
    )

    op.alter_column("repositories", "tenant_id", server_default=None)

    op.add_column("analyses", sa.Column("initiating_actor_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_analyses_initiating_actor_id_actors",
        "analyses",
        "actors",
        ["initiating_actor_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Guard, checked FIRST, before any schema change -- mirrors migration
    # 20260917_0005's downgrade guard exactly. Because only the five
    # deterministic built-in actor ids are known in advance, a later
    # `upgrade head` can only ever recreate THOSE five rows -- it has no
    # way to reconstruct a custom actor an operator created after this
    # migration ran. If one exists, refuse to downgrade rather than
    # silently destroying it or silently reassigning whatever it was used
    # for. This check must run before dropping the table or restoring the
    # server default -- a raised exception here aborts the whole migration
    # inside Alembic's transactional DDL, so nothing below it ever
    # executes and `alembic_version` stays at this revision.
    connection = op.get_bind()
    placeholders = ", ".join(f":id{i}" for i in range(len(_DETERMINISTIC_ACTOR_IDS)))
    custom_actor_count = connection.execute(
        sa.text(f"SELECT count(*) FROM actors WHERE id NOT IN ({placeholders})"),
        {f"id{i}": value for i, value in enumerate(_DETERMINISTIC_ACTOR_IDS)},
    ).scalar()
    if custom_actor_count:
        raise DowngradeWouldDestroyActorDataError(
            f"Refusing to downgrade migration 20260918_0006: {custom_actor_count} actor(s) "
            "beyond the five deterministic built-ins exist in this database. Downgrading "
            "drops the `actors` table entirely; a later `upgrade head` can only recreate "
            "those five built-in rows, not any custom actor. This migration will not delete "
            "that data. To proceed, first remove every custom actor row, or accept that this "
            "database cannot be downgraded below this revision."
        )

    # Drop the analyses -> actors link first: it references `actors`, so
    # it must go before that table does. Losing this denormalized
    # reference is within the same accepted scope as dropping `actors`
    # itself -- the guard above has already proven no actor beyond the
    # five deterministic built-ins exists, so nothing irreplaceable is
    # lost (analysis/issue/repository rows themselves are untouched).
    op.drop_constraint("fk_analyses_initiating_actor_id_actors", "analyses", type_="foreignkey")
    op.drop_column("analyses", "initiating_actor_id")

    # Restores the pre-0006 Slice 1 behavior exactly: any future insert
    # that omits tenant_id lands in the default organization again.
    op.alter_column(
        "repositories",
        "tenant_id",
        server_default=sa.text(f"'{DEFAULT_ORGANIZATION_ID}'::uuid"),
    )
    op.drop_table("actors")
