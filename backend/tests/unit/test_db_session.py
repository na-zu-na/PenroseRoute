from sqlalchemy.orm import DeclarativeBase, Session

from app.db.base import Base
from app.db.session import SessionLocal


def test_database_foundation_uses_sync_sqlalchemy() -> None:
    assert issubclass(Base, DeclarativeBase)
    assert issubclass(SessionLocal.class_, Session)


def test_session_factory_is_bound_to_the_single_project_engine() -> None:
    from app.db.session import engine

    assert SessionLocal.kw["bind"] is engine
