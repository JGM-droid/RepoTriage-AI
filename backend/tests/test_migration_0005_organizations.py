"""Migration 20260917_0005 (organizations and tenant ownership) upgrade,
backfill, and downgrade tests (Milestone 3.1 Slice 1; see ADR 0013).

Unlike every other test in this suite, these tests move a disposable
database's schema version directly -- downgrading to a specific prior
revision, seeding data at that schema shape, then upgrading again. The
fixture always re-upgrades to `head` in its teardown, even if a test
fails midway, so no other test file (which assumes the schema is already
at `head`) is ever left looking at a stale one.

Requires isolated PostgreSQL -- skips cleanly if unavailable. Never runs
against the live demo database: `database_url` is read from `DATABASE_URL`
exactly like every other integration test's fixture, and this file never
hardcodes a connection string.
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
PRE_ORGANIZATIONS_REVISION = "20260916_0004"

DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000101"
ISOLATION_DEMO_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000102"

_SEED_CHAIN_STATEMENTS = (
    "INSERT INTO repositories (id, tenant_id, name, source_url, created_at, updated_at) "
    "VALUES (:repo_id, NULL, :repo_name, :repo_url, now(), now())",
    "INSERT INTO issues (id, repository_id, external_number, title, body, state, source_url, "
    "created_at, updated_at) VALUES (:issue_id, :repo_id, 1, 'Migration test issue', "
    "'Migration test body', 'open', :issue_url, now(), now())",
    "INSERT INTO analyses (id, issue_id, status, current_stage, attempt_count, created_at, "
    "updated_at) VALUES (:analysis_id, :issue_id, 'completed', 'human_review', 1, now(), now())",
    "INSERT INTO recommendations (id, analysis_id, status, content, created_at, updated_at) "
    "VALUES (:recommendation_id, :analysis_id, 'proposed', '{}', now(), now())",
    "INSERT INTO human_decisions (id, recommendation_id, actor_id, decision, rationale, "
    "created_at, updated_at) VALUES (:decision_id, :recommendation_id, NULL, 'approve', "
    "'looks good', now(), now())",
    "INSERT INTO stage_attempts (id, analysis_id, stage, attempt_number, status, error, output, "
    "created_at, updated_at) VALUES (:stage_attempt_id, :analysis_id, 'classify', 1, "
    "'succeeded', NULL, '{}', now(), now())",
    "INSERT INTO audit_events (id, repository_id, issue_id, actor_id, event_type, metadata, "
    "created_at, updated_at) VALUES (:audit_event_id, :repo_id, :issue_id, NULL, "
    "'triage_status_transition', '{}', now(), now())",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI_PATH))
    # alembic.ini's `script_location = alembic` is intentionally relative
    # so the CLI works from `backend/` (the documented working directory
    # for every manual/CI `alembic` invocation). Alembic resolves a
    # relative script_location against the process's current working
    # directory, not the ini file's own directory -- so invoking the
    # Python API here, from a pytest process that may be launched from
    # the repository root (as CI's `pytest backend/tests` does), needs an
    # absolute override to find the same scripts folder regardless of
    # where pytest was started from.
    config.set_main_option("script_location", str(ALEMBIC_INI_PATH.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _swap_database_name(database_url: str, database_name: str) -> str:
    parts = urlsplit(database_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database_name}", "", ""))


def _maintenance_engine(database_url: str):
    """A connection to the `postgres` maintenance database, autocommit
    mode (required for `CREATE DATABASE`/`DROP DATABASE`, which cannot run
    inside a transaction block)."""
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
    """Creates `database_name` fresh on the same PostgreSQL server as
    `database_url` -- its own catalog and type-OID namespace, entirely
    independent of the shared test database."""
    _drop_database(database_url, database_name)
    engine = _maintenance_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()


# This file tests migration 20260917_0005 in isolation. Since later
# migrations (20260918_0006 and beyond) now exist above it, "head" no
# longer means "0005" -- every setup/mid-test operation in this file
# pins explicitly to 0005 itself, so this file's downgrade/upgrade
# exercises stay meaningful regardless of how many migrations come after
# it. Only the fixture's own teardown restores the database to the real
# `head`, so every other test file (which assumes the schema is fully
# migrated) is never left looking at a stale, partially-downgraded one.
_THIS_MIGRATION = "20260917_0005"


@pytest.fixture()
def migration_env(monkeypatch: pytest.MonkeyPatch):
    """Yields (database_url, alembic_config). Starts this test pinned at
    migration 20260917_0005 itself (not the database's real `head`, which
    may be later) and always restores the real `head` afterward,
    regardless of what the test itself does to the schema version in
    between."""
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for migration 0005 tests.")

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = _alembic_config(database_url)
    # Later append-only migrations may be active when the full suite
    # reaches this historical-migration test. Privileged cleanup is safe
    # only because DATABASE_URL names the disposable test database.
    _truncate_everything(database_url)
    # `command.upgrade` only ever walks forward -- given a target that is
    # already behind the database's current revision (true here whenever
    # a later migration, e.g. 20260918_0006, already exists above this
    # one and the database starts at real `head`), it silently no-ops
    # rather than downgrading. `command.downgrade` is the one that
    # actually walks backward to a specific target from wherever the
    # database currently is.
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


def test_clean_upgrade_from_an_empty_database_reaches_0005(
    migration_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately uses a separate, dedicated, disposable *database* --
    never `alembic downgrade base` on the shared test database. Migration
    0004's own downgrade drops the pgvector `vector` extension; doing that
    mid-suite poisons PostgreSQL's cached type OID for every other
    already-open or later connection in the same server/process, breaking
    unrelated, later tests that query a `vector` column with an opaque
    "cache lookup failed for type ..." error. A separate database has its
    own catalog and type OID namespace, so this is both the safer and the
    more faithful test of "a truly fresh, empty database", not a
    workaround."""
    base_database_url, _config = migration_env
    empty_database_url = _swap_database_name(base_database_url, "repotriage_m31_empty_check")
    _recreate_database(base_database_url, "repotriage_m31_empty_check")
    empty_db_config = _alembic_config(empty_database_url)

    # alembic/env.py reads Settings().database_url (DATABASE_URL), which
    # overrides whatever url is set directly on the Config object -- so the
    # env var must point at the empty database for the duration of this one
    # upgrade call, then be restored for the fixture's own teardown.
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
            assert version == "20260917_0005"

            org_count = connection.execute(text("SELECT count(*) FROM organizations")).scalar()
            assert org_count == 2

            org_ids = {
                str(row[0]) for row in connection.execute(text("SELECT id FROM organizations"))
            }
            assert org_ids == {DEFAULT_ORGANIZATION_ID, ISOLATION_DEMO_ORGANIZATION_ID}

            is_nullable = connection.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'repositories' AND column_name = 'tenant_id'"
                )
            ).scalar()
            assert is_nullable == "NO"
    finally:
        engine.dispose()
        _drop_database(base_database_url, "repotriage_m31_empty_check")


def test_a_pre_0005_repository_with_null_tenant_id_backfills_on_upgrade(migration_env) -> None:
    database_url, config = migration_env
    command.downgrade(config, PRE_ORGANIZATIONS_REVISION)
    _truncate_everything(database_url)

    ids = {
        "repo_id": uuid4(),
        "issue_id": uuid4(),
        "analysis_id": uuid4(),
        "recommendation_id": uuid4(),
        "decision_id": uuid4(),
        "stage_attempt_id": uuid4(),
        "audit_event_id": uuid4(),
    }
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        params = {
            **ids,
            "repo_name": "pallets/flask-migration-test",
            "repo_url": "https://github.com/pallets/flask-migration-test",
            "issue_url": "https://github.com/pallets/flask-migration-test/issues/1",
        }
        with engine.begin() as connection:
            for statement in _SEED_CHAIN_STATEMENTS:
                connection.execute(text(statement), params)
            null_tenant = connection.execute(
                text("SELECT tenant_id FROM repositories WHERE id = :id"), {"id": ids["repo_id"]}
            ).scalar()
            assert null_tenant is None  # sanity: genuinely null before upgrade

        command.upgrade(config, _THIS_MIGRATION)

        with engine.connect() as connection:
            backfilled_tenant = connection.execute(
                text("SELECT tenant_id FROM repositories WHERE id = :id"), {"id": ids["repo_id"]}
            ).scalar()
            assert str(backfilled_tenant) == DEFAULT_ORGANIZATION_ID

            # Every existing id and count survives the migration unchanged.
            for table, id_column, row_id in [
                ("repositories", "id", ids["repo_id"]),
                ("issues", "id", ids["issue_id"]),
                ("analyses", "id", ids["analysis_id"]),
                ("recommendations", "id", ids["recommendation_id"]),
                ("human_decisions", "id", ids["decision_id"]),
                ("stage_attempts", "id", ids["stage_attempt_id"]),
                ("audit_events", "id", ids["audit_event_id"]),
            ]:
                count = connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {id_column} = :id"), {"id": row_id}
                ).scalar()
                assert count == 1, f"{table} lost or duplicated row {row_id}"

            org_count = connection.execute(text("SELECT count(*) FROM organizations")).scalar()
            assert org_count == 2
    finally:
        engine.dispose()


def test_downgrade_then_upgrade_restores_valid_ownership(migration_env) -> None:
    """The normal, expected case: with only the two deterministic
    built-in organizations present (no custom organization), downgrade
    succeeds and a later upgrade restores valid ownership -- see the two
    `test_downgrade_refuses_*` tests below for the guarded, unsafe case."""
    database_url, config = migration_env
    _truncate_everything(database_url)

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    repo_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO repositories (id, tenant_id, name, source_url) "
                    "VALUES (:id, :tenant_id, 'pallets/flask-downgrade-test', "
                    "'https://github.com/pallets/flask-downgrade-test')"
                ),
                {"id": repo_id, "tenant_id": DEFAULT_ORGANIZATION_ID},
            )

        command.downgrade(config, "-1")

        with engine.connect() as connection:
            has_organizations_table = connection.execute(
                text("SELECT to_regclass('organizations') IS NOT NULL")
            ).scalar()
            assert has_organizations_table is False
            # The repository itself, and its tenant_id value, survive the
            # downgrade untouched -- only the constraint/table are removed.
            surviving_count = connection.execute(
                text("SELECT count(*) FROM repositories WHERE id = :id"), {"id": repo_id}
            ).scalar()
            assert surviving_count == 1

        command.upgrade(config, _THIS_MIGRATION)

        with engine.connect() as connection:
            org_count = connection.execute(text("SELECT count(*) FROM organizations")).scalar()
            assert org_count == 2
            tenant_id = connection.execute(
                text("SELECT tenant_id FROM repositories WHERE id = :id"), {"id": repo_id}
            ).scalar()
            assert str(tenant_id) == DEFAULT_ORGANIZATION_ID
    finally:
        engine.dispose()


def test_downgrade_refuses_when_an_extra_empty_custom_organization_exists(
    migration_env,
) -> None:
    """A custom organization -- even one that owns no repository yet --
    cannot be reconstructed by a later `upgrade head` (only the two
    deterministic built-in ids are known in advance), so downgrade must
    refuse rather than silently destroy it."""
    database_url, config = migration_env
    _truncate_everything(database_url)
    custom_org_id = uuid4()

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
                {"id": custom_org_id, "slug": f"custom-{custom_org_id.hex[:8]}", "name": "Custom"},
            )

        with pytest.raises(Exception, match="Refusing to downgrade migration 20260917_0005"):
            command.downgrade(config, "-1")

        with engine.connect() as connection:
            # Alembic remains at 0005 -- the guard raised before any DDL,
            # and Alembic's transactional DDL rolled back the attempt.
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "20260917_0005"

            # The organizations table, the FK, NOT NULL, the server
            # default, and the custom organization row are all still
            # exactly as they were before the refused downgrade.
            has_organizations_table = connection.execute(
                text("SELECT to_regclass('organizations') IS NOT NULL")
            ).scalar()
            assert has_organizations_table is True
            org_count = connection.execute(text("SELECT count(*) FROM organizations")).scalar()
            assert org_count == 3
            custom_org_still_present = connection.execute(
                text("SELECT count(*) FROM organizations WHERE id = :id"), {"id": custom_org_id}
            ).scalar()
            assert custom_org_still_present == 1

            fk_still_present = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE constraint_name = 'fk_repositories_tenant_id_organizations'"
                )
            ).scalar()
            assert fk_still_present == 1

            is_nullable = connection.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'repositories' AND column_name = 'tenant_id'"
                )
            ).scalar()
            assert is_nullable == "NO"

        # Clean up the custom organization so it doesn't leak into later
        # tests sharing this fixture's database.
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": custom_org_id}
            )
    finally:
        engine.dispose()


def test_downgrade_refuses_when_an_extra_custom_organization_owns_a_repository(
    migration_env,
) -> None:
    """The more consequential case: a custom organization that already
    owns a repository (and, transitively, every downstream issue/
    analysis/recommendation/decision/audit record). Downgrading must not
    leave that repository referencing an organization id that no longer
    exists, and must not silently reassign it to the default
    organization -- it must refuse outright."""
    database_url, config = migration_env
    _truncate_everything(database_url)
    custom_org_id = uuid4()
    repo_id = uuid4()

    ids = {
        "repo_id": repo_id,
        "issue_id": uuid4(),
        "analysis_id": uuid4(),
        "recommendation_id": uuid4(),
        "decision_id": uuid4(),
        "stage_attempt_id": uuid4(),
        "audit_event_id": uuid4(),
    }
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
                {
                    "id": custom_org_id,
                    "slug": f"custom-owner-{custom_org_id.hex[:8]}",
                    "name": "Custom Owner",
                },
            )
            # Seed the same representative record chain used by the
            # backfill test, but this time already owned by the custom
            # organization (not NULL) -- proving the guard fires
            # regardless of whether tenant_id was ever NULL.
            statements = list(_SEED_CHAIN_STATEMENTS)
            statements[0] = statements[0].replace(
                "VALUES (:repo_id, NULL, :repo_name, :repo_url, now(), now())",
                "VALUES (:repo_id, :custom_org_id, :repo_name, :repo_url, now(), now())",
            )
            params = {
                **ids,
                "custom_org_id": custom_org_id,
                "repo_name": "custom/owned-repo",
                "repo_url": "https://github.com/custom/owned-repo",
                "issue_url": "https://github.com/custom/owned-repo/issues/1",
            }
            for statement in statements:
                connection.execute(text(statement), params)

        with pytest.raises(Exception, match="Refusing to downgrade migration 20260917_0005"):
            command.downgrade(config, "-1")

        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert version == "20260917_0005"

            # Every representative downstream record, and the repository's
            # ownership value itself, survive the refused downgrade
            # completely unchanged.
            for table, id_column, row_id in [
                ("organizations", "id", custom_org_id),
                ("repositories", "id", ids["repo_id"]),
                ("issues", "id", ids["issue_id"]),
                ("analyses", "id", ids["analysis_id"]),
                ("recommendations", "id", ids["recommendation_id"]),
                ("human_decisions", "id", ids["decision_id"]),
                ("stage_attempts", "id", ids["stage_attempt_id"]),
                ("audit_events", "id", ids["audit_event_id"]),
            ]:
                count = connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {id_column} = :id"), {"id": row_id}
                ).scalar()
                assert count == 1, f"{table} row {row_id} did not survive the refused downgrade"

            tenant_id = connection.execute(
                text("SELECT tenant_id FROM repositories WHERE id = :id"), {"id": ids["repo_id"]}
            ).scalar()
            assert str(tenant_id) == str(custom_org_id)  # never silently reassigned

        # Clean up so this repository/organization doesn't leak into later
        # tests sharing this fixture's database.
        _truncate_everything(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM organizations WHERE id = :id"), {"id": custom_org_id}
            )
    finally:
        engine.dispose()


def test_normal_bootstrap_does_not_duplicate_organizations(migration_env) -> None:
    """Running `upgrade` to 20260917_0005 again on an already-migrated
    database (the same idempotent pattern the importer's fixture
    bootstrap already relies on) must never create duplicate organization
    rows."""
    database_url, config = migration_env

    command.upgrade(config, _THIS_MIGRATION)  # already there; must be a no-op

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            org_count = connection.execute(text("SELECT count(*) FROM organizations")).scalar()
            assert org_count == 2
    finally:
        engine.dispose()
