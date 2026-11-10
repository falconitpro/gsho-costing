"""Эксплуатация: статус резервных копий (docs/operations/09 §3). Только чтение, без секретов.

Ручной запуск из CRM не реализован (D-026): у backend нет безопасного способа запустить host-скрипт,
поэтому UI показывает фиксированную команду для администратора сервера.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..db import get_db
from ..models import BackupRun
from ..services import backup_status as bs

router = APIRouter(prefix="/api/operations", tags=["operations"])


def _reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can("backup_status", 1):
        raise HTTPException(403, "Нет доступа к статусу резервных копий")
    return user


@router.get("/backups/status")
def backup_status(db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    return bs.refresh(db)


@router.get("/backups/runs")
def backup_runs(limit: int = 30, offset: int = 0, db: Session = Depends(get_db), user: CurrentUser = Depends(_reader)):
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    total = db.scalar(select(func.count()).select_from(BackupRun)) or 0
    rows = db.scalars(select(BackupRun).order_by(BackupRun.started_at.desc().nullslast(), BackupRun.created_at.desc()).offset(offset).limit(limit)).all()
    return {"total": int(total), "limit": limit, "offset": offset, "items": [bs.run_dto(r) for r in rows]}
