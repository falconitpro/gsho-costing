"""Сводка «Ваши приоритеты» (docs/operations/07 §11): тот же расчёт, что в блоке рабочего места, и действия над своими элементами."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..db import get_db
from ..models import BriefingItemState
from ..services import briefing as brf

router = APIRouter(prefix="/api/briefing", tags=["briefing"])


def _ctx(db: Session, user: CurrentUser) -> dict:
    from .workplace import build
    return build(db, user)[1]


@router.get("")
def get_briefing(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from .workplace import briefing_block
    return briefing_block(db, user, _ctx(db, user))


class SigIn(BaseModel):
    source_signature: str


class SnoozeIn(BaseModel):
    source_signature: str
    until: date


@router.post("/items/{item_key:path}/pin")
def pin(item_key: str, body: SigIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    it = brf.find(db, user, _ctx(db, user), item_key, body.source_signature)
    brf.pin(db, user.id, it, True)
    db.commit()
    return {"ok": True, "item_key": it.item_key, "pinned": True}


@router.delete("/items/{item_key:path}/pin")
def unpin(item_key: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    s = db.scalar(select(BriefingItemState).where(BriefingItemState.user_id == user.id, BriefingItemState.item_key == item_key))
    if s is not None:
        s.pinned_at = None
        db.commit()
    return {"ok": True, "item_key": item_key, "pinned": False}


@router.post("/items/{item_key:path}/snooze")
def snooze(item_key: str, body: SnoozeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    it = brf.find(db, user, _ctx(db, user), item_key, body.source_signature)
    until = brf.snooze(db, user.id, it, body.until)
    db.commit()
    return {"ok": True, "item_key": it.item_key, "snoozed_until": until.isoformat()}


@router.post("/items/{item_key:path}/hide")
def hide(item_key: str, body: SigIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    it = brf.find(db, user, _ctx(db, user), item_key, body.source_signature)
    brf.hide(db, user.id, it)
    db.commit()
    return {"ok": True, "item_key": it.item_key, "hidden": True}


@router.get("/states")
def my_states(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Скрытые и отложенные (для «Показать скрытые и отложенные»): только ключ, тип и даты — без содержимого источника."""
    rows = db.scalars(select(BriefingItemState).where(BriefingItemState.user_id == user.id,
                                                      (BriefingItemState.hidden_at.is_not(None)) | (BriefingItemState.snoozed_until.is_not(None)))).all()
    iso = lambda d: d.isoformat() if d else None   # noqa: E731
    cur = {c.item_key: c for c in brf.candidates(db, user, _ctx(db, user))[0]}
    # действующие отметки — только для текущей версии источника (изменившийся источник уже снова в сводке)
    live = [r for r in rows if r.item_key in cur and cur[r.item_key].signature == r.source_signature]
    return {"items": [{"item_key": r.item_key, "title": cur[r.item_key].title, "hidden_at": iso(r.hidden_at), "snoozed_until": iso(r.snoozed_until)}
                      for r in live]}


class ResetIn(BaseModel):
    item_keys: Optional[list[str]] = None      # пусто — все свои отметки «скрыть» и «отложить»


@router.post("/items/reset")
def reset(body: ResetIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    q = select(BriefingItemState).where(BriefingItemState.user_id == user.id)
    if body.item_keys is not None:
        if len(body.item_keys) > 200:
            raise HTTPException(400, "Слишком много элементов")
        q = q.where(BriefingItemState.item_key.in_(body.item_keys))
    n = 0
    cur = {c.item_key: c.signature for c in brf.candidates(db, user, _ctx(db, user))[0]}
    for s in db.scalars(q).all():
        if s.hidden_at or s.snoozed_until:
            n += 1 if cur.get(s.item_key) == s.source_signature else 0
            s.hidden_at, s.snoozed_until = None, None
        if not s.pinned_at:
            db.delete(s)
    db.commit()
    return {"ok": True, "reset": n}
