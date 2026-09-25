"""Read projections for Incidents and detection-time impact facts."""
from collections import Counter
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.recovery import IncidentStatus, IncidentType
from app.db.repositories.incident_repository import IncidentRepository


class IncidentQueryService:
    def __init__(self, session: Session) -> None:
        self.repository = IncidentRepository(session)

    def list_incidents(
        self, *, page: int, page_size: int, business_date: date | None,
        incident_type: str | None, status: str | None,
    ) -> tuple[list[dict], int]:
        filters = dict(
            business_date=business_date,
            incident_type=IncidentType(incident_type) if incident_type else None,
            status=IncidentStatus(status) if status else None,
        )
        incidents = self.repository.list_incidents(page=page, page_size=page_size, **filters)
        return [self._base_view(incident) for incident in incidents], self.repository.count_incidents(**filters)

    def get_detail(self, incident_id: UUID) -> dict:
        incident = self.repository.get_incident_for_query(incident_id)
        if incident is None:
            raise NotFound(code="INCIDENT_NOT_FOUND", message="Incident was not found")
        affected = incident.affected_orders
        impacts = Counter(row.impact_type.value for row in affected)
        return {
            **self._base_view(incident),
            "base_plan_code": incident.delivery_plan.plan_code,
            "incident_location_id": incident.incident_location_id,
            "original_ready_at": incident.original_ready_at,
            "updated_ready_at": incident.updated_ready_at,
            "delay_seconds": incident.delay_seconds,
            "resolved_at": incident.resolved_at,
            "detected_by": incident.detected_by,
            "details": incident.details,
            "requires_replanning": any(row.requires_replanning for row in affected),
            "affected_order_count": len(affected),
            "handover_order_count": sum(row.handover_required for row in affected),
            "impact_summary": dict(impacts),
            "recovery_attempts": [
                {
                    "recovery_plan_id": row.id,
                    "attempt_no": row.attempt_no,
                    "replanning_scope": row.replanning_scope.value,
                    "status": row.status.value,
                    "solver_status": row.solver_status.value if row.solver_status else None,
                    "validation_status": row.validation_status.value if row.validation_status else None,
                    "candidate_delivery_plan_id": row.candidate_delivery_plan_id,
                }
                for row in sorted(incident.recovery_plans, key=lambda item: item.attempt_no)
            ],
        }

    def list_affected_orders(self, incident_id: UUID) -> list[dict]:
        if self.repository.get_incident_for_query(incident_id) is None:
            raise NotFound(code="INCIDENT_NOT_FOUND", message="Incident was not found")
        return [
            {
                "id": row.id,
                "incident_id": row.incident_id,
                "order_id": row.order_id,
                "original_vehicle_route_id": row.original_vehicle_route_id,
                "execution_status_snapshot": row.execution_status_snapshot.value,
                "risk_status_snapshot": row.risk_status_snapshot.value,
                "was_picked_up": row.was_picked_up,
                "was_completed": row.was_completed,
                "requires_replanning": row.requires_replanning,
                "handover_required": row.handover_required,
                "impact_type": row.impact_type.value,
                "impact_reason": row.impact_reason,
                "assessed_at": row.assessed_at,
                "created_at": row.created_at,
            }
            for row in self.repository.list_affected_order_snapshots(incident_id)
        ]

    @staticmethod
    def _base_view(incident) -> dict:
        return {
            "id": incident.id,
            "incident_code": incident.incident_code,
            "incident_type": incident.incident_type.value,
            "status": incident.status.value,
            "business_date": incident.delivery_plan.business_date,
            "base_delivery_plan_id": incident.delivery_plan_id,
            "vehicle_route_id": incident.vehicle_route_id,
            "vehicle_id": incident.vehicle_id,
            "merchant_id": incident.merchant_id,
            "detected_at": incident.detected_at,
        }
