from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.router import api_router


app = FastAPI(title="PenroseRoute API")
register_error_handlers(app)
app.include_router(api_router)
