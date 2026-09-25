from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.models import Incident, IncidentAffectedOrder
from app.db.models.recovery import IncidentStatus, IncidentType


class IncidentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_open_vehicle_incident(
        self, delivery_plan_id: UUID, vehicle_id: UUID
    ) -> Incident | None:
        statement = select(Incident).where(
            Incident.delivery_plan_id == delivery_plan_id,
            Incident.vehicle_id == vehicle_id,
            Incident.incident_type == IncidentType.VEHICLE_UNAVAILABLE,
            Incident.status != IncidentStatus.RESOLVED,
        )
        return self.session.scalar(statement)

    def get_open_merchant_incident(
        self, delivery_plan_id: UUID, merchant_id: UUID
    ) -> Incident | None:
        statement = select(Incident).where(
            Incident.delivery_plan_id == delivery_plan_id,
            Incident.merchant_id == merchant_id,
            Incident.incident_type == IncidentType.MERCHANT_DELAY,
            Incident.status != IncidentStatus.RESOLVED,
        )
        return self.session.scalar(statement)

    def get_incident_affected_orders(
        self, incident_id: UUID
    ) -> list[IncidentAffectedOrder]:
        statement = (
            select(IncidentAffectedOrder)
            .where(IncidentAffectedOrder.incident_id == incident_id)
            .options(
                joinedload(IncidentAffectedOrder.order),
                joinedload(IncidentAffectedOrder.original_vehicle_route),
            )
            .order_by(IncidentAffectedOrder.created_at, IncidentAffectedOrder.id)
        )
        return list(self.session.scalars(statement))

    def get_incident_with_affected_orders(
        self, incident_id: UUID
    ) -> Incident | None:
        statement = (
            select(Incident)
            .where(Incident.id == incident_id)
            .execution_options(populate_existing=True)
            .options(
                joinedload(Incident.incident_location),
                selectinload(Incident.affected_orders).joinedload(
                    IncidentAffectedOrder.order
                ),
            )
        )
        return self.session.scalar(statement)

    def lock_incident_by_id(self, incident_id: UUID) -> Incident | None:
        statement = (
            select(Incident)
            .where(Incident.id == incident_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement)

    def add_incident(self, incident: Incident) -> None:
        self.session.add(incident)

    def add_affected_order(self, affected_order: IncidentAffectedOrder) -> None:
        self.session.add(affected_order)

    def flush(self) -> None:
        self.session.flush()
