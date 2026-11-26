"""Реестр событий изменений (D-181): запись и чтение. Только добавление; значений полей в событиях нет."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import ChangeEvent

# Запрет изменения и удаления — тот же текст, что в migrations/053 (dev и стенд создают схему через init_db)
GUARD_SQL = """
CREATE OR REPLACE FUNCTION change_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'change_events: только добавление (изменение и удаление запрещены)';
END $$;
DROP TRIGGER IF EXISTS change_events_no_update ON change_events;
CREATE TRIGGER change_events_no_update BEFORE UPDATE OR DELETE ON change_events
    FOR EACH ROW EXECUTE FUNCTION change_events_append_only();
DROP TRIGGER IF EXISTS change_events_no_truncate ON change_events;
CREATE TRIGGER change_events_no_truncate BEFORE TRUNCATE ON change_events
    FOR EACH STATEMENT EXECUTE FUNCTION change_events_append_only();
"""


def ensure_guard(engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        # init_db может выполняться одновременно в нескольких процессах: замок и проверка «уже есть», иначе
        # параллельный CREATE OR REPLACE FUNCTION падает с «tuple concurrently updated»
        conn.exec_driver_sql("SELECT pg_advisory_xact_lock(181181)")
        done = conn.exec_driver_sql("SELECT count(*) FROM pg_trigger WHERE tgname IN ('change_events_no_update', 'change_events_no_truncate')").scalar()
        if done == 2:
            return
        conn.exec_driver_sql(GUARD_SQL)


def record(db: Session, entity: str, entity_id: str, action: str, actor_id: Optional[str] = None,
           deal_id: Optional[str] = None) -> None:
    """Добавить событие в текущую транзакцию: откат транзакции откатывает и событие."""
    db.add(ChangeEvent(entity=entity[:64], entity_id=str(entity_id)[:128], action=action[:32], actor_id=actor_id, deal_id=deal_id))


def settled(db: Session, after: int = 0, limit: int = 500, entity: str = "") -> tuple[list[ChangeEvent], int]:
    """События с seq > after только из завершённых транзакций (xid ниже горизонта всех незавершённых).

    Возвращает события и курсор для следующего запроса. Курсор не перескакивает через незавершённые транзакции:
    если транзакция с меньшим seq ещё идёт, чтение останавливается перед ней."""
    horizon = db.execute(text("SELECT (pg_snapshot_xmin(pg_current_snapshot())::text)::bigint")).scalar_one()
    rows = db.scalars(select(ChangeEvent).where(ChangeEvent.seq > after).order_by(ChangeEvent.seq).limit(max(1, min(limit, 2000)) + 1)).all()
    out: list[ChangeEvent] = []
    cursor = after
    for ev in rows[:limit]:
        if ev.xid >= horizon:     # транзакция ещё могла не завершиться — дальше не читаем
            break
        cursor = ev.seq
        if not entity or ev.entity == entity:
            out.append(ev)
    return out, cursor
