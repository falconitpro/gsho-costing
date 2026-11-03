"""Документы сделки: файлы клиента (тендерная документация, типовая форма договора, ТЗ и прочее) — загружает менеджер при
заведении сделки и позже; юридические документы (протокол разногласий, заключение) — юрист. Проверка юриста по сделке."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..core.workflow import add_business_days
from ..db import get_db
from ..models import Attachment, Deal, FeedEntry, Task, User
from ..services.deal_service import audit, complete_tasks, ensure_folders, notify
from .common import get_deal_or_404

router = APIRouter(prefix="/api/deals/{deal_id}", tags=["documents"])

CLIENT_CATEGORIES = {"tender": "Тендерная документация", "contract_form": "Типовая форма договора клиента", "client_spec": "Техническое задание клиента",
                     "client_other": "Прочие файлы клиента"}
LEGAL_CATEGORIES = {"disagreements": "Протокол разногласий", "legal_opinion": "Заключение юриста", "contract_draft": "Проект договора (редакция юриста)",
                    "contract_signed": "Подписанный договор"}
CATEGORIES = {**CLIENT_CATEGORIES, **LEGAL_CATEGORIES}
REVIEW_TITLES = {"in_review": "на проверке у юриста", "ok": "проверено, замечаний нет", "remarks": "есть замечания (протокол разногласий)"}
NEEDS_LAWYER = {"tender", "contract_form"}


def _can_upload(user: CurrentUser, deal: Deal, category: str) -> bool:
    if user.has(Role.GD, Role.CD, Role.LAWYER):
        return True
    if category in CLIENT_CATEGORIES:
        return user.has(Role.MANAGER) and deal.manager_id == user.id
    return False


def _rows(db: Session, deal: Deal) -> list[dict]:
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    out = []
    for a in db.scalars(select(Attachment).where(Attachment.deal_id == deal.id, Attachment.entity == "deal_doc").order_by(Attachment.uploaded_at.desc())).all():
        out.append({"id": a.id, "category": a.category, "category_title": CATEGORIES.get(a.category, a.category), "legal": a.category in LEGAL_CATEGORIES,
                    "file_name": a.file_name, "comment": a.comment, "size": a.size, "url": f"/api/files/{a.storage_path}",
                    "uploaded_by": names.get(a.uploaded_by, ""), "uploaded_at": a.uploaded_at.isoformat() if a.uploaded_at else None,
                    "uploaded_by_id": a.uploaded_by})
    return out


def _review(db: Session, deal: Deal) -> dict:
    r = dict(deal.legal_review or {})
    if r.get("by"):
        u = db.get(User, r["by"])
        r["by_name"] = u.display_name if u else ""
    r["title"] = REVIEW_TITLES.get(r.get("status", ""), "не проверялось")
    return r


@router.get("/documents")
def documents(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("legal_docs"):
        raise HTTPException(403, "Документы сделки закрыты для роли")
    return {"documents": _rows(db, deal), "client_categories": CLIENT_CATEGORIES, "legal_categories": LEGAL_CATEGORIES, "review": _review(db, deal),
            "can_upload_client": _can_upload(user, deal, "tender"), "can_upload_legal": _can_upload(user, deal, "disagreements"),
            "can_review": user.has(Role.LAWYER, Role.CD, Role.GD)}


@router.post("/documents", status_code=201)
async def upload_document(deal_id: str, file: UploadFile = File(...), category: str = Form("client_other"), comment: str = Form(""),
                          db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if category not in CATEGORIES:
        raise HTTPException(422, "Неизвестный вид документа")
    if not _can_upload(user, deal, category):
        raise HTTPException(403, "Файлы клиента загружают менеджер сделки, КД, ЗГД, юрист; юридические документы — юрист, КД, ЗГД")
    d = tempfile.mkdtemp()
    name = os.path.basename(file.filename or "document")
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        ensure_folders(db, deal)
        stored = adapters.storage().upload(path, deal.storage_folder, "08_legal", f"{CATEGORIES[category]} — {name}".replace("/", "-"))
    except Exception as e:
        raise HTTPException(502, f"Хранилище недоступно: {e}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    att = Attachment(deal_id=deal.id, folder="08_legal", entity="deal_doc", entity_id=deal.id, category=category, comment=comment, file_name=name,
                     storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id)
    db.add(att)
    db.add(FeedEntry(deal_id=deal.id, kind="file", subject=f"{CATEGORIES[category]}: {name}", body=comment, user_id=user.id))
    # тендерная документация и форма договора клиента → задача юристу на проверку (одна открытая задача на сделку)
    if category in NEEDS_LAWYER and (deal.legal_review or {}).get("status") != "in_review":
        if not db.scalar(select(Task.id).where(Task.deal_id == deal.id, Task.block == "legal", Task.done_at.is_(None))):
            db.add(Task(deal_id=deal.id, role=Role.LAWYER.value, assignee_id=None, title="Проверить документы клиента: тендерная документация, форма договора",
                        block="legal", due=add_business_days(date.today(), 2)))
        deal.legal_review = {"status": "in_review", "comment": "", "by": None, "at": None}
    elif category in LEGAL_CATEGORIES and deal.manager_id and deal.manager_id != user.id:
        notify(db, deal, deal.manager_id, "legal_doc", None, f"{deal.number}: юрист загрузил «{CATEGORIES[category]}» — {name}")
    audit(db, user.id, deal.id, "deal_doc", att.id, "upload", after={"category": CATEGORIES[category], "file": name, "comment": comment})
    db.commit()
    return {"documents": _rows(db, deal), "review": _review(db, deal)}


@router.delete("/documents/{att_id}")
def delete_document(deal_id: str, att_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Ошибочно загруженный файл убирается из документов сделки (файл в хранилище и запись в журнале сохраняются)."""
    deal = get_deal_or_404(db, deal_id, user)
    a = db.get(Attachment, att_id)
    if a is None or a.deal_id != deal.id or a.entity != "deal_doc":
        raise HTTPException(404)
    if a.uploaded_by != user.id and not user.has(Role.CD, Role.GD):
        raise HTTPException(403, "Убрать файл может тот, кто его загрузил, КД или ЗГД")
    a.entity = "deal_doc_removed"
    audit(db, user.id, deal.id, "deal_doc", a.id, "delete", before={"category": CATEGORIES.get(a.category, a.category), "file": a.file_name})
    db.commit()
    return {"documents": _rows(db, deal)}


class ReviewIn(BaseModel):
    status: str             # ok | remarks | in_review
    comment: str = ""


@router.post("/legal-review")
def legal_review(deal_id: str, body: ReviewIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Итог проверки юриста: «замечаний нет» или «есть замечания» (протокол разногласий — файлом); задача юриста закрывается,
    менеджер и КД получают уведомление."""
    deal = get_deal_or_404(db, deal_id, user)
    if not user.has(Role.LAWYER, Role.CD, Role.GD):
        raise HTTPException(403, "Итог проверки вносит юрист (или КД / ЗГД)")
    if body.status not in REVIEW_TITLES:
        raise HTTPException(422, "Итог: на проверке, замечаний нет или есть замечания")
    if body.status == "remarks" and not body.comment.strip():
        raise HTTPException(422, "Опишите замечания")
    before = dict(deal.legal_review or {})
    deal.legal_review = {"status": body.status, "comment": body.comment, "by": user.id, "at": datetime.now(timezone.utc).isoformat()}
    if body.status != "in_review":
        complete_tasks(db, deal, block="legal")
    text = f"{deal.number}: юридическая проверка — {REVIEW_TITLES[body.status]}" + (f": {body.comment}" if body.comment else "")
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject="Юридическая проверка", body=text, user_id=user.id))
    for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
        if u.id == deal.manager_id or "cd" in (u.roles or []):
            if u.id != user.id:
                notify(db, deal, u.id, "legal_review", None, text)
    audit(db, user.id, deal.id, "deal", deal.id, "legal_review", before, deal.legal_review, body.comment)
    db.commit()
    return {"review": _review(db, deal)}
