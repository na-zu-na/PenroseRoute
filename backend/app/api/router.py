from fastapi import APIRouter
from app.api.routes.dispatch import router as dispatch_router

from app.api.routes.alerts import router as alerts_router
from app.api.routes.assignments import router as assignments_router
from app.api.routes.decisions import router as decisions_router
from app.api.routes.recovery import router as recovery_router
from app.api.routes.recovery_plans import router as recovery_plans_router
from app.api.routes.customers import router as customers_router
from app.api.routes.delivery_plans import router as delivery_plans_router
from app.api.routes.drivers import router as drivers_router
from app.api.routes.health import router as health_router
from app.api.routes.incidents import router as incidents_router
from app.api.routes.merchants import router as merchants_router
from app.api.routes.operations import router as operations_router
from app.api.routes.orders import router as orders_router
from app.api.routes.planning import router as planning_router
from app.api.routes.route_stops import router as route_stops_router
from app.api.routes.vehicle_routes import router as vehicle_routes_router
from app.api.routes.vehicles import router as vehicles_router


api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(dispatch_router)
api_router.include_router(orders_router)
api_router.include_router(assignments_router)
api_router.include_router(recovery_router)
api_router.include_router(recovery_plans_router)
api_router.include_router(decisions_router)
api_router.include_router(incidents_router)
api_router.include_router(merchants_router)
api_router.include_router(customers_router)
api_router.include_router(vehicles_router)
api_router.include_router(drivers_router)
api_router.include_router(operations_router)
api_router.include_router(alerts_router)
api_router.include_router(planning_router)
api_router.include_router(delivery_plans_router)
api_router.include_router(vehicle_routes_router)
api_router.include_router(route_stops_router)
