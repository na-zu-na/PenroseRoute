from fastapi import APIRouter

from app.api.routes.customers import router as customers_router
from app.api.routes.drivers import router as drivers_router
from app.api.routes.health import router as health_router
from app.api.routes.merchants import router as merchants_router
from app.api.routes.vehicles import router as vehicles_router


api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(merchants_router)
api_router.include_router(customers_router)
api_router.include_router(vehicles_router)
api_router.include_router(drivers_router)
