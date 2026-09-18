import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind, Status, StatusCode

from app.api.identity import DEMO_ACTOR_HEADER, IdentityError
from app.api.rate_limit import RateLimitExceeded
from app.api.v1.demo import router as demo_router
from app.api.v1.router import router as v1_router
from app.config import Settings, get_settings
from app.observability import (
    CORRELATION_HEADER,
    configure_observability,
    correlation_context,
    monotonic,
    record_http,
    span,
    valid_or_new_correlation_id,
)
from app.schemas.core import ErrorResponse

logger = logging.getLogger("repotriage.api")


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


def _identity_error_handler(request: Request, exc: IdentityError) -> JSONResponse:
    del request
    payload = ErrorResponse(error=exc.error, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


def create_app(settings: Settings | None = None) -> FastAPI:
    """The one place `FastAPI()` is constructed (Milestone 3.1 Slice 2
    correction; see ADR 0014). Takes `settings` explicitly, defaulting to
    a fresh `get_settings()` read -- never a module-level snapshot
    captured once at import time. This makes the demo-actor route's
    presence/absence a direct, deterministic function of the `settings`
    passed in: real startup (`app = create_app()` below) always reads the
    process's actual environment at call time, exactly once, the same
    way a production ASGI server import always has; tests can instead
    construct `create_app(Settings(demo_mode_enabled=True))` and get a
    real, independently-constructed app with no shared mutable state and
    no reliance on `importlib.reload` or import-order timing."""
    settings = settings or get_settings()
    configure_observability(settings.service_name, settings.otel_exporter_otlp_endpoint)
    app = FastAPI(title="RepoTriage AI API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.cors_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", DEMO_ACTOR_HEADER, CORRELATION_HEADER, "Idempotency-Key"],
        expose_headers=[CORRELATION_HEADER, "traceparent"],
    )

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        correlation_id = valid_or_new_correlation_id(request.headers.get(CORRELATION_HEADER))
        started = monotonic()
        status_code = 500
        route = "unmatched"
        with (
            correlation_context(correlation_id),
            span(
                "http.request",
                traceparent=request.headers.get("traceparent"),
                kind=SpanKind.SERVER,
                attributes={"http.request.method": request.method},
            ) as current_span,
        ):
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers[CORRELATION_HEADER] = correlation_id
                span_context = current_span.get_span_context()
                if span_context.is_valid:
                    response.headers["traceparent"] = (
                        f"00-{span_context.trace_id:032x}-{span_context.span_id:016x}-"
                        f"{'01' if span_context.trace_flags.sampled else '00'}"
                    )
                return response
            finally:
                route_object = request.scope.get("route")
                route = getattr(route_object, "path", "unmatched")
                duration = monotonic() - started
                current_span.set_attribute("http.route", route)
                current_span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    current_span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
                record_http(request.method, route, status_code, duration)
                logger.info(
                    "http_request_complete method=%s route=%s status=%s duration_ms=%.3f",
                    request.method,
                    route,
                    status_code,
                    duration * 1000,
                )

    app.include_router(v1_router, prefix="/api/v1")
    # Only registered when explicitly enabled, so the read-only
    # demo-actor-discovery endpoint does not exist at all -- not merely
    # return an error -- in any deployment that hasn't opted in (the live
    # demo has not opted in as of this slice; default is disabled).
    if settings.demo_mode_enabled:
        app.include_router(demo_router, prefix="/api/v1")

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_exception_handler(IdentityError, _identity_error_handler)

    return app


app = create_app()
