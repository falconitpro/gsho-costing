"""Справочники и база клиентов (раздел 11)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.roles import Role, filter_payload
from ..core.workflow import DealStatus
from ..db import get_db
from ..models import Client, Deal, DealVersion, EquipmentModel, Factory, FactoryPriceHistory, HsCode, PaymentScheme, Position
from ..services.deal_service import audit
from .common import serialize

router = APIRouter(prefix="/api/refs", tags=["references"])


class ClientIn(BaseModel):
    name: str
    inn: str = Field(min_length=10, max_length=12)
    address: str = ""
    sites: list[dict] = Field(default_factory=list)
    contacts: list[dict] = Field(default_factory=list)
    default_payment_scheme_id: Optional[str] = None
    full_name: str = ""
    kpp: str = ""
    ogrn: str = ""
    director: str = ""
    legal_status: str = ""
    okved: str = ""
    registry: dict = Field(default_factory=dict)


def _registry_lookup(db: Session, inn: str, user: CurrentUser) -> list[dict]:
    """Реквизиты по ИНН из внешнего источника; каждый вызов — в журнал интеграций."""
    from .. import adapters
    from ..adapters.company_registry import RegistryNotConfigured
    from ..models import IntegrationLog
    reg = None
    try:
        reg = adapters.company_registry()
        found = [c.as_dict() for c in reg.find_by_inn(inn)]
        db.add(IntegrationLog(system="registry", operation="find_by_inn", ok=True, details={"provider": reg.name, "inn": inn, "found": len(found)}))
        db.commit()
        return found
    except RegistryNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        db.add(IntegrationLog(system="registry", operation="find_by_inn", ok=False, details={"provider": getattr(reg, "name", ""), "inn": inn, "error": str(e)[:300]}))
        db.commit()
        raise HTTPException(502, f"Источник реквизитов недоступен: {e}")


@router.get("/clients/lookup")
def client_lookup(inn: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Поиск организации по ИНН: реквизиты из ЕГРЮЛ/ЕГРИП (через подключённый источник) и признак «уже есть в базе»."""
    from ..adapters.company_registry import inn_valid
    inn = inn.strip()
    if not inn_valid(inn):
        raise HTTPException(422, "ИНН неверный: 10 цифр для организации или 12 для ИП, с правильными контрольными цифрами")
    existing = db.scalar(select(Client).where(Client.inn == inn))
    found = _registry_lookup(db, inn, user)
    return {"inn": inn, "existing_client_id": existing.id if existing else None, "results": found}

@router.get("/clients")
def clients(q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(Client).order_by(Client.name)
    if q:
        stmt = stmt.where(or_(Client.name.ilike(f"%{q}%"), Client.inn.like(f"{q}%")))
    return [serialize(c) for c in db.scalars(stmt).all()]


@router.post("/clients", status_code=201)
def create_client(body: ClientIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.MANAGER, Role.CD, Role.GD, Role.ADMIN))):
    from datetime import datetime, timezone
    from ..adapters.company_registry import inn_valid
    if not inn_valid(body.inn):
        raise HTTPException(422, "ИНН неверный: проверьте контрольные цифры")
    if db.scalar(select(Client).where(Client.inn == body.inn)):
        raise HTTPException(409, "Клиент с таким ИНН уже есть")
    c = Client(manager_id=user.id, **body.model_dump())
    if body.registry:
        c.registry_checked_at = datetime.now(timezone.utc)
    db.add(c)
    db.flush()
    audit(db, user.id, None, "client", c.id, "create", after=body.model_dump())
    db.commit()
    return serialize(c)


@router.get("/clients/{client_id}")
def client_card(client_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Карточка клиента: воронка, суммы и средняя маржа по закрытым (видно КД/ФД/ЗГД), последние КП."""
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404)
    deals = db.scalars(select(Deal).where(Deal.client_id == c.id).order_by(Deal.created_at.desc())).all()
    if user.has(Role.MANAGER) and not user.has(Role.CD, Role.FD, Role.GD):
        deals = [d for d in deals if d.manager_id == user.id]
    # задача 2: выигранная сделка дальше идёт «Контракт → Исполнение → Закрыта»; выигрыш определяем по исходу или по этим стадиям
    won_statuses = (DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED)
    is_won = lambda d: d.outcome == "won" or d.status in won_statuses  # noqa: E731
    funnel = {"requests": len(deals), "kp_sent": sum(d.status in (DealStatus.KP_SENT, DealStatus.LOST) or is_won(d) for d in deals),
              "won": sum(is_won(d) for d in deals)}
    margins, sums = [], 0.0
    last_kp = []
    for d in deals:
        for v in d.variants:
            if v.price_rub:
                last_kp.append({"deal": d.number, "variant": v.name, "price_rub": v.price_rub, "on": d.updated_at.isoformat() if d.updated_at else None,
                                "status": d.status.value, "models": [p.designation for p in d.positions if p.id in v.position_ids]})
        if is_won(d) and d.approved_version_id:
            ver = db.get(DealVersion, d.approved_version_id)
            if ver:
                for vid, scen in ver.calc_result["variants"].items():
                    m = scen["base"].get("margin_pct")
                    if m is not None:
                        margins.append(m)
                        sums += scen["base"].get("price_rub") or 0
    data = serialize(c)
    from ..services.catalog_service import client_kp_history
    kp_history = client_kp_history(db, c.id) if user.can("client_price_history") else []
    totals = {"kp_count": len(kp_history), "won": sum(1 for h in kp_history if h["outcome"] == "won"),
              "sum_rub": round(sum((h["latest"]["total_rub"] or 0) for h in kp_history), 2)}
    data.update({"funnel": funnel, "deals": [{"id": d.id, "number": d.number, "status": d.status.value, "outcome": d.outcome,
                                             "outcome_reason": d.outcome_reason} for d in deals],
                 "last_kp": last_kp[:10], "kp_history": kp_history, "kp_totals": totals,
                 "avg_margin_pct": round(sum(margins) / len(margins), 2) if margins else None, "sum_won_rub": sums})
    return filter_payload(data, user)


@router.post("/clients/{client_id}/refresh-registry")
def client_refresh_registry(client_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.MANAGER, Role.CD, Role.GD, Role.ADMIN))):
    """Обновить реквизиты клиента из реестра (наименование, КПП, ОГРН, адрес, руководитель, статус)."""
    from datetime import datetime, timezone
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404)
    found = _registry_lookup(db, c.inn, user)
    if not found:
        raise HTTPException(404, "Организация с таким ИНН в реестре не найдена")
    main = next((x for x in found if (x.get("raw") or {}).get("branch_type") in (None, "MAIN")), found[0])
    before = serialize(c)
    c.name = main["name"] or c.name
    for k_src, k_dst in (("full_name", "full_name"), ("kpp", "kpp"), ("ogrn", "ogrn"), ("address", "address"), ("director", "director"),
                         ("status", "legal_status"), ("okved", "okved")):
        if main.get(k_src):
            setattr(c, k_dst, main[k_src])
    c.registry, c.registry_checked_at = main, datetime.now(timezone.utc)
    audit(db, user.id, None, "client", c.id, "registry_refresh", {k: before.get(k) for k in ("name", "kpp", "address", "director")}, {"source": main.get("source")})
    db.commit()
    return serialize(c)


@router.get("/factories")
def factories(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    out = [serialize(f) for f in db.scalars(select(Factory).order_by(Factory.name)).all()]
    if not user.can("factory_price_history"):          # банковские реквизиты — тем, кто видит цены завода
        for f in out:
            f.pop("bank_details", None)
    return out


CONTACT_ROLES = {"rfq": "Заявки и КП", "sales": "Продажи / договор", "logistics": "Логистика и отгрузка", "finance": "Финансы и оплаты",
                 "service": "Сервис и запчасти", "quality": "Качество и документы", "management": "Руководство", "other": "Другое"}


class FactoryContact(BaseModel):
    name: str
    name_zh: str = ""
    position: str = ""
    roles: list[str] = Field(default_factory=list)       # направления из CONTACT_ROLES
    phone: str = ""
    mobile: str = ""
    email: str = ""
    wechat: str = ""
    wecom_id: str = ""                                    # внешний контакт WeCom (для отправки из системы)
    whatsapp: str = ""
    telegram: str = ""
    language: str = ""                                    # ru | zh | en
    primary: bool = False
    comment: str = ""


class FactoryIn(BaseModel):
    name: str
    name_zh: str = ""
    short_name: str = ""
    address: str = ""
    address_zh: str = ""
    country: str = "CN"
    province: str = ""
    city: str = ""
    website: str = ""
    uscc: str = ""
    supplier_status: str = ""
    contacts: list[FactoryContact] = Field(default_factory=list)
    payment_terms: list[dict] = Field(default_factory=list)
    bank_details: dict = Field(default_factory=dict)
    default_currency: str = "CNY"
    default_basis: str = ""
    machine_types: list[str] = Field(default_factory=list)
    trademarks: list[str] = Field(default_factory=list)      # торговые марки (первая — по умолчанию для позиций и каталога)
    typical_production_days: Optional[int] = None
    rating: Optional[float] = None
    notes: str = ""
    is_active: bool = True


def _factory_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(Role.MANAGER, Role.CD, Role.GD, Role.ADMIN, Role.LOGISTICIAN):
        raise HTTPException(403, "Карточки заводов ведут менеджеры, логисты, КД и ЗГД")
    return user


def _apply_factory(f: Factory, body: FactoryIn, user: Optional[CurrentUser] = None) -> None:
    data = body.model_dump()
    if user is not None and not user.can("factory_price_history"):
        data.pop("bank_details", None)                   # реквизиты не видны роли — и не перезаписываются ею
    marks: list[str] = []
    for t in data.get("trademarks") or []:
        t = (t or "").strip()
        if t and t.lower() not in {m.lower() for m in marks}:
            marks.append(t)
    data["trademarks"] = marks
    for k, v in data.items():
        setattr(f, k, v)
    # отправка из системы (запрос заводу): WeCom-контакты по направлению «Заявки» первыми
    ordered = sorted(data["contacts"], key=lambda c: (("rfq" not in c["roles"]), not c["primary"]))
    f.wecom_external_ids = [c["wecom_id"] for c in ordered if c.get("wecom_id")]


@router.get("/countries")
def countries(user: CurrentUser = Depends(get_current_user)):
    """Справочник стран для поля «Страна-производитель»."""
    from ..services.countries import country_names
    return country_names()


@router.get("/factories/contact-roles")
def factory_contact_roles(user: CurrentUser = Depends(get_current_user)):
    return CONTACT_ROLES


@router.get("/factories/{factory_id}")
def factory_card(factory_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    f = db.get(Factory, factory_id)
    if f is None:
        raise HTTPException(404)
    d = serialize(f)
    d["models"] = [{"id": m.id, "model": m.model, "machine_type": m.machine_type, "status": m.status}
                   for m in db.scalars(select(EquipmentModel).where(EquipmentModel.factory_id == f.id).order_by(EquipmentModel.model)).all()]
    d["deals"] = len({p.deal_id for p in db.scalars(select(Position).where(Position.factory_id == f.id)).all()})
    if not user.can("factory_price_history"):
        d.pop("bank_details", None)
    return d


@router.post("/factories", status_code=201)
def create_factory(body: FactoryIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_factory_editor)):
    if db.scalar(select(Factory).where(Factory.name == body.name)):
        raise HTTPException(409, "Завод с таким наименованием уже есть")
    f = Factory()
    _apply_factory(f, body, user)
    db.add(f)
    db.flush()
    audit(db, user.id, None, "factory", f.id, "create", after={"name": f.name})
    db.commit()
    return serialize(f)


@router.put("/factories/{factory_id}")
def update_factory(factory_id: str, body: FactoryIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_factory_editor)):
    f = db.get(Factory, factory_id)
    if f is None:
        raise HTTPException(404)
    if body.name != f.name and db.scalar(select(Factory).where(Factory.name == body.name)):
        raise HTTPException(409, "Завод с таким наименованием уже есть")
    before = {"name": f.name, "contacts": len(f.contacts or [])}
    _apply_factory(f, body, user)
    audit(db, user.id, None, "factory", f.id, "update", before, {"name": f.name, "contacts": len(f.contacts or [])})
    db.commit()
    out = serialize(f)
    if not user.can("factory_price_history"):
        out.pop("bank_details", None)
    return out


@router.get("/factories/{factory_id}/prices")
def factory_prices(factory_id: str, model: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """История цен и сроков завода (задача 7): видят менеджер, КД, ЗГД (настраивается в уровнях доступа)."""
    if not user.can("factory_price_history"):
        raise HTTPException(403, "История цен завода закрыта для роли")
    stmt = select(FactoryPriceHistory).where(FactoryPriceHistory.factory_id == factory_id).order_by(FactoryPriceHistory.on.desc())
    if model:
        stmt = stmt.where(FactoryPriceHistory.model == model)
    return [serialize(h) for h in db.scalars(stmt.limit(50)).all()]


@router.get("/equipment")
def equipment(q: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(EquipmentModel).order_by(EquipmentModel.model)
    if q:
        stmt = stmt.where(or_(EquipmentModel.model.ilike(f"%{q}%"), EquipmentModel.machine_type.ilike(f"%{q}%")))
    rows = db.scalars(stmt).all()
    out = [serialize(m) for m in rows]
    if not user.has(Role.SERVICE_HEAD, Role.CD, Role.FD, Role.GD, Role.ADMIN):
        for m in out:
            m.pop("maintenance_regulations", None)
    return out


@router.get("/hs")
def hs_search(q: str = Query(min_length=2), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(HsCode).where(HsCode.valid_to.is_(None), or_(HsCode.code.like(f"{q}%"), HsCode.description.ilike(f"%{q}%"))).limit(30)
    return [serialize(h) for h in db.scalars(stmt).all()]


@router.get("/hs/lookup")
def hs_lookup(code: str = Query(min_length=4, max_length=14), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Наименование кода ТН ВЭД: справочник системы → внешний сервис (HS_LOOKUP_URL) → ссылки для поиска вручную."""
    import re as _re
    c = _re.sub(r"\D", "", code)
    row = db.scalar(select(HsCode).where(HsCode.code == c, HsCode.valid_to.is_(None)))
    links = [{"title": "TKS.ru", "url": f"https://www.tks.ru/db/tnved/search?q={c}"}, {"title": "Alta-Soft", "url": f"https://www.alta.ru/tnved/code/{c}/"}]
    if row:
        return {"code": c, "source": "reference", "description": row.description, "duty_pct": row.duty_pct, "vat_pct": row.vat_pct,
                "util_fee": row.util_fee, "certification": row.certification, "links": links}
    from ..core.config import get_settings
    tpl = get_settings().hs_lookup_url
    if tpl:
        import httpx
        try:
            r = httpx.get(tpl.format(code=c), timeout=10)
            r.raise_for_status()
            data = r.json()
            item = data[0] if isinstance(data, list) and data else data
            name = next((item.get(k) for k in ("name", "Name", "description", "Description", "title") if isinstance(item, dict) and item.get(k)), None)
            if name:
                return {"code": c, "source": "internet", "description": str(name), "links": links}
        except Exception as e:   # сервис недоступен — логист вводит наименование сам
            return {"code": c, "source": None, "description": "", "error": f"внешний сервис недоступен: {str(e)[:120]}", "links": links}
    return {"code": c, "source": None, "description": "", "links": links,
            "note": "Кода нет в справочнике; автоматический поиск в интернете не подключён — введите наименование, оно сохранится в справочник"}


@router.get("/payment-schemes")
def payment_schemes(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return [serialize(p) for p in db.scalars(select(PaymentScheme)).all()]


class SchemeIn(BaseModel):
    name: str
    lines: list[dict]


@router.post("/payment-schemes", status_code=201)
def create_scheme(body: SchemeIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.CD, Role.GD, Role.ADMIN))):
    p = PaymentScheme(**body.model_dump())
    db.add(p)
    db.commit()
    return serialize(p)
