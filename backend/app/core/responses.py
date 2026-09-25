from typing import Any

from app.schemas.common import ApiResponse, DataT


def success_response(
    *,
    data: DataT | None,
    request_id: str,
    message: str = "Operation completed successfully",
    code: str = "SUCCESS",
) -> ApiResponse[DataT]:
    return ApiResponse(
        success=True,
        code=code,
        message=message,
        data=data,
        request_id=request_id,
    )


def error_response(
    *,
    code: str,
    message: str,
    request_id: str,
    data: Any | None = None,
) -> ApiResponse[Any]:
    return ApiResponse(
        success=False,
        code=code,
        message=message,
        data=data,
        request_id=request_id,
    )
