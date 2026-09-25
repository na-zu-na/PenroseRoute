from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.error_handlers import register_error_handlers
from app.api.router import api_router


app = FastAPI(title="PenroseRoute API")
register_error_handlers(app)
app.include_router(api_router)


def error_response(request: Request, status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={
        "success": False, "code": code, "message": message, "data": None,
        "request_id": getattr(request.state, "request_id", None) or "req_" + uuid4().hex,
    })


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Do not echo arbitrary client input in the error response.
    return error_response(request, 422, "VALIDATION_ERROR", "请求参数不符合接口约束")


from sqlalchemy.exc import OperationalError

@app.exception_handler(OperationalError)
async def database_unavailable_handler(request: Request, exc: OperationalError):
    return error_response(request, 503, "DATABASE_UNAVAILABLE", "数据库暂不可用，请检查服务配置")
