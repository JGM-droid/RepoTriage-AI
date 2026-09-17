"""Migration 0007 upgrade, preservation, and guarded downgrade tests."""

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
PRE_APPEND_ONLY_REVISION = "20260918_0006"
APPEND_ONLY_REVISION = "20260919_0007"
DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000101"
TRUNCATE_DATA = (
    "TRUNCATE audit_events, human_decisions, recommendations, stage_attempts, "
    "analyses, issues, repositories CASCADE"
)


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(ALEMBIC_INI_PATH.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _swap_database_name(database_url: str, database_name: str) -> str:
    parts = urlsplit(database_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database_name}", "", ""))


def _maintenance_engine(database_url: str):
    return create_engine(
        _swap_database_name(database_url, "postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
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


def _truncate(database_url: str) -> None:
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            truncate_for_test(connection, TRUNCATE_DATA)
    finally:
        engine.dispose()


@pytest.fixture()
def migration_env(monkeypatch: pytest.MonkeyPatch):
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for migration 0007 tests.")
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    _truncate(database_url)
    try:
        yield database_url, config
    finally:
        command.upgrade(config, "head")
        _truncate(database_url)
        get_settings.cache_clear()


def _seed_historical_event(database_url: str):
    repository_id, issue_id, event_id = uuid4(), uuid4(), uuid4()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO repositories (id, tenant_id, name, source_url) "
                    "VALUES (:id, :tenant_id, :name, :url)"
                ),
                {
                    "id": repository_id,
                    "tenant_id": DEFAULT_ORGANIZATION_ID,
                    "name": f"migration/{repository_id}",
                    "url": f"https://example.test/{repository_id}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO issues (id, repository_id, external_number, title, body, "
                    "state, source_url) VALUES (:id, :repository_id, 1, 'Historical', "
                    "'Body', 'closed', :url)"
                ),
                {
                    "id": issue_id,
                    "repository_id": repository_id,
                    "url": f"https://example.test/{repository_id}/issues/1",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO audit_events (id, repository_id, issue_id, event_type, metadata) "
                    "VALUES (:id, :repository_id, :issue_id, 'historical', '{\"kept\": true}')"
                ),
                {"id": event_id, "repository_id": repository_id, "issue_id": issue_id},
            )
    finally:
        engine.dispose()
    return event_id


def test_clean_empty_database_upgrade_reaches_new_head(
    migration_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url, _config = migration_env
    database_name = "repotriage_m32_empty_check"
    empty_url = _swap_database_name(base_url, database_name)
    _recreate_database(base_url, database_name)
    monkeypatch.setenv("DATABASE_URL", empty_url)
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(empty_url), "head")
        engine = create_engine(empty_url, connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == APPEND_ONLY_REVISION
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger "
                        "WHERE tgrelid = 'audit_events'::regclass AND NOT tgisinternal"
                    )
                )
                == 2
            )
        engine.dispose()
    finally:
        monkeypatch.setenv("DATABASE_URL", base_url)
        get_settings.cache_clear()
        _drop_database(base_url, database_name)


def test_0006_to_0007_preserves_historical_event_and_id(migration_env) -> None:
    database_url, config = migration_env
    command.downgrade(config, PRE_APPEND_ONLY_REVISION)
    event_id = _seed_historical_event(database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, event_type, metadata FROM audit_events WHERE id = :id"),
                {"id": event_id},
            ).one()
            assert row.id == event_id
            assert row.event_type == "historical"
            assert row.metadata == {"kept": True}
    finally:
        engine.dispose()


def test_downgrade_refuses_with_history_and_leaves_protection_intact(migration_env) -> None:
    database_url, config = migration_env
    event_id = _seed_historical_event(database_url)
    with pytest.raises(Exception, match="Refusing to downgrade migration 20260919_0007"):
        command.downgrade(config, "-1")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == APPEND_ONLY_REVISION
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM audit_events WHERE id = :id"), {"id": event_id}
                )
                == 1
            )
            connection.rollback()
            with pytest.raises(Exception, match="audit_events is append-only"):
                with connection.begin():
                    connection.execute(
                        text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id}
                    )
    finally:
        engine.dispose()


def test_empty_downgrade_restores_pre_0007_mutability_then_reupgrades(migration_env) -> None:
    database_url, config = migration_env
    command.downgrade(config, "-1")
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == PRE_APPEND_ONLY_REVISION
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger "
                        "WHERE tgrelid = 'audit_events'::regclass AND NOT tgisinternal"
                    )
                )
                == 0
            )
        event_id = _seed_historical_event(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_events SET event_type = 'mutable_before_0007' WHERE id = :id"),
                {"id": event_id},
            )
        _truncate(database_url)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == APPEND_ONLY_REVISION
            )
    finally:
        engine.dispose()
