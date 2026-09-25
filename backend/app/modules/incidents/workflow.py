"""Vehicle-unavailable incident transaction workflow."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.errors import BusinessError, Conflict
from app.db.models import Incident, IncidentAffectedOrder, Location, Vehicle
from app.db.models.fleet import ResourceStatus
from app.db.models.recovery import IncidentStatus, IncidentType
from app.db.repositories.incident_repository import IncidentRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.modules.incidents.context import resolve_vehicle_incident_context
from app.modules.incidents.impact import assess_vehicle_order
from app.modules.incidents.scope import initial_vehicle_unavailable_scope
from app.modules.resources.locations import resolve_location


@dataclass(frozen=True)
class VehicleUnavailableResult:
    incident_id: UUID
    incident_code: str
    incident_type: str
    business_date: date
    status: str
    delivery_plan_id: UUID
    affected_vehicle_route_id: UUID
    breakdown_location: Location
    affected_order_count: int
    handover_order_count: int
    replanning_scope: str
    recovery_required: bool
    vehicle: Vehicle


class VehicleIncidentWorkflow:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.incidents = IncidentRepository(session)
        self.plans = PlanRepository(session)
        self.resources = ResourceRepository(session)

    def report_unavailable(
        self,
        *,
        business_date: date,
        vehicle_id: UUID,
        detected_at: datetime | None = None,
        location_code: str | None = None,
        address_text: str | None = None,
        latitude: Decimal | None = None,
        longitude: Decimal | None = None,
    ) -> VehicleUnavailableResult:
        event_time = detected_at or datetime.now(timezone.utc)
        with self.session.begin():
            context = resolve_vehicle_incident_context(
                self.session,
                business_date=business_date,
                vehicle_id=vehicle_id,
            )
            if self.incidents.get_open_vehicle_incident(
                context.plan.id, vehicle_id
            ) is not None:
                raise Conflict(
                    code="INCIDENT_ALREADY_EXISTS",
                    message="An open vehicle incident already exists for this plan",
                )
            if context.vehicle.status is not ResourceStatus.ACTIVE:
                raise Conflict(
                    code="VEHICLE_NOT_EXECUTING_ROUTE",
                    message="Vehicle must be ACTIVE before it can become unavailable",
                )
            if context.vehicle.current_location_recorded_at > event_time:
                raise Conflict(
                    code="INCIDENT_FACT_CONFLICT",
                    message="Incident time precedes the latest vehicle location fact",
                )
            breakdown_location = self._resolve_breakdown_location(
                vehicle=context.vehicle,
                event_time=event_time,
                location_code=location_code,
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
            self._validate_event_time(context.route.id, event_time)
            impacts = [
                assess_vehicle_order(
                    order_id=order.id,
                    execution_status=order.execution_status,
                    risk_status=order.risk_status,
                )
                for order in context.orders
            ]
            if not any(item.requires_replanning for item in impacts):
                raise Conflict(
                    code="INCIDENT_FACT_CONFLICT",
                    message="Active route has no incomplete order to recover",
                )

            incident = Incident(
                incident_code=(
                    f"INC-{business_date:%Y%m%d}-{uuid4().hex[:12].upper()}"
                ),
                incident_type=IncidentType.VEHICLE_UNAVAILABLE,
                status=IncidentStatus.REPLANNING,
                delivery_plan_id=context.plan.id,
                vehicle_route_id=context.route.id,
                vehicle_id=context.vehicle.id,
                incident_location_id=breakdown_location.id,
                detected_at=event_time,
                detected_by="operations_user",
            )
            self.incidents.add_incident(incident)
            self.incidents.flush()
            for impact in impacts:
                self.incidents.add_affected_order(
                    IncidentAffectedOrder(
                        incident_id=incident.id,
                        order_id=impact.order_id,
                        original_vehicle_route_id=context.route.id,
                        execution_status_snapshot=(impact.execution_status_snapshot),
                        risk_status_snapshot=impact.risk_status_snapshot,
                        was_picked_up=impact.was_picked_up,
                        was_completed=impact.was_completed,
                        requires_replanning=impact.requires_replanning,
                        handover_required=impact.handover_required,
                        impact_type=impact.impact_type,
                        impact_reason=impact.impact_reason,
                        assessed_at=event_time,
                    )
                )
            context.vehicle.status = ResourceStatus.UNAVAILABLE
            context.vehicle.current_location = breakdown_location
            context.vehicle.current_location_recorded_at = event_time
            context.vehicle.updated_at = datetime.now(timezone.utc)
            self.incidents.flush()

            scope = initial_vehicle_unavailable_scope()
            return VehicleUnavailableResult(
                incident_id=incident.id,
                incident_code=incident.incident_code,
                incident_type=incident.incident_type,
                business_date=business_date,
                status=incident.status,
                delivery_plan_id=context.plan.id,
                affected_vehicle_route_id=context.route.id,
                breakdown_location=breakdown_location,
                affected_order_count=len(impacts),
                handover_order_count=sum(
                    impact.handover_required for impact in impacts
                ),
                replanning_scope=scope,
                recovery_required=True,
                vehicle=context.vehicle,
            )

    def _resolve_breakdown_location(
        self,
        *,
        vehicle: Vehicle,
        event_time: datetime,
        location_code: str | None,
        address_text: str | None,
        latitude: Decimal | None,
        longitude: Decimal | None,
    ) -> Location:
        if location_code is not None:
            if latitude is None or longitude is None:
                raise BusinessError(
                    code="VEHICLE_LOCATION_REQUIRED",
                    message="Breakdown location coordinates are required",
                )
            return resolve_location(
                self.resources,
                location_code=location_code,
                display_name=f"{vehicle.name} Breakdown Point",
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
        if (
            vehicle.current_location is None
            or vehicle.current_location_recorded_at > event_time
        ):
            raise BusinessError(
                code="VEHICLE_LOCATION_REQUIRED",
                message="A reliable vehicle location is required",
            )
        return vehicle.current_location

    def _validate_event_time(self, route_id: UUID, event_time: datetime) -> None:
        stops = self.plans.list_route_stops(route_id)
        confirmed_times = [
            value
            for stop in stops
            for value in (stop.actual_arrival_at, stop.actual_departure_at)
            if value is not None
        ]
        if confirmed_times and event_time < max(confirmed_times):
            raise Conflict(
                code="INCIDENT_FACT_CONFLICT",
                message="Incident time precedes confirmed route execution facts",
            )
