from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes.recovery import require_operations_user, get_recovery_workflow
from app.core.config import Settings
from app.modules.recovery.bootstrap import create_recovery_workflow
from tests.agent.fixture_backend import FixtureApplication
import pytest


@pytest.fixture(autouse=True)
def restore_dependencies():
    original = app.dependency_overrides.copy()
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def test_real_router_requires_authentication_configuration():
    response = TestClient(app).post(f"/api/incidents/{uuid4()}/recovery", json={})
    assert response.status_code == 503
    assert response.json()["code"] == "AUTH_NOT_CONFIGURED"


def test_deterministic_recovery_requires_authentication_configuration():
    response = TestClient(app).post(
        f"/api/incidents/{uuid4()}/deterministic-recovery", json={}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "AUTH_NOT_CONFIGURED"


def test_real_router_has_default_backend_configuration():
    from types import SimpleNamespace
    from app.modules.recovery.application import SqlRecoveryApplication
    workflow = get_recovery_workflow(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    assert isinstance(workflow.application, SqlRecoveryApplication)


def test_real_router_runs_injected_workflow():
    fixture = FixtureApplication(("INFEASIBLE", "VALID"))
    workflow = create_recovery_workflow(fixture, fixture.solver, fixture.validator,
                                        Settings(_env_file=None, agent_explanation_provider="template"))
    app.dependency_overrides[require_operations_user] = lambda: "dispatcher"
    app.dependency_overrides[get_recovery_workflow] = lambda: workflow
    response = TestClient(app).post(f"/api/incidents/{fixture.incident_id}/recovery", json={})
    assert response.status_code == 201
    assert response.json()["code"] == "RECOVERY_PENDING_REVIEW"
    assert len(response.json()["data"]["attempts_created"]) == 2
    assert fixture.current


def test_bedrock_requires_model_configuration():
    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        create_recovery_workflow(None, None, None, Settings(_env_file=None,
            agent_explanation_provider="bedrock", bedrock_model_id=None))


def test_real_router_rejects_scope_override_before_execution():
    fixture = FixtureApplication(("VALID",))
    workflow = create_recovery_workflow(fixture, fixture.solver, fixture.validator,
        Settings(_env_file=None, agent_explanation_provider="template"))
    app.dependency_overrides[require_operations_user] = lambda: "dispatcher"
    app.dependency_overrides[get_recovery_workflow] = lambda: workflow
    response = TestClient(app).post(f"/api/incidents/{fixture.incident_id}/recovery",
                                   json={"scope": "ALL_REMAINING"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert not fixture.calls
