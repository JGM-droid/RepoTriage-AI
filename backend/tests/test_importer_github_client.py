"""Unit tests for bounded, read-only live capture (no network access)."""

from app.importer.github_client import fetch_live_issues
from app.importer.service import ImporterConfig


def _make_page(numbers: list[int], *, pull_request_numbers: set[int] | None = None) -> list[dict]:
    pull_request_numbers = pull_request_numbers or set()
    items = []
    for number in numbers:
        item = {
            "number": number,
            "title": f"Issue {number}",
            "body": "Body",
            "state": "open",
            "html_url": f"https://github.com/example/example/issues/{number}",
        }
        if number in pull_request_numbers:
            item["pull_request"] = {"url": "https://api.github.com/example"}
        items.append(item)
    return items


def test_fetch_live_issues_stops_after_configured_max_issues() -> None:
    config = ImporterConfig(max_issues=5, max_pages=3, page_size=10)
    pages = {1: _make_page(list(range(1, 11)))}

    def fake_fetcher(owner: str, repo: str, page: int, page_size: int) -> list[dict]:
        return pages.get(page, [])

    result = fetch_live_issues("example", "example", config=config, page_fetcher=fake_fetcher)

    assert len(result) == 5
    assert [r["external_number"] for r in result] == [1, 2, 3, 4, 5]


def test_fetch_live_issues_stops_after_configured_max_pages() -> None:
    config = ImporterConfig(max_issues=100, max_pages=2, page_size=10)
    calls: list[int] = []

    def fake_fetcher(owner: str, repo: str, page: int, page_size: int) -> list[dict]:
        calls.append(page)
        return _make_page([page * 100 + i for i in range(10)])

    result = fetch_live_issues("example", "example", config=config, page_fetcher=fake_fetcher)

    assert calls == [1, 2]
    assert len(result) == 20


def test_fetch_live_issues_excludes_pull_request_shaped_records() -> None:
    config = ImporterConfig(max_issues=10, max_pages=1, page_size=10)

    def fake_fetcher(owner: str, repo: str, page: int, page_size: int) -> list[dict]:
        return _make_page([1, 2, 3], pull_request_numbers={2})

    result = fetch_live_issues("example", "example", config=config, page_fetcher=fake_fetcher)

    assert [r["external_number"] for r in result] == [1, 3]


def test_fetch_live_issues_stops_early_when_a_page_is_empty() -> None:
    config = ImporterConfig(max_issues=100, max_pages=3, page_size=10)
    calls: list[int] = []

    def fake_fetcher(owner: str, repo: str, page: int, page_size: int) -> list[dict]:
        calls.append(page)
        return _make_page([1, 2]) if page == 1 else []

    result = fetch_live_issues("example", "example", config=config, page_fetcher=fake_fetcher)

    assert calls == [1, 2]
    assert len(result) == 2
