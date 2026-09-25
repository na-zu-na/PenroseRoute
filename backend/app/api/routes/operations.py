from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, model_validator
from app.api.auth import Principal, authenticate_dispatch_user
from app.api.routes.recovery import require_operations_user
from app.core.config import get_settings
from app.modules.dispatch.queries import DispatchQueries
from app.modules.incidents.service import IncidentService
from app.modules.planning.service import PlanningService

router = APIRouter(prefix="/api", tags=["Dispatch operations"])

class GenerateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_date: date

class IncidentCommand(GenerateCommand):
    incident_type: Literal["VEHICLE_UNAVAILABLE", "MERCHANT_DELAY"]
    vehicle_route_id: UUID | None = None
    incident_location_id: UUID | None = None
    merchant_id: UUID | None = None
    updated_ready_at: datetime | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.incident_type == "VEHICLE_UNAVAILABLE":
            if not self.vehicle_route_id or not self.incident_location_id or self.merchant_id or self.updated_ready_at:
                raise ValueError("Vehicle event requires route and confirmed location only")
        elif not self.merchant_id or not self.updated_ready_at or self.vehicle_route_id or self.incident_location_id:
            raise ValueError("Merchant event requires merchant and new ready time only")
        if self.updated_ready_at and self.updated_ready_at.utcoffset() is None:
            raise ValueError("Explicit timezone is required")
        return self


def sessions():
    from app.db.session import SessionLocal
    return SessionLocal

def get_planning_service():
    return PlanningService(sessions(), business_timezone=get_settings().business_timezone)

def get_queries():
    return DispatchQueries(sessions())

def get_incident_service():
    return IncidentService(sessions())

def response(data, code="SUCCESS"):
    return {"success": True, "code": code, "message": "操作完成", "data": data, "request_id": "req_"+uuid4().hex}

@router.get("/operations")
def operations(business_date: date, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.operations(business_date))

@router.get("/resources/availability")
def resources(business_date: date, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.resources(business_date))

@router.get("/recovery-plans/{recovery_id}")
def proposal(recovery_id: UUID, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.proposal(recovery_id))

@router.get("/delivery-plans/compare")
def compare(base_plan_id: UUID, candidate_plan_id: UUID, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.compare(base_plan_id, candidate_plan_id))

@router.post("/planning/generate", status_code=201)
def generate(command: GenerateCommand, principal: Principal = Depends(require_operations_user), service=Depends(get_planning_service)):
    return response(service.generate(command.business_date, principal.subject), "PLAN_DRAFT_CREATED")

@router.post("/delivery-plans/{plan_id}/activate")
def activate(plan_id: UUID, principal: Principal = Depends(require_operations_user), service=Depends(get_planning_service)):
    return response(service.activate(plan_id, principal.subject), "PLAN_ACTIVATED")

@router.post("/delivery-plans/{plan_id}/cancel")
def cancel(plan_id: UUID, principal=Depends(require_operations_user), service=Depends(get_planning_service)):
    return response(service.cancel(plan_id), "PLAN_CANCELLED")

@router.post("/incidents", status_code=201)
def report(command: IncidentCommand, principal: Principal = Depends(require_operations_user), service=Depends(get_incident_service)):
    return response(service.report(command, principal.subject), "INCIDENT_REPORTED")
