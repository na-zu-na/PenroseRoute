import asyncio

import httpx

from app.main import app


def test_health_returns_ok() -> None:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(
                "/api/health",
                headers={"X-Request-ID": "req-test-health"},
            )

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-test-health"
    assert response.json() == {
        "success": True,
        "code": "SUCCESS",
        "message": "Service is healthy",
        "data": {"status": "ok"},
        "request_id": "req-test-health",
    }
