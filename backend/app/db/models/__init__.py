"""P0 SQLAlchemy ORM models.

Importing this package registers all 14 existing database tables on ``Base``.
"""

from app.db.models.fleet import Driver, Vehicle, VehicleDriverAssignment
from app.db.models.planning import DeliveryPlan, DeliveryPlanOrder, RouteStop, VehicleRoute
from app.db.models.recovery import Incident, IncidentAffectedOrder, RecoveryPlan
from app.db.models.resources import Customer, Location, Merchant, Order

__all__ = [
    "Customer",
    "DeliveryPlan",
    "DeliveryPlanOrder",
    "Driver",
    "Incident",
    "IncidentAffectedOrder",
    "Location",
    "Merchant",
    "Order",
    "RecoveryPlan",
    "RouteStop",
    "Vehicle",
    "VehicleDriverAssignment",
    "VehicleRoute",
]
