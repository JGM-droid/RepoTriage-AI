from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.rate_limit import RateLimitExceeded
from app.api.v1.router import router as v1_router
from app.config import get_settings
from app.schemas.core import ErrorResponse

settings = get_settings()
app = FastAPI(title="RepoTriage AI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(v1_router, prefix="/api/v1")


@app.exception_handler(RateLimitExceeded)
def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    del request
    payload = ErrorResponse(
        error="rate_limited",
        message="Too many requests. Please slow down and try again shortly.",
    )
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=payload.model_dump(),
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )
