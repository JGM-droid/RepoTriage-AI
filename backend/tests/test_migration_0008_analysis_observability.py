"""Migration 0008 adds nullable durable context without rewriting old analyses."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config
from tests.db_maintenance import truncate_for_test

ALEMBIC_INI_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"
PRE_OBSERVABILITY_REVISION = "20260919_0007"
OBSERVABILITY_REVISION = "20260920_0008"
DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000101"


def _config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(ALEMBIC_INI_PATH.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_upgrade_adds_nullable_context_and_preserves_existing_analysis() -> None:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for migration 0008 tests.")
    config = _config(database_url)
    command.upgrade(config, PRE_OBSERVABILITY_REVISION)
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    repository_id, issue_id, analysis_id = uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as connection:
            truncate_for_test(
                connection, "TRUNCATE audit_events, analyses, issues, repositories CASCADE"
            )
            connection.execute(
                text(
                    "INSERT INTO repositories (id, tenant_id, name, source_url) "
                    "VALUES (:id, :tenant_id, 'migration/0008', 'https://example.test/0008')"
                ),
                {"id": repository_id, "tenant_id": DEFAULT_ORGANIZATION_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO issues (id, repository_id, external_number, title, body, state, "
                    "source_url) VALUES (:id, :repository_id, 1, 'Old', 'Old', 'closed', "
                    "'https://example.test/0008/issues/1')"
                ),
                {"id": issue_id, "repository_id": repository_id},
            )
            connection.execute(
                text(
                    "INSERT INTO analyses (id, issue_id, status) VALUES (:id, :issue_id, 'queued')"
                ),
                {"id": analysis_id, "issue_id": issue_id},
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == OBSERVABILITY_REVISION
            )
            row = connection.execute(
                text("SELECT id, correlation_id, traceparent FROM analyses WHERE id = :id"),
                {"id": analysis_id},
            ).one()
            assert row == (analysis_id, None, None)
            columns = {column["name"] for column in inspect(connection).get_columns("analyses")}
            assert {"correlation_id", "traceparent"} <= columns
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            truncate_for_test(
                connection, "TRUNCATE audit_events, analyses, issues, repositories CASCADE"
            )
        engine.dispose()
