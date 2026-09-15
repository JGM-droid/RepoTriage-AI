# ADR 0009: Repository-Grounded pgvector Retrieval

**Status:** Accepted
**Date:** 2026-09-16

## Context

Milestone 2.3 requires genuine repository-grounded RAG: document/issue ingestion, embeddings,
pgvector retrieval, repository scoping, bounded context, provenance, and citations (roadmap
Milestone 2.3; rubric §E). This must integrate with the existing durable Celery workflow (ADR
0007) and provider-neutral AI gateway (ADR 0008) without becoming a second execution system, and
without weakening the deterministic classification/proposed-action/human-approval boundary (ADR
0003).

## Decision

### Vector store and embedding model

PostgreSQL with the `pgvector` extension is the primary vector store — image
`pgvector/pgvector:0.8.6-pg17-bookworm`, a pinned (not floating `pg17`) build verified from the
official `pgvector/pgvector` Docker Hub and GitHub pages. Swapping the demo database's image from
`postgres:17.7-alpine` to this image, reusing the existing named volume, was verified safe: same
PostgreSQL major version (17), matching `en_US.utf8` collation, no reinitialization ("appears to
contain a database; Skipping initialization"), all 100 issues and every existing analysis intact
afterward.

Embeddings are produced by **fastembed 0.8.0** (Apache-2.0, Qdrant-maintained), an ONNX Runtime
based library — explicitly chosen over `sentence-transformers`/PyTorch because it needs no GPU
and does not pull in the full PyTorch dependency stack, matching the instruction to prefer a
lightweight runtime. Its default text-embedding model, **`BAAI/bge-small-en-v1.5`** (MIT
licensed, 384-dimensional, CPU-only), is the selected model: small, fast (the full 100-issue +
6-document demo corpus embeds in ~10 seconds on CPU), and license-clean for a public portfolio
repository. Reproducibility comes from pinning the `fastembed==0.8.0` package version itself
(which determines exactly which ONNX conversion of the model gets downloaded), not a raw
Hugging Face revision string.

The model is baked into the Docker image at build time (`backend/Dockerfile`: a `RUN python -c
"from fastembed import TextEmbedding; TextEmbedding(...)"` step, after `pip install`), so no
container ever needs network access to embed at runtime — satisfying "no network calls during
normal runtime" without any lazy-download-on-first-use risk.

### Fake embedder for tests

`FakeEmbeddingAdapter` (`app/retrieval/embedding.py`) is a dependency-free, hash-derived,
deterministic embedder used only by the test suite (`EMBEDDING_PROVIDER=fake`, set as the
test-session default in `tests/conftest.py`, mirroring how Celery already runs in eager mode for
tests). It is fast and requires no model file, but is not semantically meaningful — tests that
need a specific, designed similarity ordering inject a small hand-crafted adapter instead
(`tests/test_retrieval.py::_HandCraftedAdapter`). The default test suite and demo make zero paid
or network embedding calls either way.

### Corpus: resolved issues and a bounded documentation fixture

All 100 already-imported `pallets/flask` issues are resolved (`state="closed"`) and are chunked
and embedded as retrievable material. A new, small, versioned fixture
(`backend/fixtures/pallets_flask/documents.json` + `documents_manifest.json`, 6 entries) adds
original, short paraphrased summaries of Flask's official Quickstart documentation
(BSD-3-Clause, Copyright 2010 Pallets), each with a real deep-linked `source_url` and an explicit
attribution/license note — not verbatim reproductions, and not a crawl of the whole
documentation. The manifest is checksum-verified on load (mirroring the existing issue fixture's
integrity check from ADR 0006), so a tampered or corrupted fixture fails loudly rather than
silently ingesting bad data. Neither ingestion path makes a network call or touches the live
GitHub API.

### Retrieval query, ranking, and a two-band confidence model

`retrieve_related_evidence` filters strictly by `repository_id` (the enforceable boundary today;
`Repository.tenant_id` exists for when Milestone 3.1 introduces real multi-tenant data) and
excludes the analyzed issue's own chunks, ranks by pgvector cosine distance, deduplicates down to
each source's single best-scoring chunk (so multiple adjacent chunks from one issue never crowd
out other evidence), and returns at most 3 results (`retrieval_max_results`) as a **cap, never a
quota** — fewer than 3 accepted candidates is a valid, expected outcome, and a rejected candidate
is never backfilled in just to fill out the slate.

**Superseded — historical only:** this ADR originally shipped with a single
`retrieval_min_similarity` floor of **0.35** and, later, a narrow "close case" lexical margin of
`[0.65, 0.70)`. Both were revised after implementation, through the normal correction/live-demo
cycle this project uses rather than a full ADR rewrite at each step; this section now documents
only the final, shipped decision. The `0.35` figure came from a query builder that embedded
generic classification/severity/rationale/proposed-action boilerplate alongside the issue's own
content, which homogenized similarity scores across unrelated issues and made a meaningful
threshold impossible to set; the retrieval query was corrected to use only issue-specific content
(normalized title, a bounded body excerpt, deduplicated matched keywords — the boilerplate fields
remain unchanged and available to the later `ai_inference` stage, just never embedded into the
retrieval query). The `[0.65, 0.70)` margin was too narrow in practice: a live demo surfaced issue
#5942 (an unrelated packaging/tooling request) scoring 0.7404 against issue #5755 and bypassing
lexical confirmation entirely by clearing that margin's 0.70 ceiling on semantic score alone — a
real, observed false positive, not a hypothetical one.

**Shipped decision — two confidence bands**, calibrated against the real corrected query builder,
the real `BAAI/bge-small-en-v1.5` model, and this corpus (full evidence in
`tests/calibration/bge_similarity_calibration.json`, which supersedes any threshold figure stated
elsewhere in this ADR):

- Below `retrieval_min_similarity` (**0.65**): rejected outright. Calibration: measured
  issue<->issue negatives topped out at ~0.575 while confirmed positives (including the
  near-duplicate #5755/#5756 pair) started at ~0.928 — a clean, defensible gap.
- From 0.65 up to (not including) `retrieval_high_confidence_similarity` (**0.90**, "medium
  confidence"): additionally requires at least one shared *discriminative* lexical term between
  the query and the candidate chunk (`app.retrieval.query_terms.extract_discriminative_terms`).
  This strips both stop words and two bounded, documented term sets that would otherwise let a
  match be manufactured from vocabulary carrying no real subject-matter signal: corpus-generic,
  high-document-frequency words measured directly against this corpus (e.g. "flask", "python",
  "code", "environment", "version" — every term at or above 10% document frequency across the
  405 retrieval chunks), and a small, general (not corpus-derived) set of low-content English
  words — personal pronouns, modal verbs, and hedge adverbs (e.g. "we", "can", "using") — that
  carry no discriminative concept in any corpus regardless of measured frequency. Both sets were
  completed iteratively during this correction's own live verification against the real demo
  corpus, which caught and fixed two further latent false positives (issues #5804 and #5836,
  each briefly accepted on nothing but shared boilerplate/low-content vocabulary) before either
  reached a real demo.
- At or above 0.90 ("high confidence"): accepted on semantic score alone, no lexical check.
  0.90 was chosen because the confirmed near-duplicate #5755/#5756 pair measures ~0.928,
  comfortably above it, while every measured false positive sits well below it.

**Known, accepted limitation — not force-fixed:** term-level lexical confirmation cannot fully
resolve polysemy. Issue #6139 ("AI junk", a demo-corpus filler issue that genuinely discusses
security vulnerabilities) is accepted for #5755 via the shared discriminative term "security" —
a different sense of "security" than #5755's URL-path name (`/security/logic`). This was
disclosed to and explicitly accepted by Jesse during Milestone 2.3 closeout rather than patched
with an issue-specific exception, which this project's correction process deliberately avoids.

### Workflow placement

`retrieve_related_evidence` is a new Celery stage between the deterministic `propose` and
`ai_inference`, running through the existing `_run_stage` unchanged — the same persisted-output,
resume-without-rerun, and per-attempt-uniqueness guarantees Milestone 2.1 built apply to it for
free, which is also what guarantees a redelivered task never re-queries or re-embeds. Only its
selected top-k `RetrievedRecord`s (never the full corpus, never the analyzed issue itself) are
passed into `AIRequest.retrieved_context`, consumed only by `ai_inference`'s prompt.
`classify`/`assess`/`propose` are completely unaffected — they run and are persisted first.

### Citations

`AIResponse` gained a `citations` field. The mock adapter deterministically cites every supplied
identifier. The OpenAI prompt explicitly labels retrieved excerpts as untrusted evidence, not
instructions, and instructs the model to cite only from the supplied identifier set using a
parsed `Citations: <id>, <id>` line; any invented identifier is rejected via
`validate_citations`/`InvalidCitationError`, which the router treats exactly like any other
controlled provider failure — falling back to the mock adapter within the same stage attempt, no
new mechanism required.

### Persistence: one additive migration, no vector database

Migration `20260916_0004` (revises `20260914_0003`; does not edit 0001–0003) enables `CREATE
EXTENSION IF NOT EXISTS vector`, adds `repository_documents` (mirroring `issues`'s shape) and
`retrieval_chunks` (`embedding vector(384)`, source type, denormalized `repository_id` for the
boundary filter, content hash, embedding model/version, and two per-source-type unique
constraints — `(issue_id, chunk_index)` and `(document_id, chunk_index)` — rather than one
combined constraint across both nullable columns, which SQL's NULL-is-distinct semantics would
not reliably enforce). No ANN index (ivfflat/hnsw) is created: at a few hundred rows for one
repository, an exact sequential scan ordered by vector distance is already fast; an approximate
index tuned for a much larger corpus would be premature infrastructure at this scale. Downgrade
drops both new tables and the extension, touching nothing from 0001–0003.

### Why full-text-only retrieval was rejected

A prior read-only review of this milestone recommended deterministic Postgres full-text search
as the smallest viable design, given the corpus size and the earlier absence of `pgvector` in the
running image. Jesse's explicit approval for this implementation requires a genuine pgvector/
embedding pipeline instead, accepting the added infrastructure (a different Postgres image, a new
migration, a baked-in model) in exchange for real semantic grounding — validated live: an issue
about a missing `/security/logic` endpoint correctly retrieved its actual near-duplicate
(`/security/login`, cosine similarity 0.928 in the final calibrated implementation) purely from
real embeddings, something lexical overlap alone would not reliably generalize across — though the
final design does still use a bounded, deterministic lexical check as a secondary confirmation
signal in the medium-confidence band (see "Retrieval query, ranking, and a two-band confidence
model" above), not as the primary retrieval mechanism. Full-text search (`pg_trgm`/`tsvector`, both
available in the pinned image without extra setup) remains available as a future secondary/hybrid
aid, per the approved decision, but nothing in this milestone currently uses it — vector search
alone met every requirement.

## Consequences

The demo database's image changed; its volume, credentials, and all existing rows were verified
intact through the swap. `fastembed`, `onnxruntime`, and `pgvector` are new runtime dependencies;
the backend image is larger (a ~130MB ONNX model baked in) and takes longer to build. Ingesting
the full demo corpus (100 issues + 6 documents, 405 chunks) takes ~10 seconds with the real model
and is fully idempotent — a second run embeds nothing new. The deterministic pipeline
(classification, severity, proposed action, evidence) remains completely unchanged and
independently correct.

## Security implications

Retrieved excerpts come from already-sanitized `issues.body`/`title` (Milestone 1.2) or the
curated, attributed documentation fixture — no new untrusted-text source is introduced. The
OpenAI prompt explicitly frames retrieved content as untrusted evidence, not instructions;
citation validation prevents a provider response from asserting a source that was never actually
supplied. Cross-repository leakage is prevented at the query itself (`repository_id` filter,
tested). Retrieved evidence, the AI narrative, deterministic inference, and the human decision
remain structurally distinct types and UI sections. Full adversarial prompt-injection fixture
testing remains explicitly deferred to Milestone 2.6.

## Testing/evidence

Chunking determinism/bounds/dedup; fake-embedder determinism; idempotent issue and document
ingestion (including a resolved-state filter and a tampered-manifest rejection); unchanged
content is never re-embedded, changed content is; same-repository isolation; analyzed-issue
exclusion; deterministic ranking and tie-breaking against designed vectors; the two-band
confidence model itself — a candidate above the old, superseded 0.70 close-case ceiling no longer
bypasses lexical confirmation; a high-confidence candidate (>=0.90) needs no lexical overlap; a
medium-confidence candidate needs a shared *discriminative* term and generic/low-content terms
alone cannot satisfy that requirement; top-3 is proven to be a cap, never a quota (fewer results
returned without backfilling); per-source deduplication; explicit `ok`/`empty`/
`insufficient_query`/`failed` status distinctions, including that a punctuation-only or otherwise
non-informative query is rejected before any embedding call or database query; full provenance
back to a stored `Issue`/`RepositoryDocument`; a dimension-mismatch failing clearly;
workflow-level tests proving the stage's persisted output, its dedicated `retrieval_evidence`
audit event (reflecting only accepted, never rejected, candidates), that a redelivered/resumed
task never re-queries or re-embeds, and that a genuine retrieval failure degrades to an explicit
empty result without blocking the workflow; AI-gateway tests proving valid citations are
accepted, invented ones are rejected via controlled fallback, and the mock adapter cites
deterministically; a frozen, versioned calibration fixture
(`tests/calibration/bge_similarity_calibration.json`) recording the real measured scores behind
both thresholds, including the specific false-positive regression examples (#5756 accepted,
#5942/#5804/#5836 rejected, #5863 accepted via "security") that must not silently regress; a live
demo against the real model and the real demo database — the actual persisted `Recommendation`
for issue #5755, not a rehearsal — showing exactly the citations this calibration predicts, zero
external network calls, and unchanged deterministic fields/human-review controls. Full backend
suite: 217 passed against a fresh isolated pgvector PostgreSQL database (migration
upgrade/downgrade/upgrade validated from an empty database); ruff clean. Jesse reviewed this
demo and explicitly approved Milestone 2.3 and R2-03 as complete.

## Revisit conditions

Revisit only through an explicitly approved ADR. The minimum-similarity and high-confidence
thresholds are now calibrated against the real, corrected query builder and a frozen evidence
snapshot (see Testing/evidence above and `tests/calibration/bge_similarity_calibration.json`),
superseding the "unproven" status this ADR originally recorded — but the calibration sample
remains small (roughly a dozen measured pairs), and the issue<->document category specifically
has no defensible dedicated threshold yet (the one measured document positive and negative
overlap). A wider empirical sample, and the known #6139-style polysemy limitation (term-level
lexical confirmation cannot distinguish different senses of the same word), are natural inputs to
Milestone 2.5's evaluation harness rather than something to silently patch with issue-specific
exceptions. Any future switch to `pg_trgm`/full-text as a genuine hybrid signal, any RAG
evaluation harness (Milestone 2.5), and any prompt-registry integration (Milestone 2.4) must
build on the contracts here rather than replacing them ad hoc.
