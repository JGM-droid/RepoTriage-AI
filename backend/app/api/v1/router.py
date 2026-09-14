from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.issues import router as issues_router
from app.api.v1.triage import router as triage_router

router = APIRouter()
router.include_router(health_router)
router.include_router(issues_router)
router.include_router(triage_router)
