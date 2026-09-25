from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.api.dependencies import ensure_request_id
from app.core.errors import AuthenticationError, BusinessError, Conflict, IntegrationError, NotFound
from app.core.responses import error_response


def _json_error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    data: object | None = None,
) -> JSONResponse:
    body = error_response(
        code=code,
        message=message,
        data=data,
        request_id=ensure_request_id(request),
    )
    response = JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )
    response.headers["X-Request-ID"] = body.request_id
    return response


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(request: Request, error: AuthenticationError) -> JSONResponse:
        return _json_error(request, status_code=error.http_status, code=error.code, message=str(error))

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_request_id = request.headers.get("X-Request-ID", "").strip()
        if supplied_request_id:
            request.state.request_id = supplied_request_id
        request_id = ensure_request_id(request)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(NotFound)
    async def not_found_handler(request: Request, error: NotFound) -> JSONResponse:
        return _json_error(
            request,
            status_code=404,
            code=error.code,
            message=error.message,
            data=error.data,
        )

    @app.exception_handler(Conflict)
    async def conflict_handler(request: Request, error: Conflict) -> JSONResponse:
        return _json_error(
            request,
            status_code=409,
            code=error.code,
            message=error.message,
            data=error.data,
        )

    @app.exception_handler(BusinessError)
    async def business_error_handler(
        request: Request,
        error: BusinessError,
    ) -> JSONResponse:
        return _json_error(
            request,
            status_code=422,
            code=error.code,
            message=error.message,
            data=error.data,
        )

    @app.exception_handler(IntegrationError)
    async def integration_error_handler(
        request: Request,
        error: IntegrationError,
    ) -> JSONResponse:
        return _json_error(
            request,
            status_code=500,
            code=error.code,
            message=error.message,
            data=error.data,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        field_errors = [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()
        ]
        return _json_error(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="Request validation failed",
            data={"field_errors": field_errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        if error.status_code == 404:
            code = "NOT_FOUND"
            message = "Resource not found"
        elif error.status_code == 405:
            code = "METHOD_NOT_ALLOWED"
            message = "Method not allowed"
        else:
            code = "HTTP_ERROR"
            message = (
                error.detail
                if isinstance(error.detail, str)
                else "HTTP request failed"
            )
        return _json_error(
            request,
            status_code=error.status_code,
            code=code,
            message=message,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del error
        return _json_error(
            request,
            status_code=500,
            code="INTERNAL_ERROR",
            message="An unexpected server error occurred",
        )
