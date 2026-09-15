"""Embedding adapter interface, the local production/demo adapter, and a
deterministic fake adapter for tests.

Both adapters expose the same shape (`model_name`, `model_version`,
`dimension`, `embed(texts)`) so `ingest.py` and `service.py` never need to
know which one is in use. Selection is via `Settings.embedding_provider`
("local" by default, "fake" for tests only) — never a network call, and
never a paid API, in either case.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol


class EmbeddingDimensionMismatchError(RuntimeError):
    """The configured dimension and the adapter's actual output disagree.

    Always a configuration/model-mismatch bug, never a transient failure —
    raised immediately rather than silently truncating or padding a vector.
    """


# BGE's documented retrieval instruction for query-side text (see the
# BAAI/bge-small-en-v1.5 model card): applied only when embedding a search
# query, never when embedding a stored passage/document chunk. Because
# passage embedding is completely unaffected, adding this required no
# corpus re-embedding.
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbeddingAdapter(Protocol):
    model_name: str
    model_version: str
    dimension: int
    query_instruction: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def embed_query(adapter: EmbeddingAdapter, query_text: str) -> list[float]:
    """Embed one query string, applying the adapter's query-side
    instruction. Never used for passage/document embedding — see
    `ingest.py`, which calls `adapter.embed(...)` directly."""
    return adapter.embed([f"{adapter.query_instruction}{query_text}"])[0]


class LocalEmbeddingAdapter:
    """A real, local, CPU-only embedding model (fastembed + ONNX Runtime;
    see ADR 0009). Requires no paid API call. Makes no network call at
    runtime once the model is present in `cache_dir` — baked into the
    Docker image at build time (see backend/Dockerfile)."""

    query_instruction = BGE_QUERY_INSTRUCTION

    def __init__(
        self,
        *,
        model_name: str,
        model_version: str,
        dimension: int,
        cache_dir: str,
    ) -> None:
        from fastembed import TextEmbedding

        self.model_name = model_name
        self.model_version = model_version
        self.dimension = dimension
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [vector.tolist() for vector in self._model.embed(list(texts))]
        for vector in vectors:
            if len(vector) != self.dimension:
                raise EmbeddingDimensionMismatchError(
                    f"configured embedding_dimension={self.dimension} does not match "
                    f"the {self.model_name} model's actual output dimension "
                    f"({len(vector)})."
                )
        return vectors


class FakeEmbeddingAdapter:
    """Deterministic, dependency-free, hash-derived embedding for tests.

    Same text always produces the same unit vector; different texts produce
    effectively unrelated vectors (this is a speed/reproducibility fake, not
    a semantic approximation — tests that need a specific, designed
    similarity ordering should inject their own small adapter instead).
    Carries the same `query_instruction` as `LocalEmbeddingAdapter` and
    records every text it was asked to embed in `.calls`, so a test can
    assert the instruction was actually applied to a query and never to a
    passage."""

    model_name = "fake-hash-embedder"
    model_version = "1"
    query_instruction = BGE_QUERY_INSTRUCTION

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.calls: list[str] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.extend(texts)
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            block = hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
            for offset in range(0, len(block) - 3, 4):
                if len(values) >= self.dimension:
                    break
                as_int = int.from_bytes(block[offset : offset + 4], "big")
                values.append((as_int / 0xFFFFFFFF) * 2 - 1)
            counter += 1
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


def build_embedding_adapter(settings) -> EmbeddingAdapter:
    if settings.embedding_provider == "fake":
        return FakeEmbeddingAdapter(dimension=settings.embedding_dimension)
    return LocalEmbeddingAdapter(
        model_name=settings.embedding_model_name,
        model_version=settings.embedding_model_version,
        dimension=settings.embedding_dimension,
        cache_dir=settings.embedding_cache_dir,
    )
