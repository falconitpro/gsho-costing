"""Технические характеристики позиции (задача 4), запрос заводам и ответы-кандидаты (задача 9), импорт таблиц с предпросмотром."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..core.workflow import DealStatus
from ..db import get_db
from ..models import Attachment, Factory, FactoryOffer, Position, PositionTechParam
from ..services import offers_service, tech_params_service as tps
from ..services.deal_service import audit, ensure_folders
from .common import get_deal_or_404, serialize

router = APIRouter(prefix="/api/deals/{deal_id}", tags=["tech-params"])


def _pos(db: Session, deal, pos_id: str) -> Position:
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404, "Позиция не найдена")
    return pos


def _can_edit(user: CurrentUser, deal) -> None:
    if user.has(Role.GD, Role.CD):
        return
    if not (user.can("tech_params", 2) and user.has(Role.MANAGER) and deal.manager_id == user.id):
        raise HTTPException(403, "Таблицу технических характеристик ведёт менеджер сделки")


def _tmp(file: UploadFile) -> str:
    d = tempfile.mkdtemp()
    path = os.path.join(d, file.filename or "table.xlsx")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return path


# ------------------------------------------------------------------ таблица

class RowIn(BaseModel):
    id: Optional[str] = None
    no: Optional[int] = None
    code: str = ""
    name_ru: str
    name_zh: str = ""
    unit: str = ""
    customer_requirement_ru: str = ""
    value_ru: str = ""
    value_zh: str = ""
    comment_ru: str = ""
    required: bool = False
    local_ru: bool = False
    section: str = ""


@router.get("/positions/{pos_id}/tech-params")
def get_rows(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    pos = _pos(db, deal, pos_id)
    if not user.can("tech_params"):
        raise HTTPException(403)
    if not pos.tech_param_rows:
        tps.init_position_rows(db, pos, user.id)
        db.commit()
    rows = tps.row_dicts(pos)
    for r in rows:   # соответствие значения завода требованию заказчика: match | mismatch | none (нет значения или требования)
        val, req = (r["value_ru"] or r["value_zh"] or "").strip(), (r["customer_requirement_ru"] or "").strip()
        r["req_status"] = offers_service.req_status(val, req) if val and req else "none"
    missing_required = [r["name_ru"] for r in rows if r["required"] and not r["local_ru"] and not (r["value_ru"] or r["value_zh"])]
    return {"rows": rows, "missing_required": missing_required, "machine_unverified": sum(1 for r in rows if r["translation_status"] == "machine"),
            "mismatched": sum(1 for r in rows if r["req_status"] == "mismatch"),
            "columns": [{"key": k, "title": tps.COLUMN_TITLES[k]} for k in tps.COLUMNS]}


@router.put("/positions/{pos_id}/tech-params")
def save_rows(deal_id: str, pos_id: str, rows: list[RowIn], db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Сохранение таблицы целиком (ручной ввод, дозаполнение, отметки обязательности и «реализуется в России»)."""
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    pos = _pos(db, deal, pos_id)
    existing = {r.id: r for r in pos.tech_param_rows or []}
    keep: set[str] = set()
    for i, r in enumerate(rows, 1):
        row = existing.get(r.id) if r.id else None
        data = r.model_dump(exclude={"id"})
        data["no"] = data["no"] or i
        if not data["section"]:
            data["section"] = row.section if (row := existing.get(r.id) if r.id else None) else tps.section_label(data["code"])
        if row is None:
            row = PositionTechParam(position_id=pos.id, source="manual", filled_by=user.id, **data)
            db.add(row)
            db.flush()
        else:
            if row.value_ru != data["value_ru"]:
                row.translation_status = "verified" if row.translation_status == "machine" else row.translation_status
            for k, v in data.items():
                setattr(row, k, v)
            row.filled_by = user.id
        keep.add(row.id)
    for rid, row in existing.items():
        if rid not in keep:
            db.delete(row)
    audit(db, user.id, deal.id, "tech_params", pos.id, "save", after={"rows": len(rows)})
    db.commit()
    db.refresh(pos)
    return {"rows": tps.row_dicts(pos)}


@router.post("/positions/{pos_id}/tech-params/verify/{row_id}")
def verify_translation(deal_id: str, pos_id: str, row_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Подтверждение машинного перевода: строка помечается проверенной, перевод попадает в глоссарий как подтверждённый."""
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    row = db.get(PositionTechParam, row_id)
    if row is None or row.position_id != pos_id:
        raise HTTPException(404)
    row.translation_status = "verified"
    from ..models import Glossary
    if row.value_zh and row.value_ru:
        g = db.scalar(select(Glossary).where(Glossary.term_ru == row.value_ru[:512], Glossary.kind == "value"))
        if g is None:
            db.add(Glossary(term_ru=row.value_ru[:512], term_zh=row.value_zh[:512], kind="value", status="confirmed", changed_by=user.id))
        else:
            g.term_zh, g.status, g.changed_by = row.value_zh[:512], "confirmed", user.id
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ импорт файла клиента / ответа завода

def _mapping_scope(db: Session, deal, pos: Position, target: str, offer_id: str) -> str:
    """Чьё сопоставление запоминаем: файл клиента — клиент сделки; ответ завода — завод (кандидата или позиции)."""
    if target == "factory_answer" or offer_id:
        fid = pos.factory_id
        if offer_id:
            o = db.get(FactoryOffer, offer_id)
            fid = o.factory_id if o else fid
        return f"factory:{fid}"
    return f"client:{deal.client_id}"


@router.post("/positions/{pos_id}/tech-params/import/preview")
async def import_preview(deal_id: str, pos_id: str, file: UploadFile = File(...), target: str = Form("requirements"), offer_id: str = Form(""),
                         db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Предпросмотр: листы, строки, сопоставление столбцов и строка заголовков (пользователь уточняет).
    Если файл того же формата уже загружался от этого клиента/завода — подставляется запомненное сопоставление (mapping.source = saved)."""
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    pos = _pos(db, deal, pos_id)
    path = _tmp(file)
    try:
        return tps.preview(path, file.filename or "", db, _mapping_scope(db, deal, pos, target, offer_id), "factory_answer" if offer_id else target)
    except Exception as e:
        raise HTTPException(422, f"Не удалось прочитать файл: {e}")
    finally:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)


@router.post("/positions/{pos_id}/tech-params/import/apply")
async def import_apply(deal_id: str, pos_id: str, file: UploadFile = File(...), mapping: str = Form(...), target: str = Form("requirements"),
                       selected_rows: str = Form(""), add_unmatched: bool = Form(True), offer_id: str = Form(""),
                       db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Применить выбранное сопоставление. target=requirements — файл клиента; factory_answer — ответ завода (ZH→RU через глоссарий/переводчик).
    offer_id — ответ конкретного завода при запросе нескольким заводам (задача 9)."""
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    pos = _pos(db, deal, pos_id)
    try:
        mp = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(422, "mapping: JSON {sheet, header_row, first_row, last_row, columns:{key: index}}")
    sel = [int(x) for x in selected_rows.split(",") if x.strip()] if selected_rows else None
    path = _tmp(file)
    try:
        if offer_id:
            offer = db.get(FactoryOffer, offer_id)
            if offer is None or offer.deal_id != deal.id:
                raise HTTPException(404, "Ответ завода не найден")
            # ответ стороннего завода пишем в кандидата, не трогая строки позиции: временная копия строк
            snapshot = [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in pos.tech_param_rows]
            res = tps.apply_import(db, pos, path, file.filename or "", mp, user.id, target="factory_answer", selected_rows=sel, add_unmatched=add_unmatched)
            offer.params = [{k: r.get(k) for k in ("no", "code", "name_ru", "name_zh", "unit", "customer_requirement_ru", "value_ru", "value_zh", "comment_ru", "comment_zh", "translation_status")} for r in res["rows"]]
            offer.answer_received_at, offer.status = datetime.now(timezone.utc), "answered"
            if offer.factory_id != pos.factory_id or offer.source_position_id != pos.id:
                # восстанавливаем строки позиции из снимка
                for r in pos.tech_param_rows:
                    snap = next((s for s in snapshot if s["id"] == r.id), None)
                    if snap:
                        for k, v in snap.items():
                            if k not in ("id", "position_id", "created_at", "updated_at"):
                                setattr(r, k, v)
                    else:
                        db.delete(r)
            res["offer_id"] = offer.id
        else:
            res = tps.apply_import(db, pos, path, file.filename or "", mp, user.id, target=target, selected_rows=sel, add_unmatched=add_unmatched)
        # запоминаем сопоставление для следующих файлов этого клиента/завода
        try:
            sheets = tps.read_table(path, file.filename or "")
            sheet = next((x for x in sheets if x["name"] == mp.get("sheet")), sheets[0])
            tgt = "factory_answer" if offer_id else target
            tps.remember_mapping(db, _mapping_scope(db, deal, pos, tgt, offer_id), tgt, sheet["rows"], mp, user.id)
        except Exception as e:
            audit(db, user.id, deal.id, "tech_params", pos.id, "mapping_not_saved", comment=str(e)[:300])
        # файл — в папку сделки
        try:
            ensure_folders(db, deal)
            stored = adapters.storage().upload(path, deal.storage_folder, "01_rfq", f"{'ответ_завода' if target == 'factory_answer' else 'требования_клиента'}_{file.filename}")
            db.add(Attachment(deal_id=deal.id, folder="01_rfq", entity="position", entity_id=pos.id, file_name=file.filename or "table.xlsx",
                              storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id))
        except Exception as e:
            audit(db, user.id, deal.id, "tech_params", pos.id, "storage_failed", comment=str(e)[:300])
        audit(db, user.id, deal.id, "tech_params", pos.id, "import", after={"target": target, "matched": res["matched"], "added": res["added"], "offer_id": offer_id or None})
        db.commit()
        return res
    finally:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)


# ------------------------------------------------------------------ запрос заводу / заводам

class RequestIn(BaseModel):
    factory_ids: list[str] = Field(default_factory=list)   # пусто → завод позиции
    image_ids: list[str] = Field(default_factory=list)     # изображения клиента, приложить к запросу (задача 10)
    via_wecom: bool = True
    via_mail: bool = True
    request_images: bool = True
    client_files_checked: bool = False                     # задача 10: менеджер проверил файлы клиента на его данные


@router.post("/positions/{pos_id}/tech-params/request")
def send_request(deal_id: str, pos_id: str, body: RequestIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Двуязычный запрос (RU+ZH) одному или нескольким заводам: файл с требованиями заказчика, просьба приложить фото и чертежи.
    По каждому заводу создаётся ответ-кандидат (задача 9). Машинные переводы возвращаются списком на проверку."""
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    pos = _pos(db, deal, pos_id)
    ids = body.factory_ids or [pos.factory_id]
    client_files = [i for i in (pos.image_rows or []) if i.id in body.image_ids and i.source == "client"]
    if client_files and not body.client_files_checked:
        raise HTTPException(422, {"message": "На файлах клиента могут быть его название, логотип и контакты. Проверьте файлы перед отправкой заводу.",
                                  "client_files": [i.caption or i.id for i in client_files]})
    image_paths: list[str] = []
    s = adapters.storage()
    for img in pos.image_rows or []:
        if img.id in body.image_ids:
            att = db.get(Attachment, img.attachment_id)
            if att:
                img.send_to_factory = True
                try:
                    image_paths.append(s.download(att.storage_path, os.path.join(tempfile.mkdtemp(), att.file_name)))
                except Exception:
                    pass
    out = []
    for fid in ids:
        factory = db.get(Factory, fid)
        if factory is None:
            raise HTTPException(404, f"Завод {fid} не найден")
        path, machine = tps.build_request_xlsx(db, pos, factory, deal.number, user.id, body.request_images)
        res = tps.send_request(db, deal, pos, factory, path, image_paths, user.id, body.via_wecom, body.via_mail)
        offer = db.scalar(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id, FactoryOffer.factory_id == fid, FactoryOffer.source_position_id == pos.id))
        if offer is None:
            offer = FactoryOffer(deal_id=deal.id, source_position_id=pos.id, factory_id=fid, model=pos.model if fid == pos.factory_id else "", currency=pos.currency)
            db.add(offer)
        offer.request_sent_at = datetime.now(timezone.utc)
        db.flush()
        out.append({"factory_id": fid, "factory": factory.name, "offer_id": offer.id, "file": res.get("file"), "wecom": res.get("wecom"), "mail": res.get("mail"),
                    "machine_translations": machine})
    audit(db, user.id, deal.id, "tech_params", pos.id, "request_sent", after={"factories": ids, "images": len(image_paths)})
    db.commit()
    return {"sent": out}


@router.get("/positions/{pos_id}/tech-params/request/file")
def request_file(deal_id: str, pos_id: str, factory_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Скачать файл запроса (без отправки) — например, чтобы отправить вручную."""
    from fastapi.responses import FileResponse
    deal = get_deal_or_404(db, deal_id, user)
    pos = _pos(db, deal, pos_id)
    factory = db.get(Factory, factory_id or pos.factory_id)
    path, _ = tps.build_request_xlsx(db, pos, factory, deal.number, user.id)
    db.commit()
    return FileResponse(path, filename=os.path.basename(path))


@router.get("/positions/{pos_id}/rfq/file")
def rfq_with_requirements(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Шаблон RFQ, заполненный требованиями заказчика из таблицы позиции (строки «реализуется в России» помечены),
    — завод заполняет значения, цены и логистику; ответ загружается обычным импортом RFQ."""
    from fastapi.responses import FileResponse
    from ..models import Template
    from ..services.rfq_template import fill_rfq_requirements
    deal = get_deal_or_404(db, deal_id, user)
    pos = _pos(db, deal, pos_id)
    if not user.can("tech_params"):
        raise HTTPException(403)
    custom = db.scalar(select(Template).where(Template.kind == "rfq", Template.is_active.is_(True)).order_by(Template.version.desc()))
    dst = os.path.join(tempfile.mkdtemp(), f"{deal.number}_RFQ_{pos.model or 'позиция'}.xlsx")
    fill_rfq_requirements(dst, tps.row_dicts(pos), src=custom.file_path if custom else None)
    return FileResponse(dst, filename=os.path.basename(dst))


# ------------------------------------------------------------------ ответы заводов: кандидаты, сравнение, отбор (задача 9)

class OfferIn(BaseModel):
    factory_id: str
    model: str = ""
    unit_price: Optional[float] = None
    currency: str = "CNY"
    basis: str = "FCA"
    production_term: dict = Field(default_factory=dict)
    factory_payments: list[dict] = Field(default_factory=list)
    params: list[dict] = Field(default_factory=list)
    comment: str = ""
    source_position_id: Optional[str] = None


@router.get("/offers")
def offers(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    rows = db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id).order_by(FactoryOffer.created_at)).all()
    factories = {f.id: f.name for f in db.scalars(select(Factory)).all()}
    out = []
    for o in rows:
        d = serialize(o)
        d["factory"] = factories.get(o.factory_id, o.factory_id)
        if not user.can("factory_price_history"):
            d.pop("unit_price", None); d.pop("factory_payments", None)
        out.append(d)
    return out


@router.post("/offers", status_code=201)
def upsert_offer(deal_id: str, body: OfferIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Ручной ввод ответа завода (цена, срок, условия, параметры) — без файла."""
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    pos_id = body.source_position_id or (deal.positions[0].id if deal.positions else None)
    o = db.scalar(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id, FactoryOffer.factory_id == body.factory_id, FactoryOffer.source_position_id == pos_id))
    if o is None:
        o = FactoryOffer(deal_id=deal.id, source_position_id=pos_id, factory_id=body.factory_id)
        db.add(o)
    for k, v in body.model_dump(exclude_unset=True, exclude={"source_position_id"}).items():
        if k == "params" and not v:
            continue
        setattr(o, k, v)
    o.answer_received_at, o.status = datetime.now(timezone.utc), "answered"
    db.flush()
    audit(db, user.id, deal.id, "factory_offer", o.id, "upsert", after=body.model_dump())
    db.commit()
    return serialize(o)


@router.get("/offers/compare")
def offers_compare(deal_id: str, position_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    pos = _pos(db, deal, position_id) if position_id else (deal.positions[0] if deal.positions else None)
    data = offers_service.compare(db, deal, pos)
    if not user.can("factory_price_history"):
        for c in data["offers"]:
            c.pop("unit_price", None); c.pop("factory_payments", None)
    return data


@router.post("/offers/submit-compare")
def offers_submit(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    offers_service.submit_compare(db, deal, user.id)
    db.commit()
    return {"ok": True}


class SelectIn(BaseModel):
    offer_ids: list[str]
    copy_calcs: bool = True


@router.post("/offers/select")
def offers_select(deal_id: str, body: SelectIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """КД/ГД отбирает заводы для КП: первый — в эту заявку, остальные — отдельные заявки (дублирование)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("factory_offers", 2):
        raise HTTPException(403, "Заводы для КП отбирает КД или ГД")
    if deal.status not in (DealStatus.DRAFT, DealStatus.REWORK, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.CALC_READY):
        raise HTTPException(409, "Отбор заводов выполняется до утверждения КП")
    try:
        created = offers_service.select_offers(db, deal, body.offer_ids, user.id, body.copy_calcs)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, user.id, deal.id, "deal", deal.id, "select_offers", after={"offer_ids": body.offer_ids, "created": [d.number for d in created]})
    db.commit()
    return {"selected": body.offer_ids, "created_deals": [{"id": d.id, "number": d.number} for d in created]}
