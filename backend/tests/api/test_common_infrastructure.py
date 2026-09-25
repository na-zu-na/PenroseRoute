import asyncio

import httpx
from fastapi import FastAPI, Query

from app.api.error_handlers import register_error_handlers
from app.core.errors import BusinessError, Conflict, IntegrationError, NotFound


def _request(app: FastAPI, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    return asyncio.run(send())


def _error_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/not-found")
    def not_found() -> None:
        raise NotFound(code="ORDER_NOT_FOUND", message="Order was not found")

    @app.get("/conflict")
    def conflict() -> None:
        raise Conflict(code="CURRENT_PLAN_CONFLICT", message="Current plan changed")

    @app.get("/business")
    def business() -> None:
        raise BusinessError(code="BUSINESS_RULE_VIOLATION", message="Rule rejected")

    @app.get("/integration")
    def integration() -> None:
        raise IntegrationError(code="ROUTING_PROVIDER_ERROR", message="Routing failed")

    @app.get("/validated")
    def validated(page: int = Query(ge=1)) -> dict[str, int]:
        return {"page": page}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("sensitive implementation detail")

    return app


def test_application_errors_are_mapped_only_at_http_boundary() -> None:
    app = _error_test_app()

    cases = [
        ("/not-found", 404, "ORDER_NOT_FOUND"),
        ("/conflict", 409, "CURRENT_PLAN_CONFLICT"),
        ("/business", 422, "BUSINESS_RULE_VIOLATION"),
        ("/integration", 500, "ROUTING_PROVIDER_ERROR"),
    ]

    for path, status_code, code in cases:
        response = _request(app, path)
        body = response.json()
        assert response.status_code == status_code
        assert body["success"] is False
        assert body["code"] == code
        assert body["data"] is None
        assert body["request_id"].startswith("req_")


def test_request_validation_error_uses_common_envelope() -> None:
    response = _request(_error_test_app(), "/validated?page=0")

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "Request validation failed"
    assert body["data"]["field_errors"][0]["path"] == "query.page"
    assert body["request_id"].startswith("req_")


def test_framework_not_found_uses_common_envelope() -> None:
    response = _request(_error_test_app(), "/missing")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "Resource not found"
    assert body["data"] is None
    assert body["request_id"].startswith("req_")


def test_unexpected_error_uses_safe_common_envelope() -> None:
    response = _request(_error_test_app(), "/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "An unexpected server error occurred"
    assert "sensitive implementation detail" not in response.text
    assert body["data"] is None
    assert body["request_id"].startswith("req_")
