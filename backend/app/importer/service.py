"""Core import service: validate, sanitize, bound, and persist issues.

Validation and sanitization happen entirely before any database write, and
the database write itself runs in a single transaction, so a malformed
record or a database failure never leaves a partially imported fixture.
Repeat imports are idempotent because inserts rely on the existing unique
constraints on `repositories.source_url` and
`(issues.repository_id, issues.external_number)`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.importer.sanitize import MAX_BODY_LENGTH, MAX_TITLE_LENGTH, sanitize_text
from app.importer.schemas import (
    ImportIntegrityError,
    ImportValidationError,
    RawIssueRecord,
    parse_raw_record,
)
from app.models.core import Issue, Repository

FIXTURE_FORMAT_VERSION = "1.0"

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "pallets_flask"


@dataclass(frozen=True)
class ImporterConfig:
    """Bounds applied to every import, live or fixture-based."""

    max_issues: int = 100
    max_pages: int = 3
    page_size: int = 100


DEFAULT_CONFIG = ImporterConfig()


@dataclass(frozen=True)
class ImportSummary:
    """Outcome of one import call."""

    repository_id: UUID
    considered: int
    inserted: int
    skipped_existing: int


def _sanitize_record(record: RawIssueRecord) -> RawIssueRecord:
    title = sanitize_text(record.title, max_length=MAX_TITLE_LENGTH)
    body = sanitize_text(record.body, max_length=MAX_BODY_LENGTH)
    return record.model_copy(update={"title": title, "body": body})


def prepare_records(
    raw_records: Sequence[dict[str, Any]], config: ImporterConfig = DEFAULT_CONFIG
) -> list[RawIssueRecord]:
    """Validate, sanitize, and bound raw records to `config` limits.

    Pull-request-shaped records are excluded. The result never exceeds
    `config.max_issues` records. Raises `ImportValidationError` on the
    first structurally malformed record, without touching the database.
    """
    prepared: list[RawIssueRecord] = []
    for payload in raw_records:
        record = parse_raw_record(payload)
        if record.is_pull_request:
            continue
        prepared.append(_sanitize_record(record))
        if len(prepared) >= config.max_issues:
            break
    return prepared


def _get_or_create_repository(session: Session, *, name: str, source_url: str) -> Repository:
    stmt = (
        pg_insert(Repository)
        .values(name=name, source_url=source_url)
        .on_conflict_do_nothing(index_elements=["source_url"])
    )
    session.execute(stmt)
    return session.execute(
        select(Repository).where(Repository.source_url == source_url)
    ).scalar_one()


def import_records(
    session: Session,
    *,
    repository_name: str,
    repository_source_url: str,
    raw_records: Sequence[dict[str, Any]],
    config: ImporterConfig = DEFAULT_CONFIG,
) -> ImportSummary:
    """Import a bounded set of raw issue records for one repository.

    Runs inside a single transaction: any failure rolls back the entire
    import, including repository creation, so no partial fixture is ever
    left behind.
    """
    records = prepare_records(raw_records, config)

    try:
        repository = _get_or_create_repository(
            session, name=repository_name, source_url=repository_source_url
        )
        inserted = 0
        for record in records:
            stmt = (
                pg_insert(Issue)
                .values(
                    repository_id=repository.id,
                    external_number=record.external_number,
                    title=record.title,
                    body=record.body,
                    state=record.state,
                    source_url=record.source_url,
                )
                .on_conflict_do_nothing(index_elements=["repository_id", "external_number"])
                .returning(Issue.id)
            )
            result = session.execute(stmt)
            if result.first() is not None:
                inserted += 1
        session.commit()
    except Exception:
        session.rollback()
        raise

    return ImportSummary(
        repository_id=repository.id,
        considered=len(records),
        inserted=inserted,
        skipped_existing=len(records) - inserted,
    )


def load_fixture(fixture_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and integrity-check a committed fixture directory.

    Raises `ImportValidationError` if the manifest or issue file is
    missing or not valid JSON, and `ImportIntegrityError` if the fixture
    content checksum or issue count does not match the manifest.
    """
    manifest_path = fixture_dir / "manifest.json"
    issues_path = fixture_dir / "issues.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        issues_bytes = issues_path.read_bytes()
        raw_records = json.loads(issues_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportValidationError("fixture files are missing or not valid JSON") from exc

    actual_checksum = hashlib.sha256(issues_bytes).hexdigest()
    if manifest.get("content_checksum") != actual_checksum:
        raise ImportIntegrityError("fixture content checksum does not match its manifest")

    if manifest.get("issue_count") != len(raw_records):
        raise ImportIntegrityError("fixture manifest issue count does not match fixture content")

    if manifest.get("fixture_format_version") != FIXTURE_FORMAT_VERSION:
        raise ImportIntegrityError("fixture format version is not supported")

    return manifest, raw_records


def import_fixture(
    session: Session,
    fixture_dir: Path | None = None,
    config: ImporterConfig = DEFAULT_CONFIG,
) -> ImportSummary:
    """Import the committed offline fixture. Performs no network call."""
    manifest, raw_records = load_fixture(fixture_dir or DEFAULT_FIXTURE_DIR)
    return import_records(
        session,
        repository_name=manifest["source_repository"],
        repository_source_url=manifest["repository_source_url"],
        raw_records=raw_records,
        config=config,
    )
