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
from ..models import Attachment, Factory, FactoryOffer, FeedEntry, Position, PositionTechParam
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
    if user.has(Role.GD, Role.CD, Role.ADMIN):
        return
    if not (user.can("tech_params", 2) and user.has(Role.MANAGER) and user.is_manager_of(deal.manager_id)):
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
    removed = [row for rid, row in existing.items() if rid not in keep]
    if removed and not user.has(Role.ADMIN, Role.CD, Role.GD):
        raise HTTPException(403, "Удалять строки таблицы технических характеристик могут КД, ЗГД или администратор")
    for row in removed:
        db.delete(row)
    db.flush()
    db.refresh(pos)
    synced = tps.sync_position(db, pos)   # тип машины / модель из таблицы — в позицию (перечень позиций, все роли); страна — названием
    from ..services.offers_service import offer_from_position
    offer_from_position(db, deal, pos, user.id, "из таблицы технических характеристик")   # значения завода → «Заводы и ответы»
    audit(db, user.id, deal.id, "tech_params", pos.id, "save", after={"rows": len(rows), **synced})
    db.commit()
    db.refresh(pos)
    return {"rows": tps.row_dicts(pos), "position": {"machine_type": pos.machine_type, "model": pos.model}, "synced": synced}


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
    """Применить выбранное сопоставление. target=requirements — файл клиента; factory_answer — ответ завода (CN→RU через глоссарий/переводчик).
    offer_id — ответ конкретного завода при запросе нескольким заводам (задача 9)."""
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    pos = _pos(db, deal, pos_id)
    try:
        mp = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(422, "Не задано сопоставление столбцов (лист, строка заголовков, диапазон строк, столбцы)")
    sel = [int(x) for x in selected_rows.split(",") if x.strip()] if selected_rows else None
    path = _tmp(file)
    try:
        if offer_id:
            offer = db.get(FactoryOffer, offer_id)
            if offer is None or offer.deal_id != deal.id:
                raise HTTPException(404, "Ответ завода не найден")
            if offer.end_supplier_id:
                raise HTTPException(409, "Предложение компании группы формируется из ответа конечного поставщика — загрузите его ответ")
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
        db.flush()
        db.refresh(pos)
        res["synced"] = tps.sync_position(db, pos)   # строки позиции (после восстановления снимка) → позиция
        if target == "factory_answer" and not offer_id:   # ответ завода позиции → вкладка «Заводы и ответы»
            from ..services.offers_service import offer_from_position
            o_ = offer_from_position(db, deal, pos, user.id, f"импорт ответа завода: {file.filename or ''}")
            if o_ is not None:
                res["offer_id"] = o_.id
        from ..services.countries import is_country_row, normalize_country
        for r in res.get("rows") or []:          # и в ответе (в т.ч. ответ другого завода) — страна названием
            if is_country_row(r.get("code") or "", r.get("name_ru") or "") and (r.get("value_ru") or r.get("value_zh")):
                r["value_ru"] = normalize_country(r.get("value_ru") or r.get("value_zh"))
        if offer_id and res.get("offer_id"):
            offer.params = [{**p_, "value_ru": normalize_country(p_.get("value_ru") or p_.get("value_zh"))}
                            if is_country_row(p_.get("code") or "", p_.get("name_ru") or "") and (p_.get("value_ru") or p_.get("value_zh")) else p_
                            for p_ in (offer.params or [])]
        if offer_id and res.get("offer_id") and offer.mirror_of_id:   # ответ конечного поставщика → предложение компании группы (D-150)
            from ..services import mirror_offers as mirror
            db.flush()
            mirror.on_lower_changed(db, offer, user.id)
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
    via_group: dict[str, list[str]] = Field(default_factory=dict)   # компания группы → конечные поставщики (зеркальный запрос, D-150)


@router.post("/positions/{pos_id}/tech-params/request")
def send_request(deal_id: str, pos_id: str, body: RequestIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Двуязычный запрос (RU+CN) одному или нескольким заводам: файл с требованиями заказчика, просьба приложить фото и чертежи.
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
    from ..services import chain_rules
    from ..services import mirror_offers as mirror
    from ..services.suppliers import require_approved
    out = []
    plain = []
    fd_gd = user.has(Role.FD, Role.GD)
    for fid in ids:
        f_ = db.get(Factory, fid)
        if f_ is None:
            raise HTTPException(404, f"Завод {fid} не найден")
        if mirror.group_entity(db, f_) is not None:   # компания группы вручную — только ФД/ЗГД (менеджер выбирает конечного поставщика, D-164)
            if not fd_gd:
                raise HTTPException(422, f"Выберите конечного поставщика — закупку через «{f_.short_name or f_.name}» подставят правила цепочек (их ведут ФД и ЗГД)")
            out += mirror.request_via_group(db, deal, pos, f_, body.via_group.get(fid) or [], user.id, image_paths, body.via_wecom, body.via_mail, body.request_images)
            continue
        require_approved(f_, "отправить ему запрос")
        route, gf, rule = chain_rules.resolve(db, f_, "gsho")       # правило по умолчанию: через компанию группы или напрямую
        if route is not None:
            out += mirror.request_via_group(db, deal, pos, gf, [fid], user.id, image_paths, body.via_wecom, body.via_mail, body.request_images,
                                            route=route, rule_note=chain_rules.describe(db, rule))
        else:
            plain.append(fid)
    for fid in plain:
        factory = db.get(Factory, fid)
        path, machine = tps.build_request_xlsx(db, pos, factory, deal.number, user.id, body.request_images)
        res = tps.send_request(db, deal, pos, factory, path, image_paths, user.id, body.via_wecom, body.via_mail)
        offer = db.scalar(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id, FactoryOffer.factory_id == fid, FactoryOffer.source_position_id == pos.id,
                                                     FactoryOffer.mirror_of_id.is_(None), FactoryOffer.end_supplier_id.is_(None)))
        if offer is None:
            offer = FactoryOffer(deal_id=deal.id, source_position_id=pos.id, factory_id=fid, model=pos.model if fid == pos.factory_id else "", currency=pos.currency)
            db.add(offer)
        offer.request_sent_at = datetime.now(timezone.utc)
        db.flush()
        out.append({"factory_id": fid, "factory": factory.name, "offer_id": offer.id, "file": res.get("file"), "wecom": res.get("wecom"), "mail": res.get("mail"),
                    "machine_translations": machine, "rule": "напрямую"})
    audit(db, user.id, deal.id, "tech_params", pos.id, "request_sent", after={"factories": ids, "via_group": body.via_group, "images": len(image_paths)})
    db.commit()
    return {"sent": out}


@router.get("/positions/{pos_id}/tech-params/request/file")
def request_file(deal_id: str, pos_id: str, factory_id: str = "", factory_ids: str = "", db: Session = Depends(get_db),
                 user: CurrentUser = Depends(get_current_user)):
    """Скачать файл запроса (без отправки) — например, чтобы отправить вручную.
    Несколько заводов (factory_ids через запятую) — ZIP с отдельным двуязычным Excel на каждый завод."""
    import zipfile
    from fastapi.responses import FileResponse
    deal = get_deal_or_404(db, deal_id, user)
    pos = _pos(db, deal, pos_id)
    ids = [x for x in (factory_ids or "").split(",") if x] or [factory_id or pos.factory_id]
    factories = [f for f in (db.get(Factory, i) for i in dict.fromkeys(ids)) if f] or [None]
    from ..services.suppliers import require_approved
    for f in factories:
        require_approved(f, "сформировать ему запрос")
    paths = []
    for f in factories:
        path, _ = tps.build_request_xlsx(db, pos, f, deal.number, user.id)
        paths.append((path, f))
    db.commit()
    if len(paths) == 1:
        return FileResponse(paths[0][0], filename=os.path.basename(paths[0][0]))
    dst = os.path.join(tempfile.mkdtemp(), f"{deal.number}_запросы_заводам.zip")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for path, f in paths:
            base, ext = os.path.splitext(os.path.basename(path))
            z.write(path, f"{base}_{(f.name if f else 'завод')[:40]}{ext}".replace("/", "_"))
    return FileResponse(dst, filename=os.path.basename(dst))


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
    offer_id: Optional[str] = None          # конкретный ответ — в т.ч. ответ конечного поставщика на запрос компании группы (D-150)


def _price_visible(user: CurrentUser, o: FactoryOffer) -> bool:
    """Цена ответа: обычный завод — «История цен завода»; конечный поставщик через компанию группы — «Цепочка компаний группы»;
    предложение компании группы — любое из двух (логист видит цепочку целиком, D-152)."""
    if o.mirror_of_id:
        return user.can("intragroup_chain")
    if o.end_supplier_id:
        return user.can("factory_price_history") or user.can("intragroup_chain")
    return user.can("factory_price_history")


@router.get("/offers")
def offers(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    rows = db.scalars(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id).order_by(FactoryOffer.created_at)).all()
    factories = {f.id: f.name for f in db.scalars(select(Factory)).all()}
    from ..services import mirror_offers as mirror
    chain_ok = user.can("intragroup_chain")
    out = []
    for o in rows:
        d = serialize(o)
        d["factory"] = factories.get(o.factory_id, o.factory_id)
        if o.end_supplier_id:
            d["factory"] = f"{d['factory']} ← {factories.get(o.end_supplier_id, '')}"
            d["mirror"] = mirror.view(db, o, chain_ok)
        if o.mirror_of_id:
            d["lower_of"] = o.mirror_of_id
        if not chain_ok:
            d.pop("chain", None); d.pop("transfer_price", None); d.pop("transfer_note", None)
        if not _price_visible(user, o):
            d.pop("unit_price", None); d.pop("factory_payments", None)
        out.append(d)
    return out


@router.post("/offers", status_code=201)
def upsert_offer(deal_id: str, body: OfferIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Ручной ввод ответа завода (цена, срок, условия, параметры) — без файла."""
    from ..services import mirror_offers as mirror
    deal = get_deal_or_404(db, deal_id, user)
    _can_edit(user, deal)
    pos_id = body.source_position_id or (deal.positions[0].id if deal.positions else None)
    if body.offer_id:
        o = db.get(FactoryOffer, body.offer_id)
        if o is None or o.deal_id != deal.id:
            raise HTTPException(404, "Ответ завода не найден")
    else:
        o = db.scalar(select(FactoryOffer).where(FactoryOffer.deal_id == deal.id, FactoryOffer.factory_id == body.factory_id, FactoryOffer.source_position_id == pos_id,
                                                 FactoryOffer.mirror_of_id.is_(None), FactoryOffer.end_supplier_id.is_(None)))
    if o is not None and o.end_supplier_id:
        raise HTTPException(409, "Предложение компании группы формируется из ответа конечного поставщика — внесите его ответ")
    if o is not None and o.mirror_of_id and not user.can("intragroup_chain", 2) and ({"unit_price", "factory_payments"} & body.model_fields_set):
        raise HTTPException(403, "Цену конечного поставщика вносят КД, ФД, ЗГД или логист")
    if o is not None and o.status in ("selected", "rejected") and o.mirror_of_id:
        pass   # изменение ответа после отбора — помечается в предложении компании группы (D-154)
    if o is None:
        o = FactoryOffer(deal_id=deal.id, source_position_id=pos_id, factory_id=body.factory_id)
        db.add(o)
    for k, v in body.model_dump(exclude_unset=True, exclude={"source_position_id", "offer_id", "factory_id"}).items():
        if k == "params" and not v:
            continue
        setattr(o, k, v)
    o.answer_received_at = datetime.now(timezone.utc)
    if o.status not in ("selected", "rejected"):
        o.status = "answered"
    db.flush()
    if o.mirror_of_id:
        mirror.on_lower_changed(db, o, user.id)
    audit(db, user.id, deal.id, "factory_offer", o.id, "upsert", after=body.model_dump())
    db.commit()
    d = serialize(o)
    if not _price_visible(user, o):
        d.pop("unit_price", None); d.pop("factory_payments", None)
    d.pop("chain", None)
    return d


@router.get("/offers/compare")
def offers_compare(deal_id: str, position_id: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    pos = _pos(db, deal, position_id) if position_id else (deal.positions[0] if deal.positions else None)
    data = offers_service.compare(db, deal, pos, can_chain=user.can("intragroup_chain"))
    for c in data["offers"]:
        o = db.get(FactoryOffer, c["offer_id"])
        if not _price_visible(user, o):
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
    """КД/ЗГД отбирает заводы для КП: первый — в эту заявку, остальные — отдельные заявки (дублирование)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("factory_offers", 2):
        raise HTTPException(403, "Заводы для КП отбирает КД или ЗГД")
    if deal.status not in (DealStatus.DRAFT, DealStatus.REWORK, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.CALC_READY):
        raise HTTPException(409, "Отбор заводов выполняется до утверждения КП")
    if any((o := db.get(FactoryOffer, i)) is not None and o.mirror_of_id for i in body.offer_ids):
        raise HTTPException(422, "Отбирается предложение компании группы, а не её запрос конечному поставщику")
    try:
        created = offers_service.select_offers(db, deal, body.offer_ids, user.id, body.copy_calcs)
    except ValueError as e:
        raise HTTPException(422, str(e))
    audit(db, user.id, deal.id, "deal", deal.id, "select_offers", after={"offer_ids": body.offer_ids, "created": [d.number for d in created]})
    db.commit()
    return {"selected": body.offer_ids, "created_deals": [{"id": d.id, "number": d.number} for d in created]}


# ------------------------------------------------------------------ зеркальные предложения компании группы (D-150…D-155)

class TransferPriceIn(BaseModel):
    price: Optional[float] = None       # None — снять фиксацию, вернуться к наценке политики
    note: str = ""


@router.get("/offers/{offer_id}/chain")
def offer_chain(deal_id: str, offer_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Цепочка предложения компании группы: цена конечного поставщика, ступени маршрута, наценки, маржа группы."""
    from ..services import mirror_offers as mirror
    deal = get_deal_or_404(db, deal_id, user)
    o = db.get(FactoryOffer, offer_id)
    if o is None or o.deal_id != deal.id or not o.end_supplier_id:
        raise HTTPException(404, "Предложение компании группы не найдено")
    if not user.can("intragroup_chain"):
        raise HTTPException(403, "Цепочка компаний группы доступна КД, ФД, ЗГД и логисту")
    return {"offer_id": o.id, "unit_price": o.unit_price, "currency": o.currency, "basis": o.basis, **mirror.view(db, o, True)}


@router.post("/offers/{offer_id}/transfer-price")
def offer_transfer_price(deal_id: str, offer_id: str, body: TransferPriceIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ФД или ЗГД фиксирует цену компании группы Берг-Хаусу вместо наценки политики (с основанием) или снимает фиксацию."""
    from ..services import mirror_offers as mirror
    deal = get_deal_or_404(db, deal_id, user)
    if not user.has(Role.FD, Role.GD):
        raise HTTPException(403, "Трансфертную цену фиксируют ФД или ЗГД")
    o = db.get(FactoryOffer, offer_id)
    if o is None or o.deal_id != deal.id or not o.end_supplier_id:
        raise HTTPException(404, "Предложение компании группы не найдено")
    mirror.set_transfer_price(db, o, body.price, body.note, user.id)
    db.commit()
    return {"offer_id": o.id, "unit_price": o.unit_price, "currency": o.currency, **mirror.view(db, o, True)}



# ------------------------------------------------------------------ внутренняя цепочка сделки: правка и виза ФД/ЗГД (D-165, D-166)

class ChainIn(BaseModel):
    route_id: Optional[str] = None          # маршрут через компанию группы; None — напрямую от Берг-Хауса


@router.get("/chain")
def deal_chain(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Цепочка отобранных позиций и состояние визы; маршруты — для правки ФД/ЗГД."""
    from ..core.roles import READ, can
    from ..models import SupplyRoute
    from ..services import mirror_offers as mirror
    deal = get_deal_or_404(db, deal_id, user)
    out = mirror.chain_view(db, deal)
    out["can_edit"] = user.has(Role.FD, Role.GD)
    if out["can_edit"]:
        out["routes"] = [{"id": r.id, "name": r.name, "country": r.supplier_country} for r in db.scalars(select(SupplyRoute).where(SupplyRoute.is_active.is_(True)).order_by(SupplyRoute.name)).all()
                         if len(r.steps) >= 2]
    if not can(user, "intragroup_chain", READ):
        for r in out["rows"]:
            r.pop("transfer_price", None)
        out.pop("chain", None)
    return out


@router.post("/offers/{offer_id}/chain")
def set_offer_chain(deal_id: str, offer_id: str, body: ChainIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ФД/ЗГД меняет цепочку предложения до отбора: другой маршрут через компанию группы или напрямую."""
    from ..services import chain_rules
    from ..services import mirror_offers as mirror
    chain_rules.require_fd_gd(user)
    deal = get_deal_or_404(db, deal_id, user)
    o = db.get(FactoryOffer, offer_id)
    if o is None or o.deal_id != deal.id:
        raise HTTPException(404, "Предложение не найдено")
    res = mirror.set_chain(db, deal, o, body.route_id, user.id)
    db.commit()
    return {"offer_id": res.id if res else None, "via_group": bool(res and res.end_supplier_id)}


@router.post("/chain/approve")
def approve_chain(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Виза ФД/ЗГД на внутреннюю цепочку сделки — до КП клиенту. Меняется цепочка — нужна новая виза."""
    from ..services import chain_rules
    from ..services import mirror_offers as mirror
    chain_rules.require_fd_gd(user)
    deal = get_deal_or_404(db, deal_id, user)
    chain = mirror.deal_chain(db, deal)
    if not chain:
        raise HTTPException(409, "В сделке нет позиций через компанию группы — утверждать нечего")
    deal.chain_approval = chain_rules.approval_record(user, chain)
    audit(db, user.id, deal.id, "deal", deal.id, "chain_approved", after={"rows": len(chain)})
    db.add(FeedEntry(deal_id=deal.id, kind="system", subject=f"Внутренняя цепочка утверждена ({user.display_name})", body=f"позиций через компанию группы: {len(chain)}", user_id=user.id))
    db.commit()
    return mirror.chain_view(db, deal)
