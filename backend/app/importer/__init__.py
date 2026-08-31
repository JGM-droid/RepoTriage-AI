"""Bounded, offline-first importer for public GitHub issues.

This package implements Milestone 1.2 (R1-02): a repository-agnostic
importer boundary that turns sanitized, provenance-tracked issue records
into rows in the existing `Repository`/`Issue` schema. See
`docs/adr/0006-bounded-issue-importer-design.md` for the design decisions
and `docs/adr/0002-public-fixture-data-policy.md` for the fixture policy.
"""
