from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .core.config import get_settings

log = logging.getLogger("gsho.db")


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
    """Создание схемы (dev и тестовый стенд): новые таблицы — create_all, новые столбцы существующих таблиц —
    ALTER TABLE … ADD COLUMN (без удаления и изменения типов). В проде — alembic upgrade head."""
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    add_missing_columns()


def add_missing_columns() -> list[str]:
    """Добавляет в БД столбцы, которые есть в моделях, но отсутствуют в таблицах (миграция вперёд без alembic)."""
    insp = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {col.type.compile(dialect=engine.dialect)}'
                default = _sql_default(col)
                if default is not None:
                    ddl += f" DEFAULT {default}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.info("db: добавлены столбцы %s", ", ".join(added))
    return added


def _sql_default(col) -> str | None:
    """Значение по умолчанию для уже существующих строк (скалярные Python-значения из модели)."""
    d = col.default
    if d is None:
        return None
    if getattr(d, "is_callable", False):
        try:   # default=list / default=dict для JSON-полей → '[]' / '{}' для существующих строк
            import json
            produced = d.arg(None)
            if isinstance(produced, (list, dict)):
                return "'" + json.dumps(produced, ensure_ascii=False).replace("'", "''") + "'"
        except Exception:
            return None
        return None
    if not getattr(d, "is_scalar", False):
        return None
    v = d.arg
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return None
