"""Migration 20260918_0006 (actors and explicit tenant ownership) upgrade,
data-preservation, and downgrade tests (Milestone 3.1 Slice 2; see ADR
0014).

Mirrors the structure and safety conventions established by
`test_migration_0005_organizations.py`: the fixture always re-upgrades to
`head` in its teardown, a truly empty-database upgrade uses a separate,
dedicated, disposable database (never `alembic downgrade base` on the
shared test database -- see that file's docstring for why), and the
Alembic `Config`'s `script_location` is overridden to an absolute path so
this file runs correctly from the repository root, exactly as CI's
`pytest backend/tests` does.

Requires isolated PostgreSQL -- skips cleanly if unavailable. Never runs
against the live demo database.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from alembic.config import Config
from app.config import get_settings
from tests.db_maintenance import truncate_for_test

ALEMBIC_INI_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"
PRE_ACTORS_REVISION = "20260917_0005"

DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000101"
ISOLATION_DEMO_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000102"

DEFAULT_ORG_VIEWER_ACTOR_ID = "00000000-0000-0000-0000-000000000201"
DEFAULT_ORG_REVIEWER_ACTOR_ID = "00000000-0000-0000-0000-000000000202"
DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID = "00000000-0000-0000-0000-000000000203"
ISOLATION_ORG_VIEWER_ACTOR_ID = "00000000-0000-0000-0000-000000000204"
ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID = "00000000-0000-0000-0000-000000000205"

_EXPECTED_ACTORS = {
    DEFAULT_ORG_VIEWER_ACTOR_ID: (DEFAULT_ORGANIZATION_ID, "viewer"),
    DEFAULT_ORG_REVIEWER_ACTOR_ID: (DEFAULT_ORGANIZATION_ID, "reviewer"),
    DEFAULT_ORG_ADMINISTRATOR_ACTOR_ID: (DEFAULT_ORGANIZATION_ID, "administrator"),
    ISOLATION_ORG_VIEWER_ACTOR_ID: (ISOLATION_DEMO_ORGANIZATION_ID, "viewer"),
    ISOLATION_ORG_ADMINISTRATOR_ACTOR_ID: (ISOLATION_DEMO_ORGANIZATION_ID, "administrator"),
}

_THIS_MIGRATION = "20260918_0006"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(ALEMBIC_INI_PATH.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _swap_database_name(database_url: str, database_name: str) -> str:
    parts = urlsplit(database_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database_name}", "", ""))


def _maintenance_engine(database_url: str):
    maintenance_url = _swap_database_name(database_url, "postgres")
    return create_engine(
        maintenance_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )


def _drop_database(database_url: str, database_name: str) -> None:
    engine = _maintenance_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        engine.dispose()


def _recreate_database(database_url: str, database_name: str) -> None:
    _drop_database(database_url, database_name)
    engine = _maintenance_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()


@pytest.fixture()
def migration_env(monkeypatch: pytest.MonkeyPatch):
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for migration 0006 tests.")

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = _alembic_config(database_url)
    _truncate_everything(database_url)
    command.downgrade(config, _THIS_MIGRATION)
    try:
        yield database_url, config
    finally:
        command.upgrade(config, "head")
        get_settings.cache_clear()


def _truncate_everything(database_url: str) -> None:
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            truncate_for_test(
                connection,
                "TRUNCATE audit_events, human_decisions, recommendations, analyses, "
                "stage_attempts, issues, repositories CASCADE",
            )
            if connection.scalar(text("SELECT to_regclass('organizations') IS NOT NULL")):
                if connection.scalar(text("SELECT to_regclass('actors') IS NOT NULL")):
                    connection.execute(
                        text(
                            "DELETE FROM actors WHERE organization_id NOT IN "
                            "(:default_org, :isolation_org)"
                        ),
                        {
                            "default_org": DEFAULT_ORGANIZATION_ID,
                            "isolation_org": ISOLATION_DEMO_ORGANIZATION_ID,
                        },
                    )
                connection.execute(
                    text(
                        "DELETE FROM organizations WHERE id NOT IN (:default_org, :isolation_org)"
                    ),
                    {
                        "default_org": DEFAULT_ORGANIZATION_ID,
                        "isolation_org": ISOLATION_DEMO_ORGANIZATION_ID,
                    },
                )
    finally:
        engine.dispose()


def test_clean_upgrade_from_an_empty_database_reaches_0006(
    migration_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truly fresh database against a dedicated disposable database (see
    module docstring for why), proving the full 0001->0006 chain applies
    cleanly and seeds exactly the five deterministic actors."""
    base_database_url, _config = migration_env
    empty_database_url = _swap_database_name(base_database_url, "repotriage_m31s2_empty_check")
    _recreate_database(base_database_url, "repotriage_m31s2_empty_check")
    empty_db_config = _alembic_config(empty_database_url)

    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    get_settings.cache_clear()
    try:
        command.upgrade(empty_db_config, _THIS_MIGRATION)
    finally:
        monkeypatch.setenv("DATABASE_URL", base_database_url)
        get_settings.cache_clear()

    engine = create_engine(empty_database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "20260918_0006"

            actor_rows = connection.execute(
                text("SELECT id, organization_id, role, is_enabled FROM actors")
            ).all()
            assert len(actor_rows) == 5
            actual = {str(row.id): (str(row.organization_id), row.role) for row in actor_rows}
            assert actual == _EXPECTED_ACTORS
            assert all(row.is_enabled for row in actor_rows)

            has_default = connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'repositories' AND column_name = 'tenant_id'"
                )
            ).scalar()
            assert has_default is None
    finally:
        engine.dispose()
        _drop_database(base_database_url, "repotriage_m31s2_empty_check")


def test_0005_to_0006_preserves_existing_organization_and_repository_data(
    migration_env,
) -> None:
    """Downgrades to the pre-0006 (0005) shape, seeds a repository owned by
    the default organization exactly like Slice 1 left things, then
    upgrades back to 0006 -- proving existing data survives untouched and
    the five deterministic actors appear alongside it."""
    database_url, config = migration_env
    command.downgrade(config, PRE_ACTORS_REVISION)
    _truncate_everything(database_url)

    repo_id = uuid4()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO repositories (id, tenant_id, name, source_url, created_at, "
                    "updated_at) VALUES (:id, :tenant_id, 'pallets/flask-0006-test', "
                    "'https://github.com/pallets/flask-0006-test', now(), now())"
                ),
                {"id": repo_id, "tenant_id": DEFAULT_ORGANIZATION_ID},
            )
            # Sanity: the pre-0006 server default is still present here.
            has_default_before = connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'repositories' AND column_name = 'tenant_id'"
                )
            ).scalar()
            assert has_default_before is not None

        command.upgrade(config, _THIS_MIGRATION)

        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "20260918_0006"

            preserved_tenant = connection.execute(
                text("SELECT tenant_id FROM repositories WHERE id = :id"), {"id": repo_id}
            ).scalar()
            assert str(preserved_tenant) == DEFAULT_ORGANIZATION_ID

            org_count = connection.execute(text("SELECT count(*) FROM organizations")).scalar()
            assert org_count == 2

            actor_count = connection.execute(text("SELECT count(*) FROM actors")).scalar()
            assert actor_count == 5

            has_default_after = connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'repositories' AND column_name = 'tenant_id'"
                )
            ).scalar()
            assert has_default_after is None
    finally:
        engine.dispose()


def test_repository_creation_without_tenant_id_fails_after_0006(migration_env) -> None:
    """The core Slice 2 guarantee: once the server default is gone, an
    insert that omits `tenant_id` fails closed at the database level."""
    database_url, _config = migration_env
    _truncate_everything(database_url)

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            with pytest.raises(Exception, match="null value|not-null|violates not-null"):
                with connection.begin():
                    connection.execute(
                        text(
                            "INSERT INTO repositories (id, name, source_url) "
                            "VALUES (:id, 'no-tenant-repo', "
                            "'https://example.test/no-tenant-repo')"
                        ),
                        {"id": uuid4()},
                    )
    finally:
        engine.dispose()


def test_downgrade_then_upgrade_restores_deterministic_actors(migration_env) -> None:
    """The normal, expected case: with only the five deterministic
    built-in actors present, downgrade succeeds and a later upgrade
    restores them -- see the two `test_downgrade_refuses_*` tests below
    for the guarded, unsafe case."""
    database_url, config = migration_env

    command.downgrade(config, "-1")
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == PRE_ACTORS_REVISION
            has_actors_table = connection.execute(
                text("SELECT to_regclass('actors') IS NOT NULL")
            ).scalar()
            assert has_actors_table is False
            has_default = connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'repositories' AND column_name = 'tenant_id'"
                )
            ).scalar()
            assert has_default is not None  # restored

        command.upgrade(config, _THIS_MIGRATION)

        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "20260918_0006"
            actor_count = connection.execute(text("SELECT count(*) FROM actors")).scalar()
            assert actor_count == 5
    finally:
        engine.dispose()


def test_downgrade_refuses_when_an_extra_custom_actor_exists(migration_env) -> None:
    database_url, config = migration_env
    custom_actor_id = uuid4()

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO actors (id, organization_id, slug, display_name, role) "
                    "VALUES (:id, :org_id, :slug, 'Custom Actor', 'viewer')"
                ),
                {
                    "id": custom_actor_id,
                    "org_id": DEFAULT_ORGANIZATION_ID,
                    "slug": f"custom-{custom_actor_id.hex[:8]}",
                },
            )

        with pytest.raises(Exception, match="Refusing to downgrade migration 20260918_0006"):
            command.downgrade(config, "-1")

        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "20260918_0006"

            has_actors_table = connection.execute(
                text("SELECT to_regclass('actors') IS NOT NULL")
            ).scalar()
            assert has_actors_table is True

            actor_count = connection.execute(text("SELECT count(*) FROM actors")).scalar()
            assert actor_count == 6  # five deterministic + the custom one

            custom_still_present = connection.execute(
                text("SELECT count(*) FROM actors WHERE id = :id"), {"id": custom_actor_id}
            ).scalar()
            assert custom_still_present == 1

            has_default = connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'repositories' AND column_name = 'tenant_id'"
                )
            ).scalar()
            assert has_default is None  # still removed -- downgrade never ran

        # Clean up so the custom actor doesn't leak into other tests
        # sharing this fixture's database.
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM actors WHERE id = :id"), {"id": custom_actor_id})
    finally:
        engine.dispose()


def test_normal_bootstrap_does_not_duplicate_actors(migration_env) -> None:
    """Running `upgrade head` again on an already-migrated database must
    never create duplicate actor rows."""
    database_url, config = migration_env

    command.upgrade(config, _THIS_MIGRATION)  # already there; must be a no-op

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            actor_count = connection.execute(text("SELECT count(*) FROM actors")).scalar()
            assert actor_count == 5
    finally:
        engine.dispose()
