"""Изображения и чертежи позиции (задача 10), опции, реализуемые в России (задача 6), настройки подачи КП (задачи 9, 10)."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..core.workflow import add_business_days
from ..db import get_db
from ..models import Attachment, LocalOption, Position, PositionImage, Task
from ..services.deal_service import audit, ensure_folders, notify
from .common import get_deal_or_404, serialize

router = APIRouter(prefix="/api/deals/{deal_id}", tags=["images-local-options"])
IMAGE_KINDS = {"photo", "render", "drawing", "scheme"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".pdf", ".dwg", ".dxf", ".svg"}


def _pos(db: Session, deal, pos_id: str) -> Position:
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404, "Позиция не найдена")
    return pos


# ------------------------------------------------------------------ изображения

@router.post("/positions/{pos_id}/images", status_code=201)
async def upload_image(deal_id: str, pos_id: str, file: UploadFile = File(...), kind: str = Form("photo"), source: str = Form("own"),
                       caption: str = Form(""), has_factory_logo: bool = Form(False), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Загружают менеджер, КД, ГД: фото, рендеры, чертежи (PDF/DWG/DXF — как вложение без предпросмотра)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("images", 2):
        raise HTTPException(403, "Файлы загружают менеджер, КД и ГД")
    pos = _pos(db, deal, pos_id)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in IMAGE_EXT:
        raise HTTPException(422, f"Формат {ext or '?'} не поддерживается: {', '.join(sorted(IMAGE_EXT))}")
    d = tempfile.mkdtemp()
    path = os.path.join(d, file.filename or f"image{ext}")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        ensure_folders(db, deal)
        stored = adapters.storage().upload(path, deal.storage_folder, "01_rfq", f"img_{pos.model}_{file.filename}".replace("/", "-"))
        att = Attachment(deal_id=deal.id, folder="01_rfq", entity="position", entity_id=pos.id, file_name=file.filename or "image",
                         storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id)
    except Exception as e:
        raise HTTPException(502, f"Хранилище недоступно: {e}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    db.add(att)
    db.flush()
    img = PositionImage(deal_id=deal.id, position_id=pos.id, attachment_id=att.id, kind=kind if kind in IMAGE_KINDS else "photo",
                        source=source if source in ("client", "factory", "own") else "own", caption=caption,
                        has_factory_logo=has_factory_logo or source == "factory", uploaded_by=user.id)
    db.add(img)
    db.flush()
    audit(db, user.id, deal.id, "position_image", img.id, "upload", after={"file": file.filename, "kind": img.kind, "source": img.source})
    db.commit()
    return {**serialize(img), "file_name": att.file_name, "url": f"/api/files/{att.storage_path}"}


@router.post("/offers/{offer_id}/images", status_code=201)
async def upload_offer_image(deal_id: str, offer_id: str, file: UploadFile = File(...), caption: str = Form(""), kind: str = Form("photo"),
                             db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Задача 10: фото и чертежи из ответа завода — к ответу-кандидату (задача 9). При отборе завода попадают в позицию
    с источником «завод» и отметкой «есть логотип завода» (на проверку перед вложением в КП)."""
    from ..models import FactoryOffer
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("images", 2):
        raise HTTPException(403, "Файлы загружают менеджер, КД и ГД")
    offer = db.get(FactoryOffer, offer_id)
    if offer is None or offer.deal_id != deal.id:
        raise HTTPException(404, "Ответ завода не найден")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in IMAGE_EXT:
        raise HTTPException(422, f"Формат {ext or '?'} не поддерживается: {', '.join(sorted(IMAGE_EXT))}")
    d = tempfile.mkdtemp()
    path = os.path.join(d, file.filename or f"image{ext}")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        ensure_folders(db, deal)
        stored = adapters.storage().upload(path, deal.storage_folder, "01_rfq", f"img_завод_{file.filename}".replace("/", "-"))
    except Exception as e:
        raise HTTPException(502, f"Хранилище недоступно: {e}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    att = Attachment(deal_id=deal.id, folder="01_rfq", entity="factory_offer", entity_id=offer.id, file_name=file.filename or "image",
                     storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id)
    db.add(att)
    db.flush()
    offer.images = [*(offer.images or []), att.id]
    # ответ завода этой позиции — сразу в блок изображений позиции (на проверку логотипа)
    pos = db.get(Position, offer.source_position_id) if offer.source_position_id else None
    if pos and pos.factory_id == offer.factory_id:
        db.add(PositionImage(deal_id=deal.id, position_id=pos.id, attachment_id=att.id, kind=kind if kind in IMAGE_KINDS else "photo",
                             source="factory", caption=caption, has_factory_logo=True, uploaded_by=user.id))
    audit(db, user.id, deal.id, "factory_offer", offer.id, "image", after={"file": file.filename})
    db.commit()
    return {"offer_id": offer.id, "attachment_id": att.id, "file_name": att.file_name, "url": f"/api/files/{att.storage_path}", "images": len(offer.images)}


@router.get("/positions/{pos_id}/images")
def list_images(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("images"):
        raise HTTPException(403)
    pos = _pos(db, deal, pos_id)
    out = []
    for img in sorted(pos.image_rows or [], key=lambda i: (not i.in_kp, i.kp_order)):
        att = db.get(Attachment, img.attachment_id)
        out.append({**serialize(img), "file_name": att.file_name if att else "", "url": f"/api/files/{att.storage_path}" if att else "",
                    "is_image": bool(att) and os.path.splitext(att.file_name)[1].lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg")})
    return out


class ImagePatch(BaseModel):
    kind: Optional[str] = None
    source: Optional[str] = None
    caption: Optional[str] = None
    has_factory_logo: Optional[bool] = None
    send_to_factory: Optional[bool] = None
    in_kp: Optional[bool] = None
    kp_order: Optional[int] = None


@router.patch("/positions/{pos_id}/images/{image_id}")
def patch_image(deal_id: str, pos_id: str, image_id: str, body: ImagePatch, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подписи и отметки — менеджер/КД/ГД; выбор «В КП» и порядок — только КД/ГД."""
    deal = get_deal_or_404(db, deal_id, user)
    img = db.get(PositionImage, image_id)
    if img is None or img.position_id != pos_id or img.deal_id != deal.id:
        raise HTTPException(404)
    data = body.model_dump(exclude_none=True)
    if ("in_kp" in data or "kp_order" in data) and not user.can("kp_images_select", 2):
        raise HTTPException(403, "Файлы для КП выбирает КД или ГД")
    if not user.can("images", 2):
        raise HTTPException(403)
    for k, v in data.items():
        setattr(img, k, v)
    audit(db, user.id, deal.id, "position_image", img.id, "update", after=data)
    db.commit()
    return serialize(img)


@router.delete("/positions/{pos_id}/images/{image_id}", status_code=204)
def delete_image(deal_id: str, pos_id: str, image_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("images", 2):
        raise HTTPException(403)
    img = db.get(PositionImage, image_id)
    if img and img.deal_id == deal.id:
        db.delete(img)
        db.commit()


class KpSettingsIn(BaseModel):
    kp_brand: Optional[str] = None          # factory | company (задача 9)
    kp_images_mode: Optional[str] = None    # document | attachment | both (задача 10)


@router.patch("/kp-settings")
def kp_settings(deal_id: str, body: KpSettingsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Бренд подачи и способ вложения изображений в КП — КД или ГД."""
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("kp_images_select", 2):
        raise HTTPException(403, "Бренд подачи и вложения в КП задаёт КД или ГД")
    from ..services.offers_service import kp_model_name
    if body.kp_brand == "company":
        missing = [p.designation for p in deal.positions if not kp_model_name(p, db)]
        if missing:
            raise HTTPException(422, {"message": "Задайте наименование под брендом компании в каталоге", "positions": missing})
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "kp_brand" and v not in ("factory", "company"):
            raise HTTPException(422, "kp_brand: factory | company")
        if k == "kp_images_mode" and v not in ("document", "attachment", "both"):
            raise HTTPException(422, "kp_images_mode: document | attachment | both")
        setattr(deal, k, v)
    audit(db, user.id, deal.id, "deal", deal.id, "kp_settings", after=body.model_dump(exclude_none=True))
    db.commit()
    return {"kp_brand": deal.kp_brand, "kp_images_mode": deal.kp_images_mode}


# ------------------------------------------------------------------ опции, реализуемые в России (задача 6)

class LocalOptionIn(BaseModel):
    name: str
    qty: int = 1
    supplier: str = ""
    cost: float = 0.0
    currency: str = "RUB"
    supplier_lead_days: int = 30
    needs_install: bool = True
    install_stage: str = "during_commissioning"   # before_handover | during_commissioning
    installer: str = "service"
    install_days: int = 0
    install_cost_rub: float = 0.0
    comment: str = ""
    tech_param_id: Optional[str] = None
    quote_attachment_id: Optional[str] = None


def _option_tasks(db: Session, deal, pos: Position, o: LocalOption, user_id: str) -> None:
    """Задачи на этапе расчёта: менеджеру — КП поставщиков, сервису — оценка установки, КД — утвердить стоимость."""
    existing = {t.block for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.done_at.is_(None))).all()}
    today = date.today()
    specs = [(Role.MANAGER, deal.manager_id, f"Запросить КП поставщиков опции «{o.name}» ({pos.designation})", f"local_option_quote:{o.id}", 2)]
    if o.needs_install:
        specs.append((Role.SERVICE_HEAD, deal.service_head_id, f"Оценить время и стоимость установки опции «{o.name}» ({pos.designation})", f"local_option_install_est:{o.id}", 2))
    specs.append((Role.CD, None, f"Утвердить стоимость опции РФ «{o.name}» ({pos.designation})", f"local_option_approve:{o.id}", 2))
    from ..services.deal_service import _assignee
    from ..core.workflow import TaskSpec
    for role, assignee, title, block, days in specs:
        if block in existing:
            continue
        spec = TaskSpec(role, title, add_business_days(today, days), block)
        a = assignee or _assignee(db, deal, spec)
        db.add(Task(deal_id=deal.id, role=role.value, assignee_id=a, title=title, block=block, due=spec.due))
        notify(db, deal, a, "task_new", ["app"], f"{deal.number}: {title}")


@router.post("/positions/{pos_id}/local-options", status_code=201)
def add_option(deal_id: str, pos_id: str, body: LocalOptionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("local_options", 2):
        raise HTTPException(403)
    pos = _pos(db, deal, pos_id)
    if body.install_stage not in ("before_handover", "during_commissioning"):
        raise HTTPException(422, "install_stage: before_handover | during_commissioning")
    o = LocalOption(deal_id=deal.id, position_id=pos.id, **body.model_dump())
    db.add(o)
    db.flush()
    if body.tech_param_id:
        from ..models import PositionTechParam
        r = db.get(PositionTechParam, body.tech_param_id)
        if r and r.position_id == pos.id:
            r.local_ru = True
    _option_tasks(db, deal, pos, o, user.id)
    audit(db, user.id, deal.id, "local_option", o.id, "create", after=body.model_dump())
    db.commit()
    return serialize(o)


@router.patch("/positions/{pos_id}/local-options/{opt_id}")
def patch_option(deal_id: str, pos_id: str, opt_id: str, body: dict, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("local_options", 2):
        raise HTTPException(403)
    o = db.get(LocalOption, opt_id)
    if o is None or o.position_id != pos_id:
        raise HTTPException(404)
    allowed = set(LocalOptionIn.model_fields) | {"status"}
    before = serialize(o)
    for k, v in body.items():
        if k in allowed:
            setattr(o, k, v)
    if body.get("status") == "approved" or body.get("approve"):
        if not user.has(Role.CD, Role.GD, Role.PURCHASING):
            raise HTTPException(403, "Стоимость опции утверждает КД (или закупки)")
        o.status, o.cost_approved_by = "approved", user.id
        from datetime import datetime, timezone
        for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.block == f"local_option_approve:{o.id}", Task.done_at.is_(None))).all():
            t.done_at = datetime.now(timezone.utc)
    if "cost" in body and user.has(Role.MANAGER):
        from datetime import datetime, timezone
        for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.block == f"local_option_quote:{o.id}", Task.done_at.is_(None))).all():
            t.done_at = datetime.now(timezone.utc)
    if ("install_days" in body or "install_cost_rub" in body) and user.has(Role.SERVICE_HEAD):
        from datetime import datetime, timezone
        for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.block == f"local_option_install_est:{o.id}", Task.done_at.is_(None))).all():
            t.done_at = datetime.now(timezone.utc)
    audit(db, user.id, deal.id, "local_option", o.id, "update", {k: before.get(k) for k in body}, body)
    db.commit()
    return serialize(o)


@router.delete("/positions/{pos_id}/local-options/{opt_id}", status_code=204)
def delete_option(deal_id: str, pos_id: str, opt_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("local_options", 2):
        raise HTTPException(403)
    o = db.get(LocalOption, opt_id)
    if o and o.position_id == pos_id:
        from datetime import datetime, timezone
        for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.done_at.is_(None), Task.block.like(f"local_option_%:{o.id}"))).all():
            t.done_at = datetime.now(timezone.utc)
        db.delete(o)
        db.commit()
