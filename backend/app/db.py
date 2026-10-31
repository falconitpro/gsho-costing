from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(_settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Создание схемы (для dev). В проде — alembic upgrade head."""
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
