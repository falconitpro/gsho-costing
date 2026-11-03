"""Удаление и архив сделок (КД и ЗГД). Удалить можно только черновик; сделка в другом статусе переносится в архив
(видна в фильтре «Архив», возвращается из архива). Сделки на стадии контракта и исполнения — только ЗГД."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..core.workflow import DealStatus
from ..db import get_db
from ..models import Deal, FeedEntry, Task
from ..services.deal_service import audit, notify

router = APIRouter(tags=["deal-trash"])
GD_ONLY = {DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED}


class DeleteIn(BaseModel):
    reason: str = ""


def _need(user: CurrentUser) -> None:
    if not user.has(Role.CD, Role.GD):
        raise HTTPException(403, "Удалять и восстанавливать сделки могут КД и ЗГД")


def _close_tasks(db: Session, deal: Deal, now: datetime) -> int:
    n = 0
    for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.done_at.is_(None))).all():
        t.done_at = now
        n += 1
    return n


@router.post("/api/deals/{deal_id}/delete")
def delete_deal(deal_id: str, body: DeleteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Черновик — удаляется (пропадает из списков, задач и отчётов; запись остаётся в журнале).
    Сделка в другом статусе не удаляется, а переносится в архив: её можно открыть и вернуть из архива."""
    _need(user)
    deal = db.get(Deal, deal_id)
    if deal is None or deal.deleted_at:
        raise HTTPException(404, "Сделка не найдена")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(422, "Укажите причину")
    now = datetime.now(timezone.utc)
    if deal.status != DealStatus.DRAFT:
        if deal.archived_at:
            raise HTTPException(409, "Сделка уже в архиве")
        if deal.status in GD_ONLY and not user.has(Role.GD):
            raise HTTPException(403, "Сделку на стадии контракта или исполнения переносит в архив только ЗГД")
        deal.archived_at, deal.archived_by, deal.archive_reason = now, user.id, reason
        closed = _close_tasks(db, deal, now)
        db.add(FeedEntry(deal_id=deal.id, kind="system", direction="out", subject="Сделка перенесена в архив", body=f"{user.display_name}: {reason}"))
        if deal.manager_id and deal.manager_id != user.id:
            notify(db, deal, deal.manager_id, "deal_archived", None, f"{deal.number}: сделка перенесена в архив ({user.display_name}) — {reason}")
        audit(db, user.id, deal.id, "deal", deal.id, "archive", {"status": deal.status.value}, {"reason": reason}, reason)
        db.commit()
        return {"deleted": False, "archived": True, "number": deal.number, "tasks_closed": closed}
    deal.deleted_at, deal.deleted_by, deal.delete_reason = now, user.id, reason
    closed = _close_tasks(db, deal, now)
    if deal.manager_id and deal.manager_id != user.id:
        notify(db, deal, deal.manager_id, "deal_deleted", None, f"{deal.number}: черновик сделки удалён ({user.display_name}) — {reason}")
    audit(db, user.id, deal.id, "deal", deal.id, "delete", {"status": deal.status.value}, {"reason": reason}, reason)
    db.commit()
    return {"deleted": True, "archived": False, "number": deal.number, "tasks_closed": closed}


@router.post("/api/deals/{deal_id}/unarchive")
def unarchive_deal(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _need(user)
    deal = db.get(Deal, deal_id)
    if deal is None or deal.deleted_at or not deal.archived_at:
        raise HTTPException(404, "Сделка в архиве не найдена")
    if deal.status in GD_ONLY and not user.has(Role.GD):
        raise HTTPException(403, "Сделку на стадии контракта или исполнения возвращает из архива только ЗГД")
    before = {"archived_at": deal.archived_at.isoformat(), "reason": deal.archive_reason}
    deal.archived_at, deal.archived_by = None, None
    db.add(FeedEntry(deal_id=deal.id, kind="system", direction="out", subject="Сделка возвращена из архива", body=f"Вернул(а) {user.display_name}"))
    audit(db, user.id, deal.id, "deal", deal.id, "unarchive", before, {})
    db.commit()
    return {"archived": False, "id": deal.id, "number": deal.number}
