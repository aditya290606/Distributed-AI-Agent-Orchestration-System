"""API route definitions and endpoints."""

from app.api.routes.agents import router as agents_router
from app.api.routes.execution import router as execution_router
from app.api.routes.health import router as health_router
from app.api.routes.llm import router as llm_router
from app.api.routes.planning import router as planning_router
from app.api.routes.recovery import router as recovery_router
from app.api.routes.workers import router as workers_router
from app.api.routes.workflows import router as workflows_router

__all__ = [
    "agents_router",
    "execution_router",
    "health_router",
    "llm_router",
    "planning_router",
    "recovery_router",
    "workers_router",
    "workflows_router",
]
