"""API package aggregating route definitions and versioned routers."""

from fastapi import APIRouter

from app.api.routes.agents import router as agents_router
from app.api.routes.execution import router as execution_router
from app.api.routes.health import router as health_router
from app.api.routes.llm import router as llm_router
from app.api.routes.planning import router as planning_router
from app.api.routes.recovery import router as recovery_router
from app.api.routes.workers import router as workers_router
from app.api.routes.workflows import router as workflows_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(llm_router)
api_v1_router.include_router(agents_router)
api_v1_router.include_router(workflows_router)
api_v1_router.include_router(planning_router)
api_v1_router.include_router(execution_router)
api_v1_router.include_router(workers_router)
api_v1_router.include_router(recovery_router)

__all__ = ["api_v1_router"]
