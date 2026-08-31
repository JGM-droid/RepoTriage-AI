"""Tests for the committed offline fixture: provenance, integrity, and the
end-to-end offline import path. Uses small temporary fixtures where the
full 100-issue fixture is not needed."""

import hashlib
import json
import os
import socket
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.importer.schemas import ImportIntegrityError, ImportValidationError
from app.importer.service import DEFAULT_FIXTURE_DIR, import_fixture, load_fixture
from app.models.core import Issue, Repository

TRUNCATE_CORE_TABLES = (
    "TRUNCATE audit_events, human_decisions, recommendations, "
    "analyses, issues, repositories CASCADE"
)


@pytest.fixture()
def database_session() -> Session:
    database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL is required for fixture import tests.")

    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as connection:
            connection.execute(text(TRUNCATE_CORE_TABLES))
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _write_fixture(tmp_path: Path, records: list[dict]) -> Path:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    issues_json = json.dumps(records, indent=2, sort_keys=True) + "\n"
    (fixture_dir / "issues.json").write_text(issues_json, encoding="utf-8")
    checksum = hashlib.sha256(issues_json.encode("utf-8")).hexdigest()
    manifest = {
        "source_provider": "github",
        "source_repository": "example/example",
        "repository_source_url": "https://github.com/example/example",
        "captured_at": "2026-08-31T00:00:00Z",
        "selection_rules": "test fixture",
        "issue_count": len(records),
        "fixture_format_version": "1.0",
        "content_checksum_algorithm": "sha256",
        "content_checksum": checksum,
    }
    (fixture_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return fixture_dir


def _record(number: int) -> dict:
    return {
        "external_number": number,
        "title": f"Issue {number}",
        "body": "Body",
        "state": "open",
        "source_url": f"https://github.com/example/example/issues/{number}",
    }


def test_committed_fixture_has_exactly_one_hundred_issues() -> None:
    manifest, records = load_fixture(DEFAULT_FIXTURE_DIR)

    assert manifest["issue_count"] == 100
    assert len(records) == 100
    assert manifest["source_repository"] == "pallets/flask"


def test_committed_fixture_issues_are_sorted_by_external_number() -> None:
    _, records = load_fixture(DEFAULT_FIXTURE_DIR)

    numbers = [record["external_number"] for record in records]

    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers)


def test_committed_fixture_excludes_pull_requests() -> None:
    _, records = load_fixture(DEFAULT_FIXTURE_DIR)

    assert all(record.get("is_pull_request") is False for record in records)


def test_load_fixture_accepts_a_small_valid_fixture(tmp_path: Path) -> None:
    fixture_dir = _write_fixture(tmp_path, [_record(1), _record(2)])

    manifest, records = load_fixture(fixture_dir)

    assert manifest["issue_count"] == 2
    assert len(records) == 2


def test_load_fixture_rejects_a_tampered_checksum(tmp_path: Path) -> None:
    fixture_dir = _write_fixture(tmp_path, [_record(1)])
    issues_path = fixture_dir / "issues.json"
    issues_path.write_text(issues_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ImportIntegrityError):
        load_fixture(fixture_dir)


def test_load_fixture_rejects_a_mismatched_issue_count(tmp_path: Path) -> None:
    fixture_dir = _write_fixture(tmp_path, [_record(1), _record(2)])
    manifest_path = fixture_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["issue_count"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ImportIntegrityError):
        load_fixture(fixture_dir)


def test_load_fixture_rejects_malformed_json(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "broken"
    fixture_dir.mkdir()
    (fixture_dir / "issues.json").write_text("not json", encoding="utf-8")
    (fixture_dir / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ImportValidationError):
        load_fixture(fixture_dir)


def test_import_fixture_performs_no_network_call(
    tmp_path: Path, database_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir = _write_fixture(tmp_path, [_record(1), _record(2)])

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("import_fixture must not open network sockets")

    monkeypatch.setattr(socket.socket, "connect", _blocked)

    summary = import_fixture(database_session, fixture_dir=fixture_dir)

    assert summary.inserted == 2


def test_committed_fixture_imports_successfully_into_postgresql(
    database_session: Session,
) -> None:
    summary = import_fixture(database_session)

    assert summary.inserted == 100
    assert len(database_session.execute(select(Issue)).scalars().all()) == 100


def test_import_fixture_is_idempotent(tmp_path: Path, database_session: Session) -> None:
    fixture_dir = _write_fixture(tmp_path, [_record(1), _record(2), _record(3)])

    first = import_fixture(database_session, fixture_dir=fixture_dir)
    second = import_fixture(database_session, fixture_dir=fixture_dir)

    assert first.inserted == 3
    assert second.inserted == 0
    assert len(database_session.execute(select(Repository)).scalars().all()) == 1
    assert len(database_session.execute(select(Issue)).scalars().all()) == 3
