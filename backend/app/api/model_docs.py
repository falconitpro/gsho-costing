"""Документы модели (руководства, чертежи, схемы, каталог запчастей) в каталоге и в сделке; ЗИП позиции сделки."""
from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import EquipmentExecution, EquipmentModel, ModelDocument, Position, User
from ..services.acceptance_service import DOC_KINDS
from ..services.deal_service import audit
from .common import get_deal_or_404, serialize

router = APIRouter(prefix="/api", tags=["model-docs"])
LANGS = {"ru": "русский", "en": "английский", "zh": "китайский", "de": "немецкий", "other": "другой"}


def _writer(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.roles or user.roles <= {Role.OBSERVER}:
        raise HTTPException(403, "Наблюдатель не загружает документы")
    return user


def _doc_payload(db: Session, d: ModelDocument) -> dict:
    x = serialize(d)
    x.pop("storage_path", None)
    u = db.get(User, d.uploaded_by) if d.uploaded_by else None
    x.update(kind_title=DOC_KINDS.get(d.kind, d.kind), language_title=LANGS.get(d.language, d.language), uploaded_by_name=u.display_name if u else "",
             url=f"/api/catalog/documents/{d.id}")
    return x


def model_docs(db: Session, model_id: str) -> list[ModelDocument]:
    return db.scalars(select(ModelDocument).where(ModelDocument.model_id == model_id, ModelDocument.active.is_(True))
                      .order_by(ModelDocument.kind, ModelDocument.language, ModelDocument.uploaded_at.desc())).all()


def _store(db: Session, model: EquipmentModel, file: UploadFile, kind: str, language: str, version: str, title: str, comment: str,
           user: CurrentUser, deal_id: Optional[str] = None) -> ModelDocument:
    if kind not in DOC_KINDS:
        raise HTTPException(422, "Вид документа: руководство, каталог запчастей, чертежи, схемы, сертификаты или прочее")
    name = os.path.basename(file.filename or "document").replace("/", "-")
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    try:
        size = os.path.getsize(path)
        stored = adapters.storage().upload(path, f"Каталог/{model.model.replace('/', '-')}/Документы", "07_fact",
                                           f"{kind}{('-' + language) if language else ''}{('-' + version) if version else ''} {name}")
    finally:
        os.unlink(path)
    d = ModelDocument(model_id=model.id, kind=kind, title=title.strip() or DOC_KINDS[kind], language=language or "ru", version=version.strip(),
                      file_name=name, storage_path=stored.path, size=size, source_deal_id=deal_id, comment=comment, uploaded_by=user.id)
    db.add(d)
    db.flush()
    audit(db, user.id, deal_id, "model_document", d.id, "upload", None, {"model": model.model, "kind": kind, "language": language, "version": version, "file": name})
    return d


@router.get("/catalog/models/{model_id}/documents")
def list_docs(model_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    if db.get(EquipmentModel, model_id) is None:
        raise HTTPException(404)
    return [_doc_payload(db, d) for d in model_docs(db, model_id)]


@router.post("/catalog/models/{model_id}/documents", status_code=201)
def upload_doc(model_id: str, file: UploadFile = File(...), kind: str = Form("manual"), language: str = Form("ru"), version: str = Form(""),
               title: str = Form(""), comment: str = Form(""), db: Session = Depends(get_db), user: CurrentUser = Depends(_writer)):
    m = db.get(EquipmentModel, model_id)
    if m is None:
        raise HTTPException(404)
    d = _store(db, m, file, kind, language, version, title, comment, user)
    db.commit()
    return _doc_payload(db, d)


@router.get("/catalog/documents/{doc_id}")
def get_doc(doc_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    d = db.get(ModelDocument, doc_id)
    if d is None:
        raise HTTPException(404)
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(d.file_name)[1])
    os.close(fd)
    adapters.storage().download(d.storage_path, path)
    return file_response(path, d.file_name, background=BackgroundTask(os.unlink, path))


@router.delete("/catalog/documents/{doc_id}")
def remove_doc(doc_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_writer)):
    """Убрать документ из модели (файл в хранилище остаётся): загрузивший или КД / ЗГД."""
    d = db.get(ModelDocument, doc_id)
    if d is None:
        raise HTTPException(404)
    if d.uploaded_by != user.id and not user.can("catalog", 2):
        raise HTTPException(403, "Убрать документ может загрузивший его или КД / ЗГД")
    d.active = False
    audit(db, user.id, d.source_deal_id, "model_document", d.id, "remove", None, None)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ в сделке: документы модели позиции

def position_model(db: Session, pos: Position, create: bool = False, user_id: Optional[str] = None) -> Optional[EquipmentModel]:
    """Модель каталога позиции: по исполнению, по ссылке позиции, по заводу и модели; при загрузке документа — заводится на проверку."""
    if pos.execution_id:
        ex = db.get(EquipmentExecution, pos.execution_id)
        if ex:
            return db.get(EquipmentModel, ex.model_id)
    if pos.equipment_model_id:
        m = db.get(EquipmentModel, pos.equipment_model_id)
        if m:
            return m
    m = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == pos.factory_id, EquipmentModel.model == pos.model)) if pos.model else None
    if m is None and create:
        if not (pos.model or "").strip():
            raise HTTPException(422, "Укажите модель позиции — документы привязываются к модели")
        m = EquipmentModel(factory_id=pos.factory_id, machine_type=pos.machine_type or "", model=pos.model.strip(), trademark=pos.trademark or "",
                           status="pending", proposed_by=user_id)
        db.add(m)
        db.flush()
    if m is not None and not pos.equipment_model_id:
        pos.equipment_model_id = m.id
    return m


def _pos(db: Session, deal_id: str, pos_id: str, user: CurrentUser) -> Position:
    deal = get_deal_or_404(db, deal_id, user)
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404, "Позиция не найдена")
    return pos


@router.get("/deals/{deal_id}/positions/{pos_id}/documents")
def pos_docs(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    pos = _pos(db, deal_id, pos_id, user)
    m = position_model(db, pos)
    return {"model_id": m.id if m else None, "model": m.model if m else pos.model, "documents": [_doc_payload(db, d) for d in model_docs(db, m.id)] if m else []}


@router.post("/deals/{deal_id}/positions/{pos_id}/documents", status_code=201)
def pos_upload(deal_id: str, pos_id: str, file: UploadFile = File(...), kind: str = Form("manual"), language: str = Form("ru"), version: str = Form(""),
               title: str = Form(""), comment: str = Form(""), db: Session = Depends(get_db), user: CurrentUser = Depends(_writer)):
    """Документ загружается из сделки, но хранится у модели — виден в каталоге и во всех сделках с этой моделью."""
    pos = _pos(db, deal_id, pos_id, user)
    m = position_model(db, pos, create=True, user_id=user.id)
    d = _store(db, m, file, kind, language, version, title, comment, user, deal_id)
    db.commit()
    return _doc_payload(db, d)


# ------------------------------------------------------------------ ЗИП позиции

ZIP_KINDS = {"supply": "в комплекте поставки", "to": "ЗИП на ТО"}


class ZipItem(BaseModel):
    assembly: str = ""                 # узел / система машины
    article: str = ""
    name: str = ""
    unit: str = "шт"
    qty: float = Field(default=1, ge=0)
    price: Optional[float] = Field(default=None, ge=0)
    currency: str = "CNY"
    interval_hours: Optional[float] = Field(default=None, ge=0)   # периодичность замены, м·ч
    kind: str = "supply"               # supply — в комплекте поставки | to — ЗИП на ТО
    lead_days: Optional[int] = Field(default=None, ge=0)          # срок поставки, дн
    comment: str = ""


class ZipIn(BaseModel):
    items: list[ZipItem]


@router.get("/deals/{deal_id}/positions/{pos_id}/zip")
def get_zip(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    pos = _pos(db, deal_id, pos_id, user)
    return {"items": pos.zip_items or [], "kinds": ZIP_KINDS}


@router.put("/deals/{deal_id}/positions/{pos_id}/zip")
def put_zip(deal_id: str, pos_id: str, body: ZipIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ЗИП позиции: в комплекте поставки (идёт в чек-лист приёмки) и на ТО (в ТО-комплекты парка машин)."""
    if not user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.CD, Role.GD):
        raise HTTPException(403, "ЗИП ведут менеджер, сервис, КД и ЗГД")
    pos = _pos(db, deal_id, pos_id, user)
    items = []
    for z in body.items:
        if not (z.article.strip() or z.name.strip()):
            continue
        if z.kind not in ZIP_KINDS:
            raise HTTPException(422, "Вид ЗИП: в комплекте поставки или на ТО")
        items.append({**z.model_dump(), "article": z.article.strip(), "name": z.name.strip(), "currency": (z.currency or "CNY").upper()[:3]})
    before = len(pos.zip_items or [])
    pos.zip_items = items
    audit(db, user.id, deal_id, "position", pos.id, "zip", {"items": before}, {"items": len(items)})
    db.commit()
    return {"items": pos.zip_items, "kinds": ZIP_KINDS}
