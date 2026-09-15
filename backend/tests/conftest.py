"""Test-session configuration.

Celery runs in eager (synchronous, in-process) mode for all tests so the
durable workflow executes deterministically without a real broker/worker,
against whatever isolated PostgreSQL database DATABASE_URL points at.
Embeddings use the deterministic, dependency-free fake adapter — not the
real local model — so the default test suite stays fast and makes no
model/network calls (a few retrieval tests override this explicitly to
exercise `LocalEmbeddingAdapter`'s own code directly, without loading a
real model). Both must be set before any test module imports
`app.config`/`app.main`.
"""

import os

os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("TRIAGE_RETRY_COUNTDOWN_SECONDS", "0")
os.environ.setdefault("EMBEDDING_PROVIDER", "fake")
