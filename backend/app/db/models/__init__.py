"""P0 and P1 SQLAlchemy ORM models.

Importing this package registers all mapped database tables on ``Base``.
"""

from app.db.models.alerts import RiskAlert, RiskAlertChange
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
    "RiskAlert",
    "RiskAlertChange",
    "RouteStop",
    "Vehicle",
    "VehicleDriverAssignment",
    "VehicleRoute",
]
