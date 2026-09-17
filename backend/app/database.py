from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Iterator[Session]:
    """The one shared FastAPI session dependency. Every router-level
    session dependency (`get_issue_session`, `get_triage_session`, ...) is
    an alias for this exact function object, not a separate copy with an
    identical body -- so `app.api.identity`'s actor-resolution dependency
    always shares the same session (and therefore the same request-scoped
    transaction) as whatever route it protects, and a test that overrides
    one router's session dependency automatically overrides identity
    resolution too, without needing a second override."""
    with SessionLocal() as session:
        yield session
