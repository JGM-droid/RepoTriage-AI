"""Sanitization rules for untrusted imported issue text.

Imported issue titles and bodies are treated as untrusted data. This
module only removes unsafe bytes and enforces documented length limits;
it never interprets, executes, or follows any content it processes.
"""

from __future__ import annotations

import re

# Matches the model column limit for `Issue.title` (see app/models/core.py).
MAX_TITLE_LENGTH = 500
# Documented policy limit for `Issue.body`; large enough for real GitHub
# issue bodies while bounding storage and downstream processing cost.
MAX_BODY_LENGTH = 20000

# NUL and other C0/C1 control characters, excluding tab (\x09) and
# newline (\x0A), which are preserved as ordinary formatting.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def normalize_line_endings(value: str) -> str:
    """Normalize CRLF/CR line endings to a single LF."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def strip_control_characters(value: str) -> str:
    """Remove NUL and unsafe control characters, keeping tabs and newlines."""
    return _CONTROL_CHARACTERS.sub("", value)


def sanitize_text(value: str, *, max_length: int) -> str:
    """Normalize, strip unsafe characters, and bound the length of `value`.

    Oversized content is truncated to `max_length` rather than rejected,
    which is the documented policy for this importer.
    """
    normalized = normalize_line_endings(value)
    cleaned = strip_control_characters(normalized)
    return cleaned[:max_length]
