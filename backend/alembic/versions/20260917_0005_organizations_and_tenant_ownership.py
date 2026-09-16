"""organizations and tenant ownership

Additive migration (Milestone 3.1 Slice 1; see ADR 0013). Adds a new
`organizations` table and converts `Repository.tenant_id` -- a bare,
unenforced, nullable UUID reserved by ADR 0005 since migration 0001 -- into
a real, PostgreSQL-enforced foreign key. Does not touch 0001-0004 or any
other existing table.

Data steps, in dependency order:

  1. Create `organizations`.
  2. Insert exactly two deterministic, well-known organization rows (never
     randomly generated -- see `app.models.core.DEFAULT_ORGANIZATION_ID` /
     `ISOLATION_DEMO_ORGANIZATION_ID`, the same literals used here):
     the default organization (which will own every pre-existing
     repository) and a second, empty "isolation demo" organization whose
     only purpose is to make a real, distinct tenant provably present for
     future cross-tenant negative tests.
  3. Backfill every existing repository whose `tenant_id` is still NULL to
     the default organization.
  4. Add the foreign-key constraint (validates cleanly now that every row
     has a valid, existing organization).
  5. Set `tenant_id` NOT NULL.
  6. Set `tenant_id`'s server-side default to the default organization, so
     any future insert that omits it (the existing importer's
     `_get_or_create_repository`, and every existing test's `Repository(...)`
     construction) continues to produce a valid row unchanged -- this
     migration requires zero changes to the importer or to any existing
     test fixture.

The existing `ix_repositories_tenant_id` index (from migration 0001) is
reused as-is for the new foreign key; no redundant index is created.

Scope boundary: this migration establishes the tenant *data model* only.
It does not add request identity, authentication, role enforcement, or
API-level tenant scoping -- those remain deferred to later Milestone 3.1
slices (see ADR 0013). Critical Gate G4 (tenant isolation) is not yet
satisfied by this migration alone.

Revision ID: 20260917_0005
Revises: 20260916_0004
Create Date: 2026-09-17 00:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260917_0005"
down_revision: Union[str, Sequence[str], None] = "20260916_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Deterministic, well-known ids -- must exactly match
# app.models.core.DEFAULT_ORGANIZATION_ID / ISOLATION_DEMO_ORGANIZATION_ID.
# Migrations are intentionally self-contained (literal values, not an
# import of application code that may change independently later), so
# these are restated here rather than imported.
DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000101"
ISOLATION_DEMO_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000102"

_FK_NAME = "fk_repositories_tenant_id_organizations"


class DowngradeWouldDestroyTenantDataError(RuntimeError):
    """Raised when downgrading this migration would drop a custom
    organization (and, implicitly, any repository ownership pointing at
    it) that this migration has no way to reconstruct -- only the two
    deterministic built-in organizations survive a downgrade/upgrade
    cycle, because only their ids are known in advance."""


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
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
    )

    organizations = sa.table(
        "organizations",
        sa.column("id", sa.Uuid()),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
    )
    op.bulk_insert(
        organizations,
        [
            {
                "id": DEFAULT_ORGANIZATION_ID,
                "slug": "default-demo",
                "name": "Default Demo Organization",
            },
            {
                "id": ISOLATION_DEMO_ORGANIZATION_ID,
                "slug": "isolation-demo",
                "name": "Isolation Demo Organization",
            },
        ],
    )

    # A literal, not a bound parameter: both values are fixed constants
    # defined in this same file, never external input.
    op.execute(
        f"UPDATE repositories SET tenant_id = '{DEFAULT_ORGANIZATION_ID}'::uuid "
        "WHERE tenant_id IS NULL"
    )

    op.create_foreign_key(
        _FK_NAME,
        "repositories",
        "organizations",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("repositories", "tenant_id", nullable=False)
    op.alter_column(
        "repositories",
        "tenant_id",
        server_default=sa.text(f"'{DEFAULT_ORGANIZATION_ID}'::uuid"),
    )


def downgrade() -> None:
    # Guard, checked FIRST, before any schema change: downgrade drops the
    # `organizations` table entirely. Because only the two deterministic
    # built-in ids are known in advance, a later `upgrade head` can only
    # ever recreate THOSE two rows -- it has no way to reconstruct a
    # custom organization an operator created after this migration ran.
    # If one exists (empty or owning repositories, either way), refuse to
    # downgrade rather than silently destroying it or silently remapping
    # its repositories to the default organization. This check must run
    # before dropping the foreign key, changing nullability/defaults, or
    # dropping the table -- a raised exception here aborts the whole
    # migration inside Alembic's transactional DDL, so nothing below it
    # ever executes and `alembic_version` stays at this revision.
    connection = op.get_bind()
    custom_organization_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM organizations WHERE id NOT IN (:default_org, :isolation_org)"
        ),
        {"default_org": DEFAULT_ORGANIZATION_ID, "isolation_org": ISOLATION_DEMO_ORGANIZATION_ID},
    ).scalar()
    if custom_organization_count:
        raise DowngradeWouldDestroyTenantDataError(
            f"Refusing to downgrade migration 20260917_0005: {custom_organization_count} "
            "organization(s) beyond the two deterministic built-ins "
            f"({DEFAULT_ORGANIZATION_ID}, {ISOLATION_DEMO_ORGANIZATION_ID}) exist in this "
            "database. Downgrading drops the `organizations` table entirely; a later "
            "`upgrade head` can only recreate those two built-in rows, not any custom "
            "organization, so any repository owned by a custom organization would be left "
            "referencing an organization id that no longer exists. This migration will not "
            "delete that data or silently reassign those repositories to the default "
            "organization. To proceed, first reassign or remove every repository owned by a "
            "custom organization and delete the custom organization row(s), or accept that "
            "this database cannot be downgraded below this revision."
        )

    # Restores the pre-0005 nullable, unenforced schema. Deliberately does
    # NOT null out existing repositories.tenant_id values or delete any
    # repository/issue/analysis/other customer record -- only this
    # migration's own additions (the constraint, the default, and the
    # organizations table) are removed. Because the two organization rows
    # use fixed, deterministic ids -- and the guard above has already
    # proven no other organization exists -- a subsequent `upgrade head`
    # recreates them with the exact same ids and the already-populated
    # tenant_id values on existing repositories become valid again
    # automatically; no special-case re-backfill logic is needed.
    op.alter_column("repositories", "tenant_id", server_default=None)
    op.alter_column("repositories", "tenant_id", nullable=True)
    op.drop_constraint(_FK_NAME, "repositories", type_="foreignkey")
    op.drop_table("organizations")
