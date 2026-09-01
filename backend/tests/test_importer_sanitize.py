"""Unit tests for untrusted-content sanitization (no database required)."""

from app.importer.sanitize import (
    MAX_BODY_LENGTH,
    MAX_TITLE_LENGTH,
    normalize_line_endings,
    sanitize_text,
    strip_control_characters,
)


def test_normalize_line_endings_converts_crlf_and_cr_to_lf() -> None:
    assert normalize_line_endings("a\r\nb\rc\nd") == "a\nb\nc\nd"


def test_strip_control_characters_removes_nul_and_unsafe_controls() -> None:
    value = "a\x00b\x01c\x7fd"

    assert strip_control_characters(value) == "abcd"


def test_strip_control_characters_preserves_tabs_and_newlines() -> None:
    value = "a\tb\nc"

    assert strip_control_characters(value) == value


def test_sanitize_text_normalizes_removes_controls_and_bounds_length() -> None:
    value = "Title\r\nwith\x00control\r-chars"

    result = sanitize_text(value, max_length=100)

    assert "\x00" not in result
    assert "\r" not in result
    assert result == "Title\nwithcontrol\n-chars"


def test_sanitize_text_truncates_oversized_content() -> None:
    value = "x" * (MAX_TITLE_LENGTH + 50)

    result = sanitize_text(value, max_length=MAX_TITLE_LENGTH)

    assert len(result) == MAX_TITLE_LENGTH


def test_sanitize_text_respects_documented_body_limit() -> None:
    value = "y" * (MAX_BODY_LENGTH + 1)

    result = sanitize_text(value, max_length=MAX_BODY_LENGTH)

    assert len(result) == MAX_BODY_LENGTH
