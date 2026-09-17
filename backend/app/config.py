from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "repotriage-api"
    database_url: str = "postgresql+psycopg://localhost:5432/repotriage"
    cors_origin: str = "http://localhost:5173"
    redis_url: str = "redis://localhost:6379/0"
    triage_max_attempts: int = 3
    triage_retry_countdown_seconds: int = 1
    triage_stage_timeout_seconds: int = 30
    celery_task_always_eager: bool = False

    # AI gateway (Milestone 2.2). "mock" (the default) is deterministic and
    # makes zero network calls; it is what tests, CI, and the default demo
    # use. "openai" requires openai_api_key to be set, or the worker fails
    # fast at startup rather than silently falling back (see
    # app.ai_gateway.router.ensure_ai_gateway_configured).
    ai_provider: str = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    ai_provider_timeout_seconds: float = 20.0
    openai_input_price_per_million_usd: float = 0.15
    openai_output_price_per_million_usd: float = 0.60

    # Repository-grounded retrieval (Milestone 2.3). "local" (the default) is
    # a real, local, CPU-only embedding model requiring no paid API call and
    # no network access at runtime once its weights are baked into the
    # image (see backend/Dockerfile). "fake" is a deterministic,
    # hash-based embedder used only by the test suite, for speed and to
    # keep tests independent of any model file.
    embedding_provider: str = "local"
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    embedding_model_version: str = "fastembed-0.8.0"
    embedding_dimension: int = 384
    embedding_cache_dir: str = "/app/.fastembed_cache"
    retrieval_max_results: int = 3
    retrieval_max_excerpt_chars: int = 320
    # Below this cosine similarity, a candidate is not "relevant evidence" —
    # it is excluded rather than forced into the top-k just because it was
    # the best of a bad set. Recalibrated (see docs/adr/0009 and
    # tests/test_retrieval_calibration.py) against the *production*
    # build_query_text — issue title + bounded body excerpt + deduplicated
    # matched keywords only, with generic severity/rationale/proposed-action
    # boilerplate removed (that boilerplate previously homogenized every
    # issue's query into the same narrow score range). With boilerplate
    # removed, issue<->issue matching separates cleanly: measured negatives
    # topped out at ~0.575, confirmed positives (including #5755/#5756)
    # started at ~0.93. issue<->document matching is a different story: the
    # one measured document negative (~0.62) scored *higher* than the one
    # measured document positive (~0.62), an overlap a lexical-overlap check
    # did not reliably resolve either — no defensible document-specific
    # threshold exists yet with this small sample. 0.65 sits safely inside
    # the confident issue<->issue gap and also happens to exclude both
    # measured document examples, favoring missing a weak cross-genre match
    # over presenting unsupported evidence.
    retrieval_min_similarity: float = 0.65
    # Two-band confidence model (see app.retrieval.service and ADR 0009),
    # replacing an earlier narrow "close case" margin that only required
    # lexical support in [0.65, 0.70). A live demo showed that band was too
    # narrow: issue #5942 ("flask run should support pep723", an unrelated
    # packaging request) scored 0.7404 against issue #5755 ("Flask cannot
    # find /security/logic API") and cleared 0.70 on semantic score alone —
    # a false positive. Below retrieval_min_similarity: rejected. From
    # retrieval_min_similarity up to (not including) this threshold: "medium
    # confidence" — requires at least one shared *discriminative* term (see
    # app.retrieval.query_terms.extract_discriminative_terms, which strips
    # both stop words and corpus-generic terms like "flask"/"python"/"code"
    # so a match can't be manufactured from ubiquitous vocabulary). At or
    # above this threshold: "high confidence" — semantic score alone
    # qualifies, no lexical check needed. 0.90 is provisional, chosen
    # because the confirmed near-duplicate #5755->#5756 measured at ~0.928
    # (see tests/calibration/bge_similarity_calibration.json) — comfortably
    # above 0.90 — while the false positive #5942 (0.7404) and the
    # plausible-but-unconfirmed #5863 (0.7077, accepted only via its shared
    # "security" term) both sit well below it.
    retrieval_high_confidence_similarity: float = 0.90

    # Rate limiting (Milestone 2.6; see app.api.rate_limit and ADR 0012).
    # Applies only to the two state-changing endpoints (start triage, record
    # a human decision) -- never to read-only GET endpoints. Defaults are
    # demo-friendly: generous enough that a normal ownership walkthrough
    # never trips them, tight enough to catch an accidental rapid-duplicate
    # submission (e.g. a double-click or a buggy retry loop).
    rate_limit_triage_start_max_requests: int = 20
    rate_limit_triage_start_window_seconds: int = 60
    rate_limit_decision_max_requests: int = 20
    rate_limit_decision_window_seconds: int = 60

    # Milestone 3.1 Slice 2 (see ADR 0014). Defaults False in every
    # environment, including the live demo: the demo-actor listing
    # endpoint (GET /api/v1/demo/actors) is registered only when this is
    # explicitly true, so a fresh deployment never exposes it by accident.
    # Setting this alone never changes what identity/authorization actually
    # enforce -- it only controls whether the read-only actor-discovery
    # convenience endpoint exists at all.
    demo_mode_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _validate_retrieval_confidence_bands(self) -> "Settings":
        if self.retrieval_high_confidence_similarity < self.retrieval_min_similarity:
            raise ValueError(
                "retrieval_high_confidence_similarity must be >= retrieval_min_similarity "
                f"(got {self.retrieval_high_confidence_similarity} < "
                f"{self.retrieval_min_similarity})."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
