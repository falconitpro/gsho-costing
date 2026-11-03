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
    sync_enums()
    Base.metadata.create_all(engine)
    add_missing_columns()
    vat_22_once()


def vat_22_once() -> None:
    """С 2026 года НДС в РФ — 22 %: однократно переводит действующие ставки справочника ТН ВЭД с 20 % на 22 %."""
    from sqlalchemy.orm import Session
    from .models import HsCode, RateSetting
    with Session(engine) as db:
        if db.query(RateSetting).filter(RateSetting.key == "_migration_vat22").first():
            return
        db.query(HsCode).filter(HsCode.valid_to.is_(None), HsCode.vat_pct == 20).update({HsCode.vat_pct: 22}, synchronize_session=False)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)   # закрытая запись — служебная отметка, в настройки не попадает
        db.add(RateSetting(key="_migration_vat22", value=True, comment="НДС 20 → 22 % в справочнике ТН ВЭД", valid_from=now, valid_to=now))
        db.commit()


def sync_enums() -> None:
    """Новые значения перечислений (deal_status: contract, execution, closed) — ALTER TYPE … ADD VALUE (PostgreSQL)."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import Enum as SAEnum
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for table in Base.metadata.sorted_tables:
            for col in table.columns:
                if isinstance(col.type, SAEnum) and col.type.name:
                    exists = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": col.type.name}).first()
                    if not exists:
                        continue
                    labels = {r[0] for r in conn.execute(text("SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :n"), {"n": col.type.name})}
                    for v in col.type.enums:
                        if v not in labels:
                            conn.execute(text(f"ALTER TYPE {col.type.name} ADD VALUE IF NOT EXISTS '{v}'"))


def add_missing_columns() -> list[str]:
    """Добавляет в БД столбцы, которые есть в моделях, но отсутствуют в таблицах (миграция вперёд без alembic)."""
    insp = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            cols = {c["name"]: c for c in insp.get_columns(table.name)}
            existing = set(cols)
            for col in table.columns:
                if col.name in existing:
                    # расширение varchar (например, tasks.block 32 → 64): PostgreSQL меняет тип без перезаписи данных
                    want, have = getattr(col.type, "length", None), getattr(cols[col.name]["type"], "length", None)
                    if want and have and have < want and engine.dialect.name == "postgresql":
                        conn.execute(text(f'ALTER TABLE {table.name} ALTER COLUMN "{col.name}" TYPE VARCHAR({want})'))
                        added.append(f"{table.name}.{col.name}({have}→{want})")
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
