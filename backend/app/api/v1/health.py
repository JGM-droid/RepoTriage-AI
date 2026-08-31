from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.schemas.system import ServiceStatus

router = APIRouter(tags=["system"])


def verify_database() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


@router.get("/health", response_model=ServiceStatus)
def health() -> ServiceStatus:
    return ServiceStatus(status="healthy")


@router.get(
    "/readiness",
    response_model=ServiceStatus,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ServiceStatus}},
)
def readiness() -> ServiceStatus | JSONResponse:
    try:
        verify_database()
    except SQLAlchemyError:
        unavailable = ServiceStatus(status="unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=unavailable.model_dump(),
        )
    return ServiceStatus(status="ready")
