"""Запчасти, шаг 2: каталог запасных частей (карточка утверждена 05.11.2026) и перенос из МойСклад."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
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
    Client, EquipmentExecution, EquipmentModel, Factory, User, LegalEntity, Part, PartApplicability, PartClientPrice, PartCrossRef, PartStock, SupplyRoute, Warehouse,
)
from ..services import applicability as app_svc
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


def _below_min(s: PartStock) -> bool:
    # то же правило, что в разделе «Снабжение и склад» (api/parts_stock.py): свободно = остаток − резерв < минимум
    return bool(s.min_qty and s.min_qty > 0 and (s.qty or 0) - (s.reserved or 0) < s.min_qty)


def _row(p: Part) -> dict:
    reserved = sum(s.reserved or 0 for s in p.stock)
    return {"id": p.id, "article": p.article, "name_ru": p.name_ru, "unit": p.unit, "part_group": p.part_group, "manufacturer": p.manufacturer,
            "oem_number": p.oem_number, "status": p.status, "stock_total": _stock_total(p), "sale_price": p.sale_price,
            "sale_currency": p.sale_currency, "has_client_prices": bool(p.prices),
            # D-170: сводка по складам для списка — только чтение, добавлена к прежним полям
            "stock_reserved": reserved, "stock_available": _stock_total(p) - reserved,
            "stock_in_transit": sum(s.in_transit or 0 for s in p.stock),
            "below_min_count": sum(1 for s in p.stock if _below_min(s))}


@router.get("/meta/catalog")
def catalog_meta(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    groups = [g for g in db.scalars(select(Part.part_group).where(Part.part_group != "").distinct().order_by(Part.part_group)).all()]
    return {"cross_kinds": CROSS_KINDS, "status": PART_STATUS, "groups": groups,
            "can_edit": user.has(*EDIT_ROLES), "can_import": user.has(*IMPORT_ROLES)}


@router.get("/catalog")
def catalog(q: str = "", group: str = "", status: str = "active", in_stock: bool = False, below_min: bool = False, limit: int = 50, offset: int = 0,
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
    if below_min:
        stmt = stmt.where(Part.id.in_(select(PartStock.part_id).where(
            PartStock.min_qty.is_not(None), PartStock.min_qty > 0,
            (func.coalesce(PartStock.qty, 0) - func.coalesce(PartStock.reserved, 0)) < PartStock.min_qty)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Part.name_ru, Part.article).limit(min(max(limit, 1), 200)).offset(max(offset, 0))).all()
    items = [_row(p) for p in rows]
    if _engineer_only(user):
        for it in items:
            for k in _ENGINEER_HIDDEN:
                it.pop(k, None)
    return {"total": total, "items": items}


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


@router.get("/catalog/by-model/{model_id}")
def parts_by_model(model_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Карточка модели ГШО в Каталоге: применимые запчасти (по узлам) и комплекты ТО."""
    from ..models import MaintenanceKit
    rows = db.scalars(select(PartApplicability).where(PartApplicability.equipment_model_id == model_id)).all()
    out = []
    for a in rows:
        p = db.get(Part, a.part_id)
        if p is not None and p.status == "active":
            out.append({"id": p.id, "article": p.article, "name_ru": p.name_ru, "part_group": p.part_group, "assembly": a.assembly, "unit": p.unit})
    out.sort(key=lambda x: (x["assembly"], x["name_ru"]))
    kits = db.scalars(select(MaintenanceKit).where(MaintenanceKit.equipment_model_id == model_id).order_by(MaintenanceKit.interval_hours)).all()
    return {"parts": out, "kits": [{"id": k.id, "name": k.name, "interval_hours": k.interval_hours, "lines": k.lines or [], "active": k.active} for k in kits]}


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
        row["status_title"] = app_svc.STATUSES.get(a.status, a.status)
        row["verified_by_name"] = (db.get(User, a.verified_by).display_name if a.verified_by and db.get(User, a.verified_by) else "")
        row["execution_name"] = (db.get(EquipmentExecution, a.execution_id).name if a.execution_id and db.get(EquipmentExecution, a.execution_id) else "")
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
    if _engineer_only(user):
        for k in _ENGINEER_HIDDEN:
            d.pop(k, None)
    return d


# D-181: инженеру сервиса нужны номера, применимость и остатки, но не цены. Остальным ролям — без изменений.
_ENGINEER_HIDDEN = ("sale_price", "sale_currency", "has_client_prices", "client_prices", "min_price", "min_price_currency")


def _engineer_only(user: CurrentUser) -> bool:
    return bool(user.roles) and all(r == Role.ENGINEER for r in user.roles)


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
    id: Optional[str] = None                  # D-196: существующая связь — по id (иначе — по модели и узлу)
    equipment_model_id: Optional[str] = None
    other_manufacturer: str = ""
    other_model: str = ""
    assembly: str = ""
    # J-b: не переданы (прежний вид, импорт) — у существующей связи не меняются, у новой — «не подтверждено»
    execution_id: Optional[str] = None
    status: str = "unverified"
    serial_from: str = Field(default="", max_length=64)
    serial_to: str = Field(default="", max_length=64)
    basis_kind: str = ""
    basis: str = ""


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


def _validate(body: PartIn, db: Optional[Session] = None) -> None:
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
        if db is not None:
            _check_app(db, a)
    for v in (body.moq, body.multiple, body.weight_kg, body.lead_days):
        if v is not None and v < 0:
            raise HTTPException(422, "Количество, вес и срок не могут быть отрицательными")


_APP_KEY = ("equipment_model_id", "other_manufacturer", "other_model", "assembly")
_APP_CHECK = ("execution_id", "status", "serial_from", "serial_to", "basis_kind", "basis")


def _check_app(db: Session, a: ApplicabilityIn) -> None:
    """Проверки J-b: статус и основание из списка, «подтверждено» и «не подходит» — только с основанием, исполнение — той же модели."""
    if a.status not in app_svc.STATUSES:
        raise HTTPException(422, "Статус применимости — из списка")
    if a.basis_kind and a.basis_kind not in app_svc.BASIS_KINDS:
        raise HTTPException(422, "Основание — из списка")
    if "status" in a.model_fields_set and a.status in app_svc.NEED_BASIS and not (a.basis.strip() or a.basis_kind):
        raise HTTPException(422, f"Для статуса «{app_svc.STATUSES[a.status]}» укажите основание")
    if a.execution_id:
        ex = db.get(EquipmentExecution, a.execution_id)
        if ex is None or ex.model_id != a.equipment_model_id:
            raise HTTPException(422, "Исполнение — той же модели, что в применимости")


def _merge_applicability(db: Session, p: Part, rows: list[ApplicabilityIn], user: CurrentUser) -> None:
    """Сохранить применимость, не теряя проверку: связь ищется по id, иначе по модели и узлу. Поля проверки, которых
    нет в запросе (прежний вид, импорт), остаются как были; изменение статуса или основания записывает, кто и когда."""
    old = {a.id: a for a in p.applicability}
    by_key = {tuple(getattr(a, k) or "" for k in _APP_KEY): a for a in p.applicability}
    out = []
    for r in rows:
        cur = old.get(r.id or "") or by_key.get(tuple(getattr(r, k) or "" for k in _APP_KEY))
        if cur is None or cur in out:
            cur = PartApplicability(status="unverified")
        for k in _APP_KEY:
            setattr(cur, k, getattr(r, k))
        before = {k: getattr(cur, k) for k in ("status", "basis_kind", "basis")}
        for k in _APP_CHECK:
            if k in r.model_fields_set:
                setattr(cur, k, getattr(r, k).strip() if isinstance(getattr(r, k), str) else getattr(r, k))
        if cur.status is None:
            cur.status = "unverified"
        if any(getattr(cur, k) != v for k, v in before.items()) and cur.status != "unverified":
            cur.verified_by, cur.verified_at = user.id, datetime.now(timezone.utc)
        out.append(cur)
    p.applicability = out


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
    _merge_applicability(db, p, body.applicability, user)
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
    _validate(body, db)
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
    _validate(body, db)
    p = _part_or_404(db, pid)
    before = _snapshot(db, p)
    _apply(db, p, body, user)
    db.flush()
    db.refresh(p)
    _mark_supplier(db, p.supplier_id, "parts")
    after = _snapshot(db, p)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    audit(db, user.id, None, "part", p.id, "update", {k: before.get(k) for k in changed} | {"article": before.get("article")},
          {k: after.get(k) for k in changed} | {"article": after.get("article")})
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


# ------------------------------------------------------------------ J-b (D-196): проверка применимости

def _app_row(db: Session, a: PartApplicability, p: Part, names: dict[str, str]) -> dict:
    m = db.get(EquipmentModel, a.equipment_model_id) if a.equipment_model_id else None
    f = db.get(Factory, m.factory_id) if m else None
    ex = db.get(EquipmentExecution, a.execution_id) if a.execution_id else None
    return {**serialize(a), "status_title": app_svc.STATUSES.get(a.status, a.status), "part_article": p.article, "part_name": p.name_ru,
            "model_title": (f"{(f.short_name or f.name) if f else ''} {m.model}".strip() if m else f"{a.other_manufacturer} {a.other_model}".strip()),
            "execution_name": ex.name if ex else "", "verified_by_name": names.get(a.verified_by or "", "")}


@router.get("/applicability")
def applicability_queue(status: str = "unverified", model_id: str = "", q: str = "", limit: int = 100, offset: int = 0,
                        db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Очередь проверки применимости: связи деталь — модель с отбором по статусу, модели и тексту; счётчики по статусам."""
    limit = max(1, min(limit, 500))
    base = select(PartApplicability, Part).join(Part, Part.id == PartApplicability.part_id).where(Part.status == "active")
    if model_id:
        base = base.where(PartApplicability.equipment_model_id == model_id)
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.where(or_(Part.article.ilike(like), Part.name_ru.ilike(like), PartApplicability.other_model.ilike(like),
                              PartApplicability.assembly.ilike(like)))
    counts = {k: 0 for k in app_svc.STATUSES}
    sq = base.subquery()
    for st, n in db.execute(select(sq.c.status, func.count()).group_by(sq.c.status)).all():
        counts[st] = n
    if status:
        base = base.where(PartApplicability.status == status)
    rows = db.execute(base.order_by(Part.article, PartApplicability.assembly).offset(offset).limit(limit)).all()
    ids = {a.verified_by for a, _ in rows if a.verified_by}
    names = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}
    return {"items": [_app_row(db, a, p, names) for a, p in rows], "counts": counts, "statuses": app_svc.STATUSES,
            "basis_kinds": app_svc.BASIS_KINDS, "need_basis": sorted(app_svc.NEED_BASIS), "can_edit": user.has(*VERIFY_ROLES)}


class VerifyIn(BaseModel):
    status: str
    basis_kind: str = ""
    basis: str = Field(default="", max_length=5000)
    serial_from: Optional[str] = Field(default=None, max_length=64)
    serial_to: Optional[str] = Field(default=None, max_length=64)
    execution_id: Optional[str] = None


VERIFY_ROLES = (*EDIT_ROLES, Role.SERVICE_HEAD)   # D-197: руководитель сервиса утверждает применимость (решение владельца 27.11)


def _verifier(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*VERIFY_ROLES):
        raise HTTPException(403, "Применимость проверяют менеджеры, закупки, КД, ЗГД, руководитель сервиса и администратор")
    return user


@router.post("/applicability/{aid}/verify")
def applicability_verify(aid: str, body: VerifyIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_verifier)):
    """Отметить проверку связи: статус и основание (для «подтверждено», «не подходит», «заменена» — обязательно)."""
    a = db.get(PartApplicability, aid)
    if a is None:
        raise HTTPException(404, "Связь не найдена")
    chk = ApplicabilityIn(equipment_model_id=a.equipment_model_id, other_model=a.other_model or "x", status=body.status,
                          basis_kind=body.basis_kind, basis=body.basis, execution_id=body.execution_id if body.execution_id is not None else a.execution_id)
    _check_app(db, chk)
    before = {k: getattr(a, k) for k in _APP_CHECK}
    a.status, a.basis_kind, a.basis = body.status, body.basis_kind, body.basis.strip()
    for k in ("serial_from", "serial_to", "execution_id"):
        v = getattr(body, k)
        if v is not None:
            setattr(a, k, (v.strip() if isinstance(v, str) else v) or ("" if k != "execution_id" else None))
    a.verified_by, a.verified_at = user.id, datetime.now(timezone.utc)
    audit(db, user.id, None, "part_applicability", a.id, "verify", before=before, after={k: getattr(a, k) for k in _APP_CHECK})
    db.commit()
    p = db.get(Part, a.part_id)
    return _app_row(db, a, p, {user.id: user.display_name})



# ------------------------------------------------------------------ J-g (D-199): история карточки детали

_SNAP_FIELDS = ("article", "name_ru", "unit", "part_group", "status", "description", "manufacturer", "oem_number", "supplier_id", "route_id",
                "purchase_price", "purchase_currency", "purchase_on", "lead_days", "hs_code", "origin_country", "moq", "multiple", "weight_kg",
                "length_mm", "width_mm", "height_mm", "volume_m3", "sale_price", "sale_currency", "min_price", "min_price_currency",
                "spec_no", "addendum_no")
FIELD_TITLES = {
    "article": "Артикул", "name_ru": "Наименование", "unit": "Единица", "part_group": "Группа", "status": "Статус", "description": "Описание",
    "manufacturer": "Производитель", "oem_number": "OEM", "supplier_id": "Поставщик", "route_id": "Маршрут", "purchase_price": "Закупочная цена",
    "purchase_currency": "Валюта закупки", "purchase_on": "Дата закупочной цены", "lead_days": "Срок поставки, дн", "hs_code": "Код ТН ВЭД",
    "origin_country": "Страна происхождения", "moq": "Минимальная партия", "multiple": "Кратность", "weight_kg": "Вес, кг", "length_mm": "Длина, мм",
    "width_mm": "Ширина, мм", "height_mm": "Высота, мм", "volume_m3": "Объём, м³", "sale_price": "Цена продажи", "sale_currency": "Валюта продажи",
    "min_price": "Минимальная цена", "min_price_currency": "Валюта мин. цены", "spec_no": "Спецификация", "addendum_no": "Дополнение",
    "names": "Наименования на других языках", "cross_refs": "Номера и аналоги", "applicability": "Применимость", "client_prices": "Договорные цены",
    "min_stock": "Минимальные остатки", "photos": "Фото", "name": "Наименование",
}
_PURCHASE = {"purchase_price", "purchase_currency", "purchase_on"}
_SALE = set(_ENGINEER_HIDDEN) | {"client_prices"}


def _snapshot(db: Session, p: Part) -> dict:
    """Карточка детали в сравнимом виде — для журнала изменений (что было / стало по полям)."""
    d = {k: (getattr(p, k).isoformat() if isinstance(getattr(p, k), date) else getattr(p, k)) for k in _SNAP_FIELDS}
    d["names"] = {k: v for k, v in sorted((p.names or {}).items()) if v}
    d["cross_refs"] = sorted(f"{CROSS_KINDS.get(c.kind, c.kind)}: {c.number}" + (f" ({c.manufacturer})" if c.manufacturer else "") for c in p.cross_refs)
    apps = []
    for a in p.applicability:
        if a.equipment_model_id:
            m = db.get(EquipmentModel, a.equipment_model_id)
            title = m.model if m else "модель каталога"
        else:
            title = f"{a.other_manufacturer} {a.other_model}".strip()
        apps.append(f"{title}{' / ' + a.assembly if a.assembly else ''} [{app_svc.STATUSES.get(a.status, a.status)}]")
    d["applicability"] = sorted(apps)
    names = {c.id: c.name for c in db.scalars(select(Client)).all()} if p.prices else {}
    d["client_prices"] = sorted(f"{names.get(x.client_id, x.client_name)}: {x.price:g} {x.currency}" for x in p.prices)
    whs = {w.id: w.name for w in db.scalars(select(Warehouse)).all()} if p.stock else {}
    d["min_stock"] = sorted(f"{whs.get(x.warehouse_id, '?')}: {x.min_qty:g}" for x in p.stock if x.min_qty is not None)
    d["photos"] = len(p.photos or [])
    return d


@router.get("/catalog/{pid}/history")
def part_history(pid: str, limit: int = 100, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """История карточки детали (только чтение): кто, когда и что изменил — по полям «было / стало», включая проверку
    применимости. Цены — с теми же правами, что в карточке (закупка — кто видит цены завода; продажа — не инженеру)."""
    from ..models import AuditLog
    p = _part_or_404(db, pid)
    app_ids = [a.id for a in p.applicability]
    st = select(AuditLog).where(((AuditLog.entity == "part") & (AuditLog.entity_id == pid)) |
                                ((AuditLog.entity == "part_applicability") & (AuditLog.entity_id.in_(app_ids or ["-"]))))
    rows = db.scalars(st.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(max(1, min(limit, 500)))).all()
    hide = set()
    if not user.can("factory_price_history"):
        hide |= _PURCHASE
    if _engineer_only(user):
        hide |= _SALE
    uids = {r.user_id for r in rows if r.user_id}
    who = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
    out = []
    for r in rows:
        b, a = r.before or {}, r.after or {}
        changes = []
        if r.entity == "part_applicability":
            title = "Проверка применимости"
            for k in ("status", "basis_kind", "basis", "serial_from", "serial_to"):
                if b.get(k) != a.get(k):
                    fmt = (lambda v: app_svc.STATUSES.get(v, v)) if k == "status" else (lambda v: app_svc.BASIS_KINDS.get(v, v) if k == "basis_kind" else v)
                    changes.append({"field": k, "title": {"status": "Статус", "basis_kind": "Основание", "basis": "Основание (текст)",
                                                          "serial_from": "Зав. № с", "serial_to": "Зав. № по"}[k], "before": fmt(b.get(k)), "after": fmt(a.get(k))})
        else:
            title = {"create": "Карточка создана", "update": "Изменения", "photo_remove": "Удалено фото"}.get(r.action, r.action)
            keys = [k for k in (list(a) if r.action == "create" else sorted(set(a) | set(b), key=lambda k: list(FIELD_TITLES).index(k) if k in FIELD_TITLES else 999))]
            for k in keys:
                if k in hide or k in ("forced",) or (r.action == "update" and k == "article" and b.get(k) == a.get(k)):
                    continue
                if r.action == "create":
                    if k in ("article", "name"):
                        changes.append({"field": k, "title": FIELD_TITLES.get(k, k), "before": None, "after": a.get(k)})
                    continue
                if b.get(k) != a.get(k):
                    changes.append({"field": k, "title": FIELD_TITLES.get(k, k), "before": b.get(k), "after": a.get(k)})
        if r.action == "update" and not changes and r.entity == "part":
            continue                                    # сохранили без изменений
        out.append({"id": r.id, "at": r.at.isoformat() if r.at else None, "user_name": who.get(r.user_id or "", ""), "action": r.action,
                    "title": title, "changes": changes})
    return {"items": out}
