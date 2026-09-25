from fastapi import APIRouter

from app.api.routes.decisions import router as decisions_router
from app.api.routes.dispatch import router as dispatch_router
from app.api.routes.operations import router as operations_router
from app.api.routes.health import router as health_router
from app.api.routes.recovery import router as recovery_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(recovery_router)

api_router.include_router(dispatch_router)

api_router.include_router(decisions_router)

api_router.include_router(operations_router)
