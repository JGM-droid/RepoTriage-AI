from functools import lru_cache

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
