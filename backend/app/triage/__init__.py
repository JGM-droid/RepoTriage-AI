"""Deterministic issue triage workflow.

Implements the explicit stages `classify -> retrieve_fixture_evidence ->
assess -> propose -> human_review` using only stored issue data and
deterministic rules. No network access, AI/LLM calls, embeddings, or
randomness are used. See docs/adr/0003-mandatory-human-review-boundary.md
and docs/adr/0004-defer-async-workflow-technology-selection.md.
"""
