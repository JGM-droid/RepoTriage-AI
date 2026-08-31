"""Offline demo/import command: `python -m app.importer`.

Imports the committed fixture into the configured PostgreSQL database.
This is the default import path and performs no network call. A
PostgreSQL failure or a malformed fixture raises rather than being
reported as a skip or a successful import.
"""

from __future__ import annotations

import sys

from app.database import SessionLocal
from app.importer.schemas import ImporterError
from app.importer.service import import_fixture


def main() -> int:
    session = SessionLocal()
    try:
        summary = import_fixture(session)
    except ImporterError as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()

    print(
        f"Imported fixture into repository {summary.repository_id}: "
        f"{summary.inserted} new issue(s), {summary.skipped_existing} already present "
        f"({summary.considered} considered)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
