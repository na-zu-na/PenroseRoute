from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
import json
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.api.error_handlers import register_error_handlers
from app.api.router import api_router
from app.api.routes.dispatch import router as dispatch_router
from app.api.routes.operations import agent_router
from app.integrations.agent.contracts import RecoveryError
from app.api.auth import Principal, authenticate_dispatch_user
from app.api.routes.dispatch import get_dispatch_service
from app.api.routes.operations import get_incident_service
from app.api.routes.decisions import get_decision_service
from app.api.routes.recovery import get_agent_recovery_workflow
from app.core.config import Settings
from app.db.models import VehicleRoute, Vehicle, Driver, VehicleDriverAssignment
from app.modules.dispatch.service import DispatchService
from app.modules.dispatch.session import ContextCodec
from app.modules.dispatch.queries import DispatchQueries
from app.modules.planning.service import PlanningService
from app.modules.incidents.service import IncidentService
from app.modules.decisions.service import DecisionService
from app.modules.recovery.application import SqlRecoveryApplication
from app.modules.recovery.orchestration import RecoveryOrchestrator
from app.modules.recovery.workflow import RecoveryWorkflow
from app.modules.recovery.evidence import project_evidence
from app.integrations.optimization.solver import solve, validate


# These legacy Agent/Dispatch endpoints are intentionally absent from the P0 app.
# Mount them only in their isolated compatibility tests.
app = FastAPI()
register_error_handlers(app)
app.include_router(api_router)
app.include_router(dispatch_router, prefix="/api")
app.include_router(agent_router, prefix="/api")


@app.exception_handler(RecoveryError)
async def legacy_recovery_error(request: Request, error: RecoveryError):
    return JSONResponse(status_code=error.http_status, content={
        "success": False, "code": error.code, "message": str(error),
        "data": None, "request_id": getattr(request.state, "request_id", "legacy-test"),
    })

@pytest.fixture(autouse=True)
def restore_overrides():
    original = app.dependency_overrides.copy()
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def test_real_http_end_to_end_dispatch_recovery_approval(database):
    sessions, now, day, ids = database
    planning = PlanningService(sessions, clock=lambda: now)
    recovery = RecoveryWorkflow(SqlRecoveryApplication(sessions, clock=lambda: now+timedelta(seconds=60)), RecoveryOrchestrator(solve, validate, evidence_projector=project_evidence))
    service = DispatchService(DispatchQueries(sessions, clock=lambda: now), codec=ContextCodec("x"*32),
        recovery=recovery, clock=lambda: now)
    app.dependency_overrides.update({authenticate_dispatch_user: lambda: Principal("alice", "dispatcher"),
        get_dispatch_service: lambda: service,
        get_incident_service: lambda: IncidentService(sessions, clock=lambda: now),
        get_decision_service: lambda: DecisionService(sessions, clock=lambda: now+timedelta(seconds=90)),
        get_agent_recovery_workflow: lambda: recovery})
    client = TestClient(app)
    generated = planning.generate(day, "alice")
    plan_id = generated["plan_id"]
    planning.activate(UUID(plan_id), "alice")
    with sessions() as s, s.begin():
        route = s.scalar(select(VehicleRoute).where(VehicleRoute.delivery_plan_id == UUID(plan_id)))
        route_id = route.id
        vehicle_id, driver_id, assignment_id = uuid4(), uuid4(), uuid4()
        s.add(Vehicle(id=vehicle_id, vehicle_code="BACKUP", name="Backup", status="AVAILABLE", capacity_load_units=5,
            current_location_id=ids["pickup"], current_location_recorded_at=now))
        s.add(Driver(id=driver_id, driver_code="BACKUP", name="Backup", status="AVAILABLE"))
        s.add(VehicleDriverAssignment(id=assignment_id, vehicle_id=vehicle_id, driver_id=driver_id,
            assigned_from_at=now, assigned_until_at=now+timedelta(hours=12), status="ACTIVE", activated_at=now))
    response = client.post("/api/incidents", json={"business_date": str(day), "incident_type": "VEHICLE_UNAVAILABLE",
        "vehicle_route_id": str(route_id), "incident_location_id": str(ids["pickup"])})
    assert response.status_code == 201
    incident_id = response.json()["data"]["incident_id"]
    response = client.post("/api/agent/dispatch", json={"message": "查看今天风险和车辆，生成恢复方案并比较差异",
        "context": {"incident_id": incident_id}})
    result = response.json()
    assert result["code"] == "DISPATCH_COMPLETED", result
    assert len(result["data"]["observations"]) == 5
    recovery_data = result["data"]["observations"][2]["data"]
    assert recovery_data["explanation"]["impact_explanation"]
    assert recovery_data["manual_intervention_required"] is True
    assert recovery_data["recovery_evidence"]["reassigned_orders"][0]["to_vehicle_id"] == str(vehicle_id)
    proposal = result["data"]["observations"][3]["data"]
    assert proposal["structured_explanation"] == recovery_data["explanation"]
    for field, value in recovery_data["recovery_evidence"]["after"].items():
        assert proposal["candidate_plan"][field] == value
    recovery_id = result["data"]["context"]["recovery_plan_id"]
    response = client.post(f"/api/recovery-plans/{recovery_id}/approve", json={"decision_reason": "调度员确认接手"})
    assert response.status_code == 200 and response.json()["data"]["candidate_status"] == "CURRENT"


def test_real_api_auth_and_reader_permissions(monkeypatch):
    import app.api.auth as auth
    key = "read-only-token-" + "a"*32
    settings = Settings(_env_file=None, dispatch_api_tokens=json.dumps({key: {"subject": "reader", "role": "reader"}}))
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    app.dependency_overrides[get_dispatch_service] = lambda: DispatchService(None, codec=ContextCodec("x"*32))
    client = TestClient(app)
    assert client.post("/api/agent/dispatch", json={"message": "今天状态"}).status_code == 401
    response = client.post("/api/agent/dispatch", headers={"Authorization": "Bearer "+key}, json={"message": "生成恢复方案"})
    assert response.status_code == 403 and response.json()["code"] == "DISPATCH_FORBIDDEN"
    response = client.post(f"/api/recovery-plans/{uuid4()}/approve", headers={"Authorization": "Bearer "+key}, json={"decision_reason": "test"})
    assert response.status_code == 403
