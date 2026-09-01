"""Bounded, read-only capture of public GitHub issues.

This module is never used by the default offline demo/import path (see
`app.importer.service.import_fixture`); it exists only to reproduce how
the committed fixture was captured. It performs read-only HTTP GET
requests against the public GitHub REST API and never comments on,
labels, closes, or otherwise mutates the source repository.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from app.importer.service import DEFAULT_CONFIG, ImporterConfig

GITHUB_API_ROOT = "https://api.github.com"
USER_AGENT = "RepoTriage-AI-Importer (read-only)"

PageFetcher = Callable[[str, str, int, int], Sequence[dict[str, Any]]]


def http_page_fetcher(owner: str, repo: str, page: int, page_size: int) -> list[dict[str, Any]]:
    """Fetch one page of the public GitHub issues endpoint (read-only)."""
    url = (
        f"{GITHUB_API_ROOT}/repos/{owner}/{repo}/issues?state=all&per_page={page_size}&page={page}"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _to_raw_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_number": item["number"],
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "state": item.get("state", "open"),
        "source_url": item.get("html_url", ""),
        "is_pull_request": "pull_request" in item,
    }


def fetch_live_issues(
    owner: str,
    repo: str,
    *,
    config: ImporterConfig = DEFAULT_CONFIG,
    page_fetcher: PageFetcher = http_page_fetcher,
) -> list[dict[str, Any]]:
    """Bounded, read-only capture of public issues for one repository.

    Stops after `config.max_pages` pages or `config.max_issues`
    non-pull-request issues, whichever comes first. Pull-request-shaped
    records are excluded before counting toward the issue limit.
    """
    collected: list[dict[str, Any]] = []
    for page in range(1, config.max_pages + 1):
        items = page_fetcher(owner, repo, page, config.page_size)
        if not items:
            break
        for item in items:
            raw = _to_raw_record(item)
            if raw["is_pull_request"]:
                continue
            collected.append(raw)
            if len(collected) >= config.max_issues:
                return collected
    return collected
