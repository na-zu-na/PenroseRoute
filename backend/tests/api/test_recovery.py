"""P0 Recovery API remains deterministic and independent of Agent imports."""
import subprocess
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from app.api.auth import Principal, authenticate_dispatch_user
from app.api.routes.recovery import require_operations_user
from app.main import app


def test_recovery_requires_authentication_configuration():
    response = TestClient(app).post(f"/api/incidents/{uuid4()}/recovery", json={})
    assert response.status_code == 503
    assert response.json()["code"] == "AUTH_NOT_CONFIGURED"


def test_temporary_recovery_route_is_not_public():
    response = TestClient(app).post(f"/api/incidents/{uuid4()}/deterministic-recovery", json={})
    assert response.status_code == 404
    assert "/api/incidents/{incident_id}/deterministic-recovery" not in app.openapi()["paths"]


def test_recovery_request_rejects_scope_override():
    app.dependency_overrides[require_operations_user] = lambda: Principal("dispatcher", "dispatcher")
    try:
        response = TestClient(app).post(
            f"/api/incidents/{uuid4()}/recovery",
            json={"scope": "ALL_REMAINING"},
        )
    finally:
        app.dependency_overrides.pop(require_operations_user, None)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_recovery_forbids_reader():
    app.dependency_overrides[authenticate_dispatch_user] = lambda: Principal("reader", "reader")
    try:
        response = TestClient(app).post(f"/api/incidents/{uuid4()}/recovery", json={})
    finally:
        app.dependency_overrides.pop(authenticate_dispatch_user, None)
    assert response.status_code == 403
    assert response.json()["code"] == "DISPATCH_FORBIDDEN"


def test_p0_app_import_does_not_require_agent_modules():
    code = """
import importlib.abc
import sys

blocked = (
    "app.integrations.agent", "app.integrations.dispatch_agent",
    "app.modules.dispatch", "app.modules.recovery.bootstrap",
    "app.modules.recovery.workflow", "app.modules.recovery.orchestration",
    "app.modules.planning.service", "app.modules.incidents.service",
    "langgraph", "boto3",
)
class BlockAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise RuntimeError("P0 startup loaded Agent module: " + fullname)
        return None

sys.meta_path.insert(0, BlockAgent())
from app.main import app
assert "/api/incidents/{incident_id}/recovery" in app.openapi()["paths"]
assert "/api/incidents/{incident_id}/deterministic-recovery" not in app.openapi()["paths"]
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
