from collections.abc import Generator
from uuid import uuid4

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def ensure_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = f"req_{uuid4().hex}"
        request.state.request_id = request_id
    return request_id


def get_request_id(request: Request) -> str:
    return ensure_request_id(request)
