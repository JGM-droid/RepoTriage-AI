"""Deterministic, bounded, non-overlapping chunking.

Same input text always produces the same ordered list of chunks: no
randomness, no clock, no network. Adjacent short paragraphs are packed
together up to `max_chars` so a source with many short paragraphs doesn't
explode into many near-empty chunks; a single paragraph longer than
`max_chars` is hard-wrapped. Identical chunk content within one source is
deduplicated. Chunk identity is `(source, chunk_index)`, assigned in the
same order every time for the same input.
"""

from __future__ import annotations

import hashlib
import re

DEFAULT_MAX_CHUNK_CHARS = 500

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []

    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT.split(normalized) if p.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    packed: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            packed.append(current)
            current = ""
        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            for start in range(0, len(paragraph), max_chars):
                packed.append(paragraph[start : start + max_chars])
    if current:
        packed.append(current)

    seen: set[str] = set()
    deduped: list[str] = []
    for chunk in packed:
        if chunk in seen:
            continue
        seen.add(chunk)
        deduped.append(chunk)
    return deduped
