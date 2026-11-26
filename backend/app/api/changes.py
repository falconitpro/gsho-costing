"""Реестр событий изменений (D-181): чтение по курсору. Пока — ЗГД и администратор (сверка и разбор);
приложение инженера (I-4) будет читать его с отбором по своим нарядам."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, require_roles
from ..core.roles import Role
from ..db import get_db
from ..services.change_events import settled

router = APIRouter(prefix="/api/changes", tags=["changes"])


@router.get("")
def list_changes(after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000), entity: str = "",
                 db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.GD, Role.ADMIN))):
    events, cursor = settled(db, after=after, limit=limit, entity=entity)
    return {"cursor": cursor, "events": [{"seq": e.seq, "at": e.at, "entity": e.entity, "entity_id": e.entity_id,
                                          "action": e.action, "actor_id": e.actor_id, "deal_id": e.deal_id} for e in events]}
