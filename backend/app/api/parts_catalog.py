"""Запчасти, шаг 2: каталог запасных частей (карточка утверждена 05.11.2026) и перенос из МойСклад."""
from __future__ import annotations

import os
import tempfile
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..core.auth import CurrentUser, get_current_user
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import (
    Client, EquipmentModel, Factory, LegalEntity, Part, PartApplicability, PartClientPrice, PartCrossRef, PartStock, SupplyRoute, Warehouse,
)
from ..services.deal_service import audit
from ..services.suppliers import mark as _mark_supplier
from ..services.moysklad_import import apply_products, apply_stock, parse_products, parse_stock, read_rows
from .common import serialize

router = APIRouter(prefix="/api/parts", tags=["parts-catalog"])

CROSS_KINDS = {"supplier": "артикул поставщика", "analog_full": "аналог — полная замена", "analog_rework": "аналог — с доработкой"}
PART_STATUS = {"active": "активна", "archive": "архив"}
EDIT_ROLES = (Role.MANAGER, Role.CD, Role.GD, Role.PURCHASING, Role.ADMIN)   # карточки заводит менеджер (ТЗ, раздел 12)
IMPORT_ROLES = (Role.CD, Role.GD, Role.ADMIN)


def _editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*EDIT_ROLES):
        raise HTTPException(403, "Карточки запчастей ведут менеджеры, закупки, КД, ЗГД и администратор")
    return user


def _importer(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*IMPORT_ROLES):
        raise HTTPException(403, "Перенос из МойСклад запускают КД, ЗГД и администратор")
    return user


# ------------------------------------------------------------------ склады

class WarehouseIn(BaseModel):
    name: str = Field(min_length=1)
    legal_entity_id: Optional[str] = None
    kind: str = "physical"
    is_active: bool = True


@router.get("/warehouses")
def warehouses(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    names = {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}
    return [{**serialize(w), "legal_entity": names.get(w.legal_entity_id, "")} for w in db.scalars(select(Warehouse).order_by(Warehouse.name)).all()]


def _check_wh(body: WarehouseIn) -> None:
    if body.kind not in ("physical", "virtual"):
        raise HTTPException(422, "Склад: физический или виртуальный")


@router.post("/warehouses", status_code=201)
def create_warehouse(body: WarehouseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_importer)):
    _check_wh(body)
    if db.scalar(select(Warehouse).where(Warehouse.name == body.name.strip())):
        raise HTTPException(409, f"Склад «{body.name}» уже есть")
    w = Warehouse(**{**body.model_dump(), "name": body.name.strip()})
    db.add(w)
    db.commit()
    return serialize(w)


@router.put("/warehouses/{wid}")
def update_warehouse(wid: str, body: WarehouseIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_importer)):
    _check_wh(body)
    w = db.get(Warehouse, wid)
    if w is None:
        raise HTTPException(404, "Склад не найден")
    for k, v in body.model_dump().items():
        setattr(w, k, v)
    db.commit()
    return serialize(w)


# ------------------------------------------------------------------ каталог

def _stock_total(p: Part) -> float:
    return sum(s.qty or 0 for s in p.stock)


def _row(p: Part) -> dict:
    return {"id": p.id, "article": p.article, "name_ru": p.name_ru, "unit": p.unit, "part_group": p.part_group, "manufacturer": p.manufacturer,
            "oem_number": p.oem_number, "status": p.status, "stock_total": _stock_total(p), "sale_price": p.sale_price,
            "sale_currency": p.sale_currency, "has_client_prices": bool(p.prices)}


@router.get("/meta/catalog")
def catalog_meta(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    groups = [g for g in db.scalars(select(Part.part_group).where(Part.part_group != "").distinct().order_by(Part.part_group)).all()]
    return {"cross_kinds": CROSS_KINDS, "status": PART_STATUS, "groups": groups,
            "can_edit": user.has(*EDIT_ROLES), "can_import": user.has(*IMPORT_ROLES)}


@router.get("/catalog")
def catalog(q: str = "", group: str = "", status: str = "active", in_stock: bool = False, limit: int = 50, offset: int = 0,
            db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(Part).options(selectinload(Part.stock), selectinload(Part.prices))
    if q.strip():
        like = f"%{q.strip()}%"
        sub = select(PartCrossRef.part_id).where(PartCrossRef.number.ilike(like))
        stmt = stmt.where(or_(Part.article.ilike(like), Part.name_ru.ilike(like), Part.oem_number.ilike(like), Part.id.in_(sub)))
    if group:
        stmt = stmt.where(Part.part_group == group)
    if status in PART_STATUS:
        stmt = stmt.where(Part.status == status)
    if in_stock:
        stmt = stmt.where(Part.id.in_(select(PartStock.part_id).where(PartStock.qty > 0)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Part.name_ru, Part.article).limit(min(max(limit, 1), 200)).offset(max(offset, 0))).all()
    return {"total": total, "items": [_row(p) for p in rows]}


def duplicates(db: Session, article: str, oem: str = "", manufacturer: str = "", exclude_id: Optional[str] = None) -> list[dict]:
    """Похожие карточки: тот же артикул (у того же или неуказанного производителя), тот же OEM или кросс-номер."""
    conds = []
    a, o = (article or "").strip(), (oem or "").strip()
    if a:
        conds += [func.lower(Part.article) == a.lower(), func.lower(Part.oem_number) == a.lower()]
    if o:
        conds += [func.lower(Part.oem_number) == o.lower(), func.lower(Part.article) == o.lower()]
    if not conds:
        return []
    found = db.scalars(select(Part).where(or_(*conds))).all()
    nums = [x for x in (a, o) if x]
    for pid in db.scalars(select(PartCrossRef.part_id).where(func.lower(PartCrossRef.number).in_([x.lower() for x in nums]))).all():
        p = db.get(Part, pid)
        if p and p not in found:
            found.append(p)
    m = (manufacturer or "").strip().lower()
    out = []
    for p in found:
        if p.id == exclude_id:
            continue
        if m and p.manufacturer and p.manufacturer.strip().lower() != m and p.article.lower() == a.lower() and not o:
            continue   # тот же номер у другого производителя — не дубль
        out.append({"id": p.id, "article": p.article, "name_ru": p.name_ru, "manufacturer": p.manufacturer, "oem_number": p.oem_number})
    return out


@router.get("/catalog/check-duplicates")
def check_duplicates(article: str = "", oem: str = "", manufacturer: str = "", exclude_id: str = "",
                     db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return duplicates(db, article, oem, manufacturer, exclude_id or None)


def _card(db: Session, p: Part, user: CurrentUser) -> dict:
    d = serialize(p)
    d["cross_refs"] = [serialize(c) for c in p.cross_refs]
    d["applicability"] = []
    for a in p.applicability:
        row = serialize(a)
        if a.equipment_model_id:
            m = db.get(EquipmentModel, a.equipment_model_id)
            f = db.get(Factory, m.factory_id) if m else None
            row["model_title"] = f"{(f.short_name or f.name) if f else ''} {m.model if m else ''}".strip()
        d["applicability"].append(row)
    d["client_prices"] = [serialize(x) for x in p.prices]
    whs = {w.id: w for w in db.scalars(select(Warehouse)).all()}
    d["stock"] = [{**serialize(s), "warehouse": whs[s.warehouse_id].name if s.warehouse_id in whs else "",
                   "warehouse_kind": whs[s.warehouse_id].kind if s.warehouse_id in whs else "",
                   "available": (s.qty or 0) - (s.reserved or 0)} for s in p.stock]
    d["stock_total"] = _stock_total(p)
    sup = db.get(Factory, p.supplier_id) if p.supplier_id else None
    d["supplier_name"] = (sup.short_name or sup.name) if sup else ""
    r = db.get(SupplyRoute, p.route_id) if p.route_id else None
    d["route_name"] = r.name if r else ""
    if not user.can("factory_price_history"):          # цена поставщика — тем, кто видит цены завода
        for k in ("purchase_price", "purchase_currency", "purchase_on"):
            d.pop(k, None)
    return d


def _part_or_404(db: Session, pid: str) -> Part:
    p = db.get(Part, pid)
    if p is None:
        raise HTTPException(404, "Карточка не найдена")
    return p


@router.get("/catalog/{pid}")
def card(pid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return _card(db, _part_or_404(db, pid), user)


class CrossIn(BaseModel):
    kind: str
    number: str = Field(min_length=1)
    supplier_id: Optional[str] = None
    analog_part_id: Optional[str] = None
    manufacturer: str = ""
    comment: str = ""


class ApplicabilityIn(BaseModel):
    equipment_model_id: Optional[str] = None
    other_manufacturer: str = ""
    other_model: str = ""
    assembly: str = ""


class ClientPriceIn(BaseModel):
    client_id: Optional[str] = None
    client_name: str = ""
    price: float
    currency: str = "EUR"
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class StockIn(BaseModel):
    warehouse_id: str
    min_qty: Optional[float] = None


class PartIn(BaseModel):
    article: str = Field(min_length=1)
    name_ru: str = Field(min_length=1)
    unit: str = "шт"
    part_group: str = ""
    status: str = "active"
    names: dict = Field(default_factory=dict)
    description: str = ""
    manufacturer: str = ""
    oem_number: str = ""
    supplier_id: Optional[str] = None
    route_id: Optional[str] = None
    purchase_price: Optional[float] = None
    purchase_currency: str = ""
    purchase_on: Optional[date] = None
    lead_days: Optional[int] = None
    hs_code: str = ""
    origin_country: str = ""
    moq: Optional[float] = None
    multiple: Optional[float] = None
    weight_kg: Optional[float] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    volume_m3: Optional[float] = None
    sale_price: Optional[float] = None
    sale_currency: str = ""
    min_price: Optional[float] = None
    min_price_currency: str = ""
    photos: list[str] = Field(default_factory=list)
    spec_no: str = ""
    addendum_no: str = ""
    cross_refs: list[CrossIn] = Field(default_factory=list)
    applicability: list[ApplicabilityIn] = Field(default_factory=list)
    client_prices: list[ClientPriceIn] = Field(default_factory=list)
    min_stock: list[StockIn] = Field(default_factory=list)


def _validate(body: PartIn) -> None:
    if body.status not in PART_STATUS:
        raise HTTPException(422, "Статус карточки: активна или архив")
    if not body.unit.strip():
        raise HTTPException(422, "Укажите единицу измерения")
    for c in body.cross_refs:
        if c.kind not in CROSS_KINDS:
            raise HTTPException(422, f"Неизвестный вид номера «{c.kind}»")
    for a in body.applicability:
        if not a.equipment_model_id and not a.other_model.strip():
            raise HTTPException(422, "В применимости выберите модель из каталога или укажите модель другого производителя")
    for v in (body.moq, body.multiple, body.weight_kg, body.lead_days):
        if v is not None and v < 0:
            raise HTTPException(422, "Количество, вес и срок не могут быть отрицательными")


def _apply(db: Session, p: Part, body: PartIn, user: CurrentUser) -> None:
    data = body.model_dump(exclude={"cross_refs", "applicability", "client_prices", "min_stock"})
    if not user.can("factory_price_history"):          # кто не видит цену поставщика — не перезаписывает её
        for k in ("purchase_price", "purchase_currency", "purchase_on"):
            data.pop(k, None)
    if data.get("length_mm") and data.get("width_mm") and data.get("height_mm") and not data.get("volume_m3"):
        data["volume_m3"] = round(data["length_mm"] * data["width_mm"] * data["height_mm"] / 1e9, 6)
    for k, v in data.items():
        setattr(p, k, v.strip() if isinstance(v, str) and k in ("article", "name_ru", "oem_number", "manufacturer") else v)
    p.cross_refs = [PartCrossRef(**c.model_dump()) for c in body.cross_refs]
    p.applicability = [PartApplicability(**a.model_dump()) for a in body.applicability]
    names = {c.id: c.name for c in db.scalars(select(Client)).all()}
    p.prices = [PartClientPrice(**{**c.model_dump(), "client_name": names.get(c.client_id, c.client_name)}) for c in body.client_prices]
    keep = {s.warehouse_id: s for s in p.stock}
    for m in body.min_stock:
        st = keep.get(m.warehouse_id)
        if st is None:
            st = PartStock(warehouse_id=m.warehouse_id)
            p.stock.append(st)
        st.min_qty = m.min_qty


@router.post("/catalog", status_code=201)
def create_part(body: PartIn, force: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    _validate(body)
    dups = duplicates(db, body.article, body.oem_number, body.manufacturer)
    if dups and not force:
        raise HTTPException(409, {"message": "Похожие карточки уже есть. Проверьте их или сохраните новую карточку принудительно.", "duplicates": dups})
    p = Part(source="manual", created_by=user.id, article=body.article.strip(), name_ru=body.name_ru.strip())
    db.add(p)
    _apply(db, p, body, user)
    db.flush()
    _mark_supplier(db, p.supplier_id, "parts")
    audit(db, user.id, None, "part", p.id, "create", None, {"article": p.article, "name": p.name_ru, "forced": bool(dups)})
    db.commit()
    return _card(db, p, user)


@router.put("/catalog/{pid}")
def update_part(pid: str, body: PartIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_editor)):
    _validate(body)
    p = _part_or_404(db, pid)
    before = {"article": p.article, "name_ru": p.name_ru, "status": p.status}
    _apply(db, p, body, user)
    _mark_supplier(db, p.supplier_id, "parts")
    audit(db, user.id, None, "part", p.id, "update", before, {"article": p.article, "name_ru": p.name_ru, "status": p.status})
    db.commit()
    db.refresh(p)
    return _card(db, p, user)


# ------------------------------------------------------------------ перенос из МойСклад

def _save_upload(file: UploadFile) -> str:
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(422, "Нужен файл Excel (.xlsx) из МойСклад")
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as f:
        f.write(file.file.read())
    return path


@router.post("/import/moysklad-products")
def import_products(file: UploadFile = File(...), dry_run: bool = Form(True), db: Session = Depends(get_db), user: CurrentUser = Depends(_importer)):
    """Выгрузка «Товары (Excel)» из МойСклад. dry_run=true — только проверка и отчёт, без записи."""
    path = _save_upload(file)
    try:
        records, report = parse_products(read_rows(path))
    finally:
        os.unlink(path)
    if report.get("error"):
        raise HTTPException(422, report["error"])
    result = apply_products(db, records, user.id, dry_run=dry_run)
    if not dry_run:
        audit(db, user.id, None, "part_import", "moysklad-products", "import", None, {"file": file.filename, **result})
        db.commit()
    return {"report": report, "result": result}


@router.post("/import/stock")
def import_stock(file: UploadFile = File(...), default_warehouse: str = Form(""), db: Session = Depends(get_db),
                 user: CurrentUser = Depends(_importer)):
    """Отчёт МойСклад «Остатки по складам» (Excel) — снимок остатков на дату переноса."""
    path = _save_upload(file)
    try:
        records, report = parse_stock(read_rows(path))
    finally:
        os.unlink(path)
    if report.get("error"):
        raise HTTPException(422, report["error"])
    if not report["warehouses"] and not default_warehouse.strip():
        raise HTTPException(422, "В файле нет разбивки по складам — укажите склад, на который записать остатки")
    result = apply_stock(db, records, default_warehouse.strip() or None)
    audit(db, user.id, None, "part_import", "stock", "import", None, {"file": file.filename, **{k: v for k, v in result.items() if k != "missing_articles"}})
    db.commit()
    return {"report": report, "result": result}


# ------------------------------------------------------------------ фото из МойСклад (файлы изображений товаров → хранилище модуля)

@router.post("/import/moysklad-photos")
def import_ms_photos(user: CurrentUser = Depends(_importer)):
    """Запуск переноса фото в фоне: все товары МойСклад с изображениями, сопоставление по коду МойСклад (ms_id)."""
    from .. import adapters
    from ..adapters.moysklad import MoySkladClient, MoySkladError
    from ..core.config import get_settings
    from ..db import SessionLocal
    from ..services import moysklad_photos
    s = get_settings()
    try:
        client = MoySkladClient(s.moysklad_token, s.moysklad_api)
    except MoySkladError as e:
        raise HTTPException(422, str(e))
    if not moysklad_photos.start(SessionLocal, client, adapters.storage(), user.id):
        raise HTTPException(409, "Перенос фото уже идёт")
    return moysklad_photos.STATE


@router.get("/import/moysklad-photos")
def import_ms_photos_state(db: Session = Depends(get_db), user: CurrentUser = Depends(_importer)):
    from ..core.config import get_settings
    from ..models import IntegrationLog
    from ..services import moysklad_photos
    last = db.scalars(select(IntegrationLog).where(IntegrationLog.system == "moysklad", IntegrationLog.operation == "photos")
                      .order_by(IntegrationLog.at.desc())).first()
    return {**moysklad_photos.STATE, "token_set": bool(get_settings().moysklad_token),
            "last_run": {"at": last.at, "ok": last.ok, "details": last.details} if last else None}


@router.get("/catalog/{pid}/photo/{i}")
def part_photo(pid: str, i: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from .. import adapters
    p = _part_or_404(db, pid)
    files = p.photo_files or []
    if i < 0 or i >= len(files):
        raise HTTPException(404, "Фото не найдено")
    f = files[i]
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(f.get("name", ""))[1] or ".jpg")
    os.close(fd)
    adapters.storage().download(f["path"], path)
    r = file_response(path, f.get("name") or "photo.jpg", background=BackgroundTask(os.unlink, path))
    r.headers["Cache-Control"] = "private, max-age=86400"
    return r


@router.delete("/catalog/{pid}/photo/{i}")
def delete_part_photo(pid: str, i: int, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Убрать фото из карточки (файл в хранилище остаётся — удаление файлов выполняет владелец)."""
    if not user.has(Role.MANAGER, Role.CD, Role.GD, Role.ADMIN, Role.PURCHASING):
        raise HTTPException(403, "Карточки ведут менеджеры, закупки, КД и ЗГД")
    p = _part_or_404(db, pid)
    files = list(p.photo_files or [])
    if i < 0 or i >= len(files):
        raise HTTPException(404, "Фото не найдено")
    removed = files.pop(i)
    p.photo_files = files
    audit(db, user.id, None, "part", p.id, "photo_remove", removed, None)
    db.commit()
    return {"photo_files": files}
