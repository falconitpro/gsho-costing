"""Блок менеджера: сделки, позиции, варианты, импорт RFQ, отправка на расчёт, лента, версии."""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..adapters.storage import sha256_file
from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.roles import Role, filter_payload
from ..core.workflow import DealState, DealStatus, close as wf_close, kp_sent as wf_kp_sent, manager_checks, send_to_calc
from ..db import get_db
from ..models import Attachment, Client, Deal, DealVersion, EquipmentModel, Factory, FactoryPriceHistory, FeedEntry, Position, RfqImport, Variant
from ..services import settings_service
from ..services.deal_service import apply_transition, audit, complete_tasks, ensure_folders, next_number, sla
from ..services.rfq_import import parse_rfq, price_reference
from .common import deal_payload, get_deal_or_404, payment_terms_set, serialize

router = APIRouter(prefix="/api/deals", tags=["deals"])


class DealCreate(BaseModel):
    client_id: str
    contact: str = ""
    site: str = ""
    region: str = ""
    requested_at: date = Field(default_factory=date.today)
    kp_due: Optional[date] = None
    priority: str = "normal"
    base_currency: str = "CNY"
    service_flag: bool = False
    target_hours: Optional[float] = None
    client_payment_wishes: str = ""          # задача 1: пожелания клиента по оплате (условия задаёт КД)
    has_alternatives: bool = False           # задача 5: альтернативные предложения в одном КП


class DealUpdate(BaseModel):
    contact: Optional[str] = None
    site: Optional[str] = None
    region: Optional[str] = None
    kp_due: Optional[date] = None
    priority: Optional[str] = None
    operating_conditions: Optional[dict] = None
    service_flag: Optional[bool] = None
    target_hours: Optional[float] = None
    client_payments: Optional[list[dict]] = None       # только КД/ГД (задача 1)
    bank_guarantee: Optional[dict] = None              # только КД/ГД
    base_currency: Optional[str] = None
    client_payment_wishes: Optional[str] = None
    has_alternatives: Optional[bool] = None
    keep_variant_id: Optional[str] = None             # задача 5: какое предложение оставить при выключении альтернатив


class PositionIn(BaseModel):
    factory_id: str
    trademark: str = ""                                    # торговая марка; пусто — по сопоставлению «завод → марки»
    model: str
    machine_type: str = ""
    qty: int = 1
    unit_price: float
    currency: str = "CNY"
    options: list[dict] = Field(default_factory=list)
    options_price: float = 0.0
    basis: str = "FCA"
    delivery_point: str = ""
    production_days: int = 90
    delivery_days: int = 10
    production_term: dict = Field(default_factory=dict)    # задача 3: {value, unit, fixed_date, start_event}
    delivery_term: dict = Field(default_factory=dict)      # задача 3: {unit, start_event}
    warranty_months: Optional[int] = None
    warranty_hours: Optional[float] = None
    zip_price: Optional[float] = None
    factory_payments: list[dict] = Field(default_factory=list)   # менеджер: условия завода как справка (factory_payments_offer); график задаёт КД
    tech_params: dict = Field(default_factory=dict)        # ручной ввод техданных (коды RFQ)
    logistics_params: dict = Field(default_factory=dict)   # поля с меткой L
    execution_id: Optional[str] = None                     # задача 7: исполнение из каталога
    price_comment: str = ""


def _production_days(term: dict, fallback: int) -> int:
    """Срок производства в календарных днях (производное поле для совместимости и справок)."""
    unit, value = term.get("unit"), term.get("value")
    if unit == "date" and term.get("fixed_date"):
        return fallback
    if not value:
        return fallback
    if unit == "weeks":
        return int(round(float(value) * 7))
    if unit == "working_days":
        return int(round(float(value) * 7 / 5))
    return int(round(float(value)))


class VariantIn(BaseModel):
    name: str
    position_ids: list[str]
    client_payments: list[dict] = Field(default_factory=list)
    service_in_kp: bool = False


@router.get("")
def list_deals(status: Optional[str] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    q = select(Deal).order_by(Deal.created_at.desc())
    if status:
        q = q.where(Deal.status == DealStatus(status))
    deals = [d for d in db.scalars(q).all() if _visible(user, d)]
    out = []
    numbers = {d.id: d.number for d in deals}
    children: dict[str, int] = {}
    for d in deals:
        if d.parent_deal_id:
            children[d.parent_deal_id] = children.get(d.parent_deal_id, 0) + 1
    for d in deals:
        row = serialize(d, exclude={"client_payments", "bank_guarantee", "operating_conditions", "scenario_override"})
        row["client_name"] = d.client.name if d.client else None
        # задача 9: группировка связанных заявок в списке
        row["group"] = d.parent_deal_id or d.id
        row["parent_number"] = numbers.get(d.parent_deal_id, "") if d.parent_deal_id else ""
        row["related_count"] = children.get(d.parent_deal_id or d.id, 0)   # у исходной — число дублей; у дубля — исходная + другие дубли
        out.append(row)
    return filter_payload(out, user)


def _visible(user: CurrentUser, d: Deal) -> bool:
    from .common import can_see_deal
    return can_see_deal(user, d)


@router.post("", status_code=201)
def create_deal(body: DealCreate, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.MANAGER, Role.GD))):
    if db.get(Client, body.client_id) is None:
        raise HTTPException(400, "Клиент не найден")
    deal = Deal(number=next_number(db, body.requested_at.year), manager_id=user.id, **body.model_dump())
    db.add(deal)
    db.flush()
    audit(db, user.id, deal.id, "deal", deal.id, "create", after=body.model_dump(mode="json"))
    db.commit()
    db.refresh(deal)
    return deal_payload(deal, user)


@router.get("/{deal_id}")
def get_deal(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    extra: dict[str, Any] = {"checks": manager_checks(_deal_dict(deal))}
    return deal_payload(deal, user, extra)


def _deal_dict(deal: Deal) -> dict:
    return {"client_id": deal.client_id,
            "positions": [{**serialize(p), "factory": p.factory_id, "tech_param_rows": [serialize(r) for r in (p.tech_param_rows or [])],
                           "local_options": [serialize(o) for o in (p.local_option_rows or [])]} for p in deal.positions],
            "client_payments": deal.client_payments, "variants": [serialize(v) for v in deal.variants], "has_alternatives": deal.has_alternatives,
            "service_flag": deal.service_flag, "target_hours": deal.target_hours,
            "payment_terms_set": payment_terms_set(deal)}


def ensure_default_variant(db: Session, deal: Deal) -> None:
    """Задача 5: без альтернатив система ведёт один служебный вариант со всеми позициями (не показывается как выбор)."""
    default = next((v for v in deal.variants if v.is_default), None)
    if deal.has_alternatives:
        return
    if default is None:
        default = Variant(deal_id=deal.id, name="Предложение", position_ids=[p.id for p in deal.positions], is_default=True)
        db.add(default)
        db.flush()
        deal.variants.append(default)
    else:
        default.position_ids = [p.id for p in deal.positions]


def _editable_by_manager(deal: Deal, user: CurrentUser) -> None:
    if user.has(Role.GD):
        return
    if not user.has(Role.MANAGER) or deal.manager_id != user.id:
        raise HTTPException(403, "Редактировать исходные данные может только менеджер сделки")
    if deal.status not in (DealStatus.DRAFT, DealStatus.REWORK, DealStatus.APPROVED, DealStatus.KP_SENT):
        raise HTTPException(409, "Сделка на расчёте — верните её на доработку для изменений")


@router.patch("/{deal_id}")
def update_deal(deal_id: str, body: DealUpdate, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    data = body.model_dump(exclude_none=True)
    if ("client_payments" in data or "bank_guarantee" in data):
        if not user.can("payment_terms", 2):
            raise HTTPException(403, "Условия оплаты и БГ задаёт коммерческий директор")
    else:
        _editable_by_manager(deal, user)
    keep_id = data.pop("keep_variant_id", None)
    if data.get("has_alternatives") is False and deal.has_alternatives:
        offers = [v for v in deal.variants if not v.is_default]
        if len(offers) > 1 and not any(v.id == keep_id for v in offers):
            # задача 5: предупреждение и выбор, какое предложение остаётся основным (остальные будут удалены)
            raise HTTPException(409, {"message": "Выключение альтернатив удалит предложения. Выберите, какое оставить основным.",
                                      "choose_variant": [{"id": v.id, "name": v.name, "price_rub": v.price_rub, "positions": len(v.position_ids or [])}
                                                         for v in offers]})
    before = serialize(deal)
    for k, v in data.items():
        setattr(deal, k, v)
    if "has_alternatives" in data:
        if deal.has_alternatives:
            for v in deal.variants:
                if v.is_default:
                    v.is_default, v.name = False, v.name if v.name != "Предложение" else "Предложение А"
        else:
            # выключение альтернатив: оставляем основным первое предложение с ценой (или первое), остальные удаляем
            keep = next((v for v in deal.variants if v.id == keep_id), None) \
                or next((v for v in deal.variants if v.price_rub is not None), deal.variants[0] if deal.variants else None)
            for v in list(deal.variants):
                if v is not keep:
                    db.delete(v)
            if keep:
                keep.is_default, keep.position_ids = True, [p.id for p in deal.positions]
            db.flush()
            db.refresh(deal)
            ensure_default_variant(db, deal)
    audit(db, user.id, deal.id, "deal", deal.id, "update", {k: before.get(k) for k in data}, body.model_dump(exclude_none=True, mode="json"))
    db.commit()
    db.refresh(deal)
    return deal_payload(deal, user)


# ------------------------------------------------------------------ позиции и варианты

@router.post("/{deal_id}/positions", status_code=201)
def add_position(deal_id: str, body: PositionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    if db.get(Factory, body.factory_id) is None:
        raise HTTPException(400, "Завод не найден")
    data = body.model_dump()
    comment = data.pop("price_comment")
    # задача 1: график оплаты заводу задаёт КД; введённое менеджером — справка «условия завода»
    data["factory_payments_offer"] = data.pop("factory_payments") or []
    if user.can("payment_terms", 2):
        data["factory_payments"] = data["factory_payments_offer"]
    data["production_days"] = _production_days(data.get("production_term") or {}, data.get("production_days") or 90)
    from ..services.catalog_service import resolve_trademark
    data["trademark"] = resolve_trademark(db, body.factory_id, body.model, body.trademark)
    pos = Position(deal_id=deal.id, **data)
    pos.tech_params = {**pos.tech_params, "_source_file": "ручной ввод"}
    pos.price_reference = _price_ref(db, pos, comment)
    if pos.price_reference.get("needs_comment") and not comment:
        raise HTTPException(422, {"message": "Отклонение цены от истории выше порога — нужен комментарий", "price_reference": pos.price_reference})
    db.add(pos)
    db.flush()
    _record_price(db, pos, deal)
    from ..services.tech_params_service import init_position_rows
    init_position_rows(db, pos, user.id)   # задача 4/7: перечень технических характеристик из исполнения каталога или шаблона по типу
    ensure_default_variant(db, deal)
    audit(db, user.id, deal.id, "position", pos.id, "create", after=data)
    db.commit()
    return serialize(pos)


def _price_ref(db: Session, pos: Position, comment: str) -> dict:
    hist = [{"unit_price": h.unit_price, "currency": h.currency, "on": h.on}
            for h in db.scalars(select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == pos.factory_id,
                                                                    FactoryPriceHistory.model == pos.model)).all()]
    ref = price_reference(pos.unit_price, pos.currency, hist, settings_service.get(db, "price_deviation_pct"))
    ref["comment"] = comment
    return ref


def _record_price(db: Session, pos: Position, deal: Deal, source: str = "rfq") -> None:
    db.add(FactoryPriceHistory(factory_id=pos.factory_id, model=pos.model, execution_id=pos.execution_id, unit_price=pos.unit_price,
                               currency=pos.currency, basis=pos.basis, production_term=pos.production_term or {"value": pos.production_days, "unit": "calendar_days"},
                               factory_payments=pos.factory_payments_offer or pos.factory_payments or [], on=date.today(), deal_id=deal.id, source=source))


@router.patch("/{deal_id}/positions/{pos_id}")
def update_position(deal_id: str, pos_id: str, body: dict, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    pos = db.get(Position, pos_id)
    if pos is None or pos.deal_id != deal.id:
        raise HTTPException(404)
    allowed = set(PositionIn.model_fields) - {"price_comment", "tech_params", "logistics_params", "factory_payments"}
    before = serialize(pos)
    for k, v in body.items():
        if k in allowed:
            setattr(pos, k, v)
    if "trademark" in body or "factory_id" in body:   # смена завода — марка по сопоставлению, если не указана явно
        from ..services.catalog_service import resolve_trademark
        pos.trademark = resolve_trademark(db, pos.factory_id, pos.model, body.get("trademark") or "")
    if "factory_payments" in body:
        pos.factory_payments_offer = body["factory_payments"] or []
        if user.can("payment_terms", 2):
            pos.factory_payments = pos.factory_payments_offer
    if "production_term" in body:
        pos.production_days = _production_days(pos.production_term or {}, body.get("production_days") or pos.production_days or 90)
    for key in ("tech_params", "logistics_params"):
        if isinstance(body.get(key), dict):
            setattr(pos, key, {**(getattr(pos, key) or {}), **body[key]})
    if "machine_type" in body or "model" in body or "tech_params" in body:   # карточка позиции → строки 1.1 / 1.2 / 1.3 таблицы
        from ..services.tech_params_service import rows_follow_position
        rows_follow_position(pos)
    if "unit_price" in body:
        pos.price_reference = _price_ref(db, pos, body.get("price_comment", ""))
        _record_price(db, pos, deal)
    if "execution_id" in body and body["execution_id"]:
        from ..services.tech_params_service import init_position_rows
        init_position_rows(db, pos, user.id, replace=True)
    audit(db, user.id, deal.id, "position", pos.id, "update", {k: before.get(k) for k in body}, body)
    db.commit()
    return serialize(pos)


@router.delete("/{deal_id}/positions/{pos_id}", status_code=204)
def delete_position(deal_id: str, pos_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    pos = db.get(Position, pos_id)
    if pos and pos.deal_id == deal.id:
        audit(db, user.id, deal.id, "position", pos.id, "delete", before=serialize(pos))
        db.delete(pos)
        db.flush()
        for v in deal.variants:
            v.position_ids = [x for x in (v.position_ids or []) if x != pos_id]
        db.commit()


@router.post("/{deal_id}/variants", status_code=201)
def add_variant(deal_id: str, body: VariantIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    if not deal.has_alternatives:
        raise HTTPException(409, "Предложения добавляются только при включённой отметке «Альтернативные предложения»")
    data = body.model_dump()
    if data.get("client_payments") and not user.can("payment_terms", 2):
        data["client_payments"] = []
    v = Variant(deal_id=deal.id, **data)
    db.add(v)
    db.flush()
    audit(db, user.id, deal.id, "variant", v.id, "create", after=body.model_dump())
    db.commit()
    return serialize(v)


@router.delete("/{deal_id}/variants/{var_id}", status_code=204)
def delete_variant(deal_id: str, var_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    v = db.get(Variant, var_id)
    if v and v.deal_id == deal.id:
        db.delete(v)
        db.commit()


# ------------------------------------------------------------------ импорт RFQ

@router.get("/rfq/template")
def rfq_template_schema(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Все поля опросника из актуального шаблона RFQ (разделы, коды, названия RU/ZH, единицы, метка L) — для ручного ввода."""
    from ..models import Template
    from ..services.rfq_import import template_fields
    from ..services.rfq_template import template_path
    custom = db.scalar(select(Template).where(Template.kind == "rfq", Template.is_active.is_(True)).order_by(Template.version.desc()))
    return template_fields(template_path(custom.file_path if custom else None))


@router.get("/rfq/template/file")
def rfq_template_file(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Пустой шаблон RFQ для отправки заводу."""
    from fastapi.responses import FileResponse
    from ..models import Template
    from ..services.rfq_template import template_path
    custom = db.scalar(select(Template).where(Template.kind == "rfq", Template.is_active.is_(True)).order_by(Template.version.desc()))
    return FileResponse(template_path(custom.file_path if custom else None), filename="RFQ_RU-ZH.xlsx")


@router.post("/{deal_id}/rfq/preview")
async def rfq_preview(deal_id: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Предпросмотр распознанных полей с подсветкой пустых обязательных (ничего не сохраняет)."""
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        path = tmp.name
    try:
        parsed = parse_rfq(path)
    finally:
        os.unlink(path)
    return parsed.to_dict()


@router.post("/{deal_id}/rfq/import", status_code=201)
async def rfq_import(deal_id: str, file: UploadFile = File(...), price_comment: str = Form(""), db: Session = Depends(get_db),
                     user: CurrentUser = Depends(get_current_user)):
    """Разбирает файл, создаёт позицию, пополняет каталог и историю цен, кладёт файл в 01 Запрос и RFQ."""
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, file.filename or "rfq.xlsx")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    parsed = parse_rfq(path)
    attempt = (db.scalar(select(RfqImport.attempt).where(RfqImport.deal_id == deal.id).order_by(RfqImport.attempt.desc())) or 0) + 1
    imp = RfqImport(deal_id=deal.id, file_path=path, file_hash=sha256_file(path), template_version=parsed.template_version,
                    attempt=attempt, parsed=parsed.to_dict(), errors=[e.__dict__ for e in parsed.errors], imported_by=user.id)
    db.add(imp)
    db.flush()   # id попытки нужен позиции (rfq_import_id) — источник «RFQ» в списке позиций
    if not parsed.ok:
        db.commit()
        raise HTTPException(422, {"message": "Ошибки импорта — исправьте файл и загрузите повторно", "errors": imp.errors,
                                  "missing_required": parsed.missing_required})
    # завод
    fname = parsed.header.get("factory_name")
    factory = db.scalar(select(Factory).where(Factory.name == fname))
    if factory is None:
        factory = Factory(name=fname, address=parsed.header.get("factory_address") or "")
        db.add(factory)
        db.flush()
    imp.factory_id = factory.id
    from ..services.catalog_service import resolve_trademark
    pos_data = parsed.position
    pos = Position(
        deal_id=deal.id, factory_id=factory.id, machine_type=pos_data.get("machine_type") or "", model=pos_data["model"],
        qty=int(pos_data.get("qty") or 1), unit_price=float(pos_data["unit_price"]), currency=pos_data.get("currency") or "CNY",
        options=parsed.options, options_price=pos_data.get("options_price") or 0.0, basis=pos_data.get("basis") or "FCA",
        delivery_point=pos_data.get("delivery_point") or "", production_days=int(pos_data.get("production_days") or 90),
        delivery_days=int(pos_data.get("delivery_days") or 10), warranty_months=pos_data.get("warranty_months"),
        warranty_hours=pos_data.get("warranty_hours"), zip_price=pos_data.get("zip_price"),
        tech_params={**parsed.tech_params, "_source_file": file.filename, "_rfq": {"title": parsed.title, "version": parsed.template_version,
                     "sections": parsed.sections, "filled": parsed.filled_count, "total": len(parsed.fields),
                     "fields": {c: f.as_dict() for c, f in parsed.fields.items()}}},
        logistics_params=parsed.logistics_params, rfq_import_id=imp.id,
        trademark=resolve_trademark(db, factory.id, pos_data["model"], str(pos_data.get("trademark") or "")),
        service_calc={"spare_parts": parsed.spare_parts, "zip_target_hours": parsed.zip_target_hours, "zip_currency": parsed.zip_currency},
    )
    pos.price_reference = _price_ref(db, pos, price_comment)
    if pos.price_reference.get("needs_comment") and not price_comment:
        db.commit()
        raise HTTPException(422, {"message": "Отклонение цены завода от истории выше порога — укажите комментарий (price_comment)",
                                  "price_reference": pos.price_reference, "preview": parsed.to_dict()})
    pos.production_term = {"value": pos.production_days, "unit": "calendar_days", "start_event": "factory_contract"}
    db.add(pos)
    db.flush()
    _record_price(db, pos, deal)
    from ..services.tech_params_service import rows_from_rfq
    rows_from_rfq(db, pos, parsed, user.id)   # задача 4: таблица технических характеристик из ответа завода в шаблоне RFQ
    ensure_default_variant(db, deal)
    # шапка сделки из раздела 0 опросника (заполняем только пустое, менеджер мог ввести раньше)
    deal.operating_conditions = {**(deal.operating_conditions or {}), **{k: parsed.header.get(k) for k in ("temp_min", "temp_max", "operating_conditions") if parsed.header.get(k) is not None}}
    if not deal.kp_due and parsed.header.get("kp_due"):
        try:
            deal.kp_due = date.fromisoformat(str(parsed.header["kp_due"])[:10])
        except ValueError:
            pass
    if parsed.header.get("priority") and deal.priority == "normal":
        deal.priority = str(parsed.header["priority"])
    if deal.service_flag and not deal.target_hours and parsed.zip_target_hours:
        deal.target_hours = parsed.zip_target_hours
    # каталог оборудования: новая модель или различия с каталогом
    cat = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == factory.id, EquipmentModel.model == pos.model))
    catalog_diff = {}
    if cat is None:
        db.add(EquipmentModel(factory_id=factory.id, machine_type=pos.machine_type, model=pos.model, trademark=pos.trademark, tech_params=parsed.tech_params,
                              logistics_params=parsed.logistics_params))
    else:
        for code, f in {**parsed.tech_params, **parsed.logistics_params}.items():
            old = (cat.tech_params.get(code) or cat.logistics_params.get(code) or {}).get("value")
            if old not in (None, f.get("value")):
                catalog_diff[code] = {"name": f.get("name"), "catalog": old, "rfq": f.get("value")}
    # файл в хранилище
    try:
        ensure_folders(db, deal)
        stored = adapters.storage().upload(path, deal.storage_folder, "01_rfq", f"RFQ_{factory.name[:30]}_v{attempt}.xlsx")
        db.add(Attachment(deal_id=deal.id, folder="01_rfq", entity="position", entity_id=pos.id, file_name=file.filename or "rfq.xlsx",
                          storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id))
        imp.file_path = stored.path
    except Exception as e:
        audit(db, user.id, deal.id, "rfq", imp.id, "storage_failed", comment=str(e)[:300])
    audit(db, user.id, deal.id, "position", pos.id, "import_rfq", after={"file": file.filename, "attempt": attempt})
    db.commit()
    return {"position": serialize(pos), "price_reference": pos.price_reference, "catalog_diff": catalog_diff,
            "warnings": [e for e in imp.errors if e["severity"] == "warning"], "template_version": parsed.template_version}


@router.post("/{deal_id}/positions/{pos_id}/catalog-update")
def catalog_update(deal_id: str, pos_id: str, codes: list[str], db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Менеджер выбрал: обновить каталог значениями из RFQ по указанным кодам (иначе значения остаются только в сделке)."""
    deal = get_deal_or_404(db, deal_id, user)
    pos = db.get(Position, pos_id)
    cat = db.scalar(select(EquipmentModel).where(EquipmentModel.factory_id == pos.factory_id, EquipmentModel.model == pos.model))
    if cat is None:
        raise HTTPException(404)
    tp, lp = dict(cat.tech_params), dict(cat.logistics_params)
    for code in codes:
        if code in pos.logistics_params:
            lp[code] = pos.logistics_params[code]
        elif code in pos.tech_params:
            tp[code] = pos.tech_params[code]
    cat.tech_params, cat.logistics_params = tp, lp
    audit(db, user.id, deal.id, "equipment_model", cat.id, "update_from_rfq", after={"codes": codes})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ переходы менеджера

@router.post("/{deal_id}/send-to-calc")
def send_to_calc_ep(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    errors = manager_checks(_deal_dict(deal))
    if errors:
        raise HTTPException(422, {"message": "Проверки не пройдены", "errors": errors})
    st = DealState(status=deal.status, service_flag=deal.service_flag)
    try:
        tr = send_to_calc(st, date.today(), sla(db))
    except Exception as e:
        raise HTTPException(409, str(e))
    deal.logistics_done = deal.service_done = False
    complete_tasks(db, deal, block="manager")
    apply_transition(db, deal, tr, user.id)
    db.commit()
    return {"status": deal.status.value}


class KpSentIn(BaseModel):
    recipients: list[str]
    sent_via: str = "outlook"   # outlook | system


@router.post("/{deal_id}/kp-sent")
def kp_sent_ep(deal_id: str, body: KpSentIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    try:
        tr = wf_kp_sent(DealState(status=deal.status, service_flag=deal.service_flag))
    except Exception as e:
        raise HTTPException(409, str(e))
    db.add(FeedEntry(deal_id=deal.id, kind="mail_out", direction="out", counterparty=", ".join(body.recipients),
                     subject=f"КП по сделке {deal.number}", body=f"Отправлено через {body.sent_via}", user_id=user.id))
    complete_tasks(db, deal, block="kp")
    apply_transition(db, deal, tr, user.id)
    # задачи 7, 8: история цен клиентам по отправленной версии КП; предложение занести оборудование в каталог
    from ..services.catalog_service import record_client_prices, propose_catalog_task
    record_client_prices(db, deal, user.id)
    propose_catalog_task(db, deal, user.id)
    db.commit()
    return {"status": deal.status.value}


class OutcomeIn(BaseModel):
    outcome: str
    reason: str
    won_variant_id: Optional[str] = None   # задача 5: какое предложение выбрал клиент


@router.post("/{deal_id}/outcome")
def outcome_ep(deal_id: str, body: OutcomeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _editable_by_manager(deal, user)
    if body.outcome == "won" and deal.has_alternatives and not body.won_variant_id:
        raise HTTPException(422, "Укажите, какое предложение выбрал клиент (won_variant_id)")
    try:
        tr = wf_close(DealState(status=deal.status, service_flag=deal.service_flag), body.outcome, body.reason, date.today(), sla(db))
    except Exception as e:
        raise HTTPException(409, str(e))
    deal.outcome, deal.outcome_reason = body.outcome, body.reason
    if body.won_variant_id:
        deal.won_variant_id = body.won_variant_id
    elif not deal.has_alternatives:
        deal.won_variant_id = next((v.id for v in deal.variants if v.is_default), None)
    apply_transition(db, deal, tr, user.id)
    from ..services.catalog_service import update_client_price_outcome
    update_client_price_outcome(db, deal)
    closed_related: list[str] = []
    if body.outcome == "won":
        from ..services.execution_service import rebuild_stages
        rebuild_stages(db, deal)
        # задача 9: связанные заявки по тому же запросу клиента закрываются автоматически
        if deal.request_group_id:
            from ..core.workflow import OPEN_STATUSES
            for d in db.scalars(select(Deal).where(Deal.request_group_id == deal.request_group_id, Deal.id != deal.id)).all():
                if d.status in OPEN_STATUSES:
                    before = d.status.value
                    d.status, d.outcome, d.outcome_reason = DealStatus.LOST, "lost", f"Клиент выбрал другое предложение ({deal.number})"
                    audit(db, user.id, d.id, "deal", d.id, "status", {"status": before}, {"status": "lost"}, d.outcome_reason)
                    db.add(FeedEntry(deal_id=d.id, kind="system", subject="Заявка закрыта", body=d.outcome_reason, user_id=user.id))
                    complete_tasks(db, d)
                    update_client_price_outcome(db, d)
                    closed_related.append(d.number)
    db.commit()
    return {"status": deal.status.value, "closed_related": closed_related}


class DuplicateIn(BaseModel):
    offer_id: Optional[str] = None       # задача 9: ответ завода, который переносится в новую заявку
    copy_calcs: bool = True              # копировать расчёты логиста и сервиса (помечаются «проверить»)
    comment: str = ""


@router.post("/{deal_id}/duplicate", status_code=201)
def duplicate(deal_id: str, body: DuplicateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Задача 9: «Дублировать заявку» — новая сделка того же клиента с копией требований, технических характеристик, изображений,
    выбранного ответа завода и (по умолчанию) расчётов логиста и сервиса. Номер — производный (D-2026-0042-2)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not (user.has(Role.GD, Role.CD) or (user.has(Role.MANAGER) and deal.manager_id == user.id)):
        raise HTTPException(403)
    from ..services.offers_service import duplicate_deal
    new = duplicate_deal(db, deal, user.id, offer_id=body.offer_id, copy_calcs=body.copy_calcs, comment=body.comment)
    db.commit()
    db.refresh(new)
    return deal_payload(new, user)


@router.get("/{deal_id}/related")
def related(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Связанные заявки по одному запросу клиента (задача 9)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not deal.request_group_id:
        return []
    out = []
    for d in db.scalars(select(Deal).where(Deal.request_group_id == deal.request_group_id).order_by(Deal.number)).all():
        row = {"id": d.id, "number": d.number, "status": d.status.value, "outcome": d.outcome, "parent_deal_id": d.parent_deal_id,
               "factories": sorted({p.factory_id for p in d.positions}), "models": [p.designation for p in d.positions], "current": d.id == deal.id}
        if user.can("price"):
            row["price_rub"] = next((v.price_rub for v in d.variants if v.price_rub is not None), None)
        out.append(row)
    return out


# ------------------------------------------------------------------ лента, версии, вложения, журнал

@router.get("/{deal_id}/feed")
def feed(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    return [serialize(f) for f in db.scalars(select(FeedEntry).where(FeedEntry.deal_id == deal.id).order_by(FeedEntry.at.desc())).all()]


@router.get("/{deal_id}/versions")
def versions(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    rows = db.scalars(select(DealVersion).where(DealVersion.deal_id == deal.id).order_by(DealVersion.version_no.desc())).all()
    out = []
    for v in rows:
        d = serialize(v, exclude={"calc_input", "calc_result"})
        if not user.can("cost_files"):
            d.pop("excel_path", None)                         # без права на папку 04 — только версия без себестоимости
        out.append(d)
    return filter_payload(out, user)


@router.get("/{deal_id}/versions/{ver_id}")
def version_detail(deal_id: str, ver_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    v = db.get(DealVersion, ver_id)
    if v is None or v.deal_id != deal.id:
        raise HTTPException(404)
    return filter_payload(serialize(v), user)


@router.get("/{deal_id}/attachments")
def attachments(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    rows = db.scalars(select(Attachment).where(Attachment.deal_id == deal.id)).all()
    if not user.can("cost_files"):
        rows = [r for r in rows if r.folder != "04_cost"]
    return [serialize(r) for r in rows]


@router.get("/{deal_id}/audit")
def audit_log(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.CD, Role.FD, Role.GD, Role.ADMIN))):
    from ..models import AuditLog
    rows = db.scalars(select(AuditLog).where(AuditLog.deal_id == deal_id).order_by(AuditLog.at.desc())).all()
    return [serialize(r) for r in rows]
