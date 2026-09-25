"""Deterministic P0 incident transaction workflows."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import BusinessError, Conflict
from app.db.models import Incident, IncidentAffectedOrder, Location, Merchant, Vehicle
from app.db.models.fleet import ResourceStatus
from app.db.models.planning import StopType
from app.db.models.recovery import IncidentStatus, IncidentType
from app.db.models.resources import MerchantPreparationStatus, OrderExecutionStatus
from app.db.repositories.incident_repository import IncidentRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.modules.incidents.context import (
    MerchantIncidentContext,
    resolve_merchant_incident_context,
    resolve_vehicle_incident_context,
)
from app.modules.incidents.impact import assess_merchant_order, assess_vehicle_order
from app.modules.incidents.scope import (
    initial_merchant_delay_scope,
    initial_vehicle_unavailable_scope,
)
from app.modules.operations.risk import RiskService
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


@dataclass(frozen=True)
class MerchantDelayResult:
    merchant: Merchant
    incident_id: UUID | None
    incident_code: str | None
    incident_type: str | None
    business_date: date
    status: str | None
    original_ready_at: datetime
    updated_ready_at: datetime
    delay_seconds: int
    requires_replanning: bool
    affected_order_count: int
    replanning_scope: str | None


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


class MerchantDelayWorkflow:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.incidents = IncidentRepository(session)
        self.risk = RiskService(
            threshold_seconds=get_settings().at_risk_threshold_seconds
        )

    def assess_delay(
        self,
        *,
        business_date: date,
        merchant_id: UUID,
        updated_ready_at: datetime,
        detected_at: datetime | None = None,
        explicit_incident: bool,
    ) -> MerchantDelayResult:
        event_time = detected_at or datetime.now(timezone.utc)
        if updated_ready_at.tzinfo is None or updated_ready_at.utcoffset() is None:
            raise BusinessError(
                code="VALIDATION_ERROR",
                message="updated_ready_at must include a timezone offset",
            )
        with self.session.begin():
            context = resolve_merchant_incident_context(
                self.session,
                business_date=business_date,
                merchant_id=merchant_id,
            )
            delay_seconds = int(
                (updated_ready_at - context.original_ready_at).total_seconds()
            )
            if delay_seconds <= 0:
                if explicit_incident:
                    raise BusinessError(
                        code="MERCHANT_DELAY_NOT_POSITIVE",
                        message="Updated ready time must be later than the plan snapshot",
                    )
                context.merchant.operational_ready_at = updated_ready_at
                context.merchant.updated_at = datetime.now(timezone.utc)
                self.session.flush()
                return MerchantDelayResult(
                    merchant=context.merchant,
                    incident_id=None,
                    incident_code=None,
                    incident_type=None,
                    business_date=business_date,
                    status=None,
                    original_ready_at=context.original_ready_at,
                    updated_ready_at=updated_ready_at,
                    delay_seconds=delay_seconds,
                    requires_replanning=False,
                    affected_order_count=0,
                    replanning_scope=None,
                )
            if self.incidents.get_open_merchant_incident(
                context.plan.id, merchant_id
            ) is not None:
                raise Conflict(
                    code="INCIDENT_ALREADY_EXISTS",
                    message="An open merchant delay incident already exists for this plan",
                )

            provisional_replanning = delay_seconds > 600
            impacts = [
                impact
                for item in context.orders
                if (
                    impact := assess_merchant_order(
                        order_id=item.order.id,
                        route_id=item.route_id,
                        execution_status=item.order.execution_status,
                        risk_status=item.order.risk_status,
                        is_direct=item.is_direct,
                        requires_replanning=provisional_replanning,
                    )
                )
                is not None
            ]
            requires_replanning = provisional_replanning and bool(impacts)
            incident_status = (
                IncidentStatus.REPLANNING
                if requires_replanning
                else IncidentStatus.RESOLVED
            )
            context.merchant.operational_ready_at = updated_ready_at
            context.merchant.preparation_status = MerchantPreparationStatus.DELAYED
            context.merchant.updated_at = datetime.now(timezone.utc)
            incident = Incident(
                incident_code=f"INC-{business_date:%Y%m%d}-{uuid4().hex[:12].upper()}",
                incident_type=IncidentType.MERCHANT_DELAY,
                status=incident_status,
                delivery_plan_id=context.plan.id,
                merchant_id=context.merchant.id,
                original_ready_at=context.original_ready_at,
                updated_ready_at=updated_ready_at,
                delay_seconds=delay_seconds,
                detected_at=event_time,
                resolved_at=event_time if incident_status is IncidentStatus.RESOLVED else None,
                detected_by="operations_user",
            )
            self.incidents.add_incident(incident)
            self.incidents.flush()
            for impact in impacts:
                self.incidents.add_affected_order(
                    IncidentAffectedOrder(
                        incident_id=incident.id,
                        order_id=impact.order_id,
                        original_vehicle_route_id=impact.route_id,
                        execution_status_snapshot=impact.execution_status_snapshot,
                        risk_status_snapshot=impact.risk_status_snapshot,
                        was_picked_up=False,
                        was_completed=False,
                        requires_replanning=requires_replanning,
                        handover_required=False,
                        impact_type=impact.impact_type,
                        impact_reason=impact.impact_reason,
                        assessed_at=event_time,
                    )
                )
            if not requires_replanning:
                self._update_remaining_eta_and_risk(context, updated_ready_at)
            self.incidents.flush()
            scope = initial_merchant_delay_scope() if requires_replanning else None
            return MerchantDelayResult(
                merchant=context.merchant,
                incident_id=incident.id,
                incident_code=incident.incident_code,
                incident_type=incident.incident_type,
                business_date=business_date,
                status=incident.status,
                original_ready_at=context.original_ready_at,
                updated_ready_at=updated_ready_at,
                delay_seconds=delay_seconds,
                requires_replanning=requires_replanning,
                affected_order_count=len(impacts),
                replanning_scope=scope,
            )

    def _update_remaining_eta_and_risk(
        self, context: MerchantIncidentContext, updated_ready_at: datetime
    ) -> None:
        direct_order_ids = {
            item.order.id for item in context.orders if item.is_direct
        }
        route_delay_seconds: dict[UUID, int] = {}
        for stop in sorted(
            context.remaining_stops,
            key=lambda item: (item.vehicle_route_id, item.sequence_no),
        ):
            delay = route_delay_seconds.get(stop.vehicle_route_id, 0)
            original_departure = stop.planned_departure_at
            stop.planned_arrival_at += timedelta(seconds=delay)
            stop.planned_departure_at = max(
                original_departure,
                stop.planned_arrival_at + timedelta(seconds=stop.service_seconds),
            )
            if stop.stop_type is StopType.PICKUP and stop.order_id in direct_order_ids:
                stop.planned_departure_at = max(
                    stop.planned_departure_at,
                    updated_ready_at + timedelta(seconds=stop.service_seconds),
                )
            route_delay_seconds[stop.vehicle_route_id] = int(
                (stop.planned_departure_at - original_departure).total_seconds()
            )
            if (
                stop.stop_type is StopType.DELIVERY
                and stop.order.execution_status is not OrderExecutionStatus.COMPLETED
            ):
                stop.order.risk_status = self.risk.status_for_eta(
                    estimated_arrival_at=stop.planned_arrival_at,
                    delivery_window_end_at=(
                        stop.time_window_end_at or stop.order.delivery_window_end_at
                    ),
                )
