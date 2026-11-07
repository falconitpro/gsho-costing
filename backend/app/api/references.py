"""Справочники и база клиентов (раздел 11)."""
from __future__ import annotations

from datetime import datetime, timezone
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

WON_STATUSES = (DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED)
OPEN_STATUSES = (DealStatus.DRAFT, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC, DealStatus.CALC_READY, DealStatus.CD_APPROVAL, DealStatus.FD_VISA,
                 DealStatus.GD_VISA, DealStatus.REWORK, DealStatus.APPROVED, DealStatus.KP_SENT)


def _client_stats(db: Session, user: CurrentUser) -> dict[str, dict]:
    """Аналитика по клиентам для списка контрагентов: сделки всего / в работе / выиграно / проиграно, последняя активность,
    менеджер последней сделки, сумма выигранных (только finance_analytics)."""
    from ..models import User
    fin = user.can("finance_analytics")
    users = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    out: dict[str, dict] = {}
    deals = db.scalars(select(Deal).where(Deal.deleted_at.is_(None)).order_by(Deal.created_at)).all()
    for d in deals:
        st = out.setdefault(d.client_id, {"deals": 0, "open": 0, "won": 0, "lost": 0, "last_on": None, "last_number": "", "last_status": "", "manager": "", "sum_won_rub": 0.0})
        st["deals"] += 1
        won = d.outcome == "won" or d.status in WON_STATUSES
        if won:
            st["won"] += 1
        elif d.status in OPEN_STATUSES:
            st["open"] += 1
        elif d.status == DealStatus.LOST or d.outcome == "lost":
            st["lost"] += 1
        on = (d.updated_at or d.created_at)
        if on and (st["last_on"] is None or on > st["last_on"]):
            st["last_on"], st["last_number"], st["last_status"], st["manager"] = on, d.number, d.status.value, users.get(d.manager_id, "")
        if fin and won and d.approved_version_id:
            ver = db.get(DealVersion, d.approved_version_id)
            vid = d.won_variant_id or next(iter(ver.calc_result.get("variants", {})), None) if ver else None
            if ver and vid and vid in ver.calc_result.get("variants", {}):
                st["sum_won_rub"] += ver.calc_result["variants"][vid]["base"].get("price_rub") or 0
    for st in out.values():
        st["last_on"] = st["last_on"].isoformat() if st["last_on"] else None
        st["sum_won_rub"] = round(st["sum_won_rub"], 2) if fin else None
    return out


@router.get("/clients")
def clients(q: str = "", stats: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    stmt = select(Client).order_by(Client.name)
    if q:
        stmt = stmt.where(or_(Client.name.ilike(f"%{q}%"), Client.inn.like(f"{q}%")))
    rows = [serialize(c) for c in db.scalars(stmt).all()]
    if stats:
        from ..models import LegalProfile
        from sqlalchemy import func as _f
        prof_counts = dict(db.execute(select(LegalProfile.owner_id, _f.count()).where(LegalProfile.owner_kind == "client", LegalProfile.is_active.is_(True)).group_by(LegalProfile.owner_id)).all())
        st = _client_stats(db, user)
        empty = {"deals": 0, "open": 0, "won": 0, "lost": 0, "last_on": None, "last_number": "", "last_status": "", "manager": "", "sum_won_rub": None}
        for r in rows:
            r["stats"] = st.get(r["id"], empty)
            r["requisites_count"] = prof_counts.get(r["id"], 0)
    return rows


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
    from ..services.moysklad_import import relink_client_prices
    relink_client_prices(db)        # договорные цены запчастей, перенесённые раньше клиента
    audit(db, user.id, None, "client", c.id, "create", after=body.model_dump())
    db.commit()
    return serialize(c)


# ------------------------------------------------------------------ наборы реквизитов клиента (для договоров)

REQ_FIELDS = ("title", "legal_name", "short_name", "inn", "kpp", "ogrn", "legal_address", "postal_address", "bank_name", "bik", "account", "corr_account",
              "signatory_name", "signatory_position", "signatory_basis", "phone", "email", "comment")


class RequisitesSet(BaseModel):
    id: Optional[str] = None
    title: str = ""
    is_default: bool = False
    legal_name: str = ""
    short_name: str = ""
    inn: str = ""
    kpp: str = ""
    ogrn: str = ""
    legal_address: str = ""
    postal_address: str = ""
    bank_name: str = ""
    bik: str = ""
    account: str = ""
    corr_account: str = ""
    signatory_name: str = ""
    signatory_position: str = ""
    signatory_basis: str = ""
    phone: str = ""
    email: str = ""
    comment: str = ""


class RequisitesIn(BaseModel):
    items: list[RequisitesSet]


def requisites_from_card(c: Client) -> dict:
    """Реквизиты «по карточке» (ЕГРЮЛ) — подсказка для заполнения профиля."""
    return {"id": "card", "title": "Из карточки (ЕГРЮЛ)", "is_default": False, "legal_name": c.full_name or c.name, "short_name": c.name, "inn": c.inn, "kpp": c.kpp or "",
            "ogrn": c.ogrn or "", "legal_address": c.address or "", "postal_address": "", "bank_name": "", "bik": "", "account": "", "corr_account": "",
            "signatory_name": c.director or "", "signatory_position": "Генеральный директор" if c.director else "", "signatory_basis": "Устава" if c.director else "",
            "phone": "", "email": "", "comment": ""}


def client_requisites_options(c: Client) -> list[dict]:
    """Варианты реквизитов клиента для договора — юридические профили (юридический блок, этап 1)."""
    from sqlalchemy.orm import object_session
    from ..services.legal_profiles import client_requisites_options as _opts
    db = object_session(c)
    return _opts(db, c) if db is not None else []


@router.get("/clients/{client_id}/requisites")
def get_requisites(client_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Совместимость: наборы реквизитов = юридические профили клиента (ведутся в юридическом блоке)."""
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404)
    items = client_requisites_options(c)
    db.commit()
    return {"items": items, "from_card": requisites_from_card(c)}


@router.put("/clients/{client_id}/requisites")
def put_requisites(client_id: str, body: RequisitesIn, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_roles(Role.MANAGER, Role.CD, Role.GD, Role.ADMIN, Role.LAWYER))):
    """Совместимость: наборы реквизитов записываются в юридические профили клиента; один — по умолчанию."""
    from ..services.legal_profiles import upsert_requisites
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404)
    items = [r.model_dump() for r in body.items if (r.title.strip() or r.legal_name.strip())]
    if sum(1 for x in items if x["is_default"]) > 1:
        raise HTTPException(422, "По умолчанию может быть только один набор реквизитов")
    before = len(c.requisites or [])
    upsert_requisites(db, c, items, user.id)
    c.requisites = []          # единый источник — профили
    audit(db, user.id, None, "client", c.id, "requisites", {"items": before}, {"items": len(items)})
    db.commit()
    return {"items": client_requisites_options(c), "from_card": requisites_from_card(c)}


@router.get("/clients/{client_id}")
def client_card(client_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Карточка клиента: воронка, суммы и средняя маржа по закрытым (видно КД/ФД/ЗГД), последние КП."""
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404)
    deals = db.scalars(select(Deal).where(Deal.client_id == c.id, Deal.deleted_at.is_(None)).order_by(Deal.created_at.desc())).all()
    if user.has(Role.MANAGER) and not user.has(Role.CD, Role.FD, Role.GD):
        deals = [d for d in deals if user.is_manager_of(d.manager_id)]
    # задача 2: выигранная сделка дальше идёт «Контракт → Исполнение → Закрыта»; выигрыш определяем по исходу или по этим стадиям
    won_statuses = (DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED)
    is_won = lambda d: d.outcome == "won" or d.status in won_statuses  # noqa: E731
    funnel = {"requests": len(deals), "kp_sent": sum(d.status in (DealStatus.KP_SENT, DealStatus.LOST) or is_won(d) for d in deals),
              "won": sum(is_won(d) for d in deals)}
    margins, sums = [], 0.0
    last_kp = []
    fin = user.can("finance_analytics")
    for d in deals:
        for v in d.variants:
            if v.price_rub and fin:
                last_kp.append({"deal": d.number, "variant": v.name, "price_rub": v.price_rub, "on": d.updated_at.isoformat() if d.updated_at else None,
                                "status": d.status.value, "models": [p.designation for p in d.positions if p.id in v.position_ids]})
        if fin and is_won(d) and d.approved_version_id:
            ver = db.get(DealVersion, d.approved_version_id)
            if ver:
                for vid, scen in ver.calc_result["variants"].items():
                    m = scen["base"].get("margin_pct")
                    if m is not None:
                        margins.append(m)
                        sums += scen["base"].get("price_rub") or 0
    data = serialize(c)
    from ..services.catalog_service import client_kp_history
    kp_history = client_kp_history(db, c.id) if fin and user.can("client_price_history") else []
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
def factories(include_archived: bool = False, supplies: str = "", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Поставщики; supplies=gsho | parts — только поставщики ГШО или запасных частей."""
    from ..models import FactoryChange
    pend = {}
    for fid in db.scalars(select(FactoryChange.factory_id).where(FactoryChange.status == "pending")).all():
        pend[fid] = pend.get(fid, 0) + 1
    out = [{**serialize(f), "approval_status": f.approval_status or "approved", "pending_changes": pend.get(f.id, 0)}
           for f in db.scalars(select(Factory).order_by(Factory.name)).all()
           if (include_archived or f.is_active is not False) and (not supplies or supplies in (f.supplies or []))]
    if not user.can("factory_price_history"):          # банковские реквизиты — тем, кто видит цены завода
        for f in out:
            f.pop("bank_details", None)
    return out


SUPPLIES = {"gsho": "ГШО", "parts": "Запасные части"}
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
    # запчасти (шаг 1): supplier_status = group_company — компания группы как поставщик
    legal_entity_id: Optional[str] = None
    language: str = ""                                    # ru | zh | en | de
    default_route_id: Optional[str] = None
    supplies: list[str] = Field(default_factory=lambda: ["gsho"])   # gsho | parts — раздел «Поставщики»


SUPPLIER_STATUSES = {"manufacturer", "official_dealer", "trader", "group_company", ""}


def _factory_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Заводить поставщиков и предлагать изменения могут все сотрудники (кроме наблюдателя); утверждают КД или ЗГД."""
    if not user.roles or user.roles <= {Role.OBSERVER}:
        raise HTTPException(403, "Наблюдатель не меняет карточки поставщиков")
    return user


APPROVERS = (Role.CD, Role.GD)
FACTORY_FIELD_TITLES = {
    "name": "Наименование", "name_zh": "Наименование (中文)", "short_name": "Краткое название", "address": "Адрес", "address_zh": "Адрес (中文)",
    "country": "Страна", "province": "Провинция", "city": "Город", "website": "Сайт", "uscc": "Код юрлица", "supplier_status": "Статус поставщика",
    "contacts": "Контакты", "payment_terms": "Условия оплаты", "bank_details": "Банковские реквизиты", "default_currency": "Валюта по умолчанию",
    "default_basis": "Базис по умолчанию", "machine_types": "Что выпускает", "trademarks": "Торговые марки", "typical_production_days": "Срок производства",
    "rating": "Оценка", "notes": "Заметки", "is_active": "Работаем", "legal_entity_id": "Компания группы", "language": "Язык переписки",
    "default_route_id": "Маршрут поставки запчастей", "supplies": "Что поставляет",
}


def _apply_factory(f: Factory, body: FactoryIn, user: Optional[CurrentUser] = None) -> None:
    data = body.model_dump()
    if data.get("supplier_status") not in SUPPLIER_STATUSES:
        raise HTTPException(422, "Тип поставщика: завод, официальный дилер, торговая компания или компания группы")
    data["supplies"] = [x for x in dict.fromkeys(data.get("supplies") or []) if x in SUPPLIES]
    if not data["supplies"]:
        raise HTTPException(422, "Отметьте, что поставляет: ГШО, запасные части или и то и другое")
    if data.get("supplier_status") != "group_company":
        data["legal_entity_id"] = None
    elif not data.get("legal_entity_id"):
        raise HTTPException(422, "Для компании группы выберите, какая это компания")
    if user is not None and not user.can("factory_price_history"):
        data.pop("bank_details", None)                   # реквизиты не видны роли — и не перезаписываются ею
    marks: list[str] = []
    for t in data.get("trademarks") or []:
        t = (t or "").strip()
        if t and t.lower() not in {m.lower() for m in marks}:
            marks.append(t)
    data["trademarks"] = marks
    return data


def _set_factory(f: Factory, data: dict) -> None:
    for k, v in data.items():
        setattr(f, k, v)
    if "contacts" in data:   # отправка из системы (запрос заводу): WeCom-контакты по направлению «Заявки» первыми
        ordered = sorted(f.contacts or [], key=lambda c: (("rfq" not in (c.get("roles") or [])), not c.get("primary")))
        f.wecom_external_ids = [c["wecom_id"] for c in ordered if c.get("wecom_id")]


def _approved(f: Factory) -> bool:
    return f.approval_status in (None, "approved")


def _notify_approvers(db: Session, user: CurrentUser, text: str) -> None:
    from ..models import User
    from ..services.deal_service import notify
    for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
        if set(u.roles or []) & {"cd", "gd"} and u.id != user.id:
            notify(db, None, u.id, "factory_approval", None, text)


def _save_factory(db: Session, f: Factory, data: dict, user: CurrentUser, comment: str = "") -> Optional["FactoryChange"]:
    """КД и ЗГД, а также любые правки ещё не утверждённого поставщика — сразу в карточку.
    Правка утверждённого поставщика остальными — предложение изменений, в карточку попадает после утверждения КД или ЗГД."""
    from ..models import FactoryChange
    if user.has(*APPROVERS) or not _approved(f) or not f.id:
        _set_factory(f, data)
        return None
    cur = serialize(f)
    changed = {k: v for k, v in data.items() if cur.get(k) != v and not (cur.get(k) in (None, "", [], {}) and v in (None, "", [], {}))}
    if not changed:
        return None
    ch = db.scalars(select(FactoryChange).where(FactoryChange.factory_id == f.id, FactoryChange.proposed_by == user.id,
                                                FactoryChange.status == "pending")).first()
    if ch is None:
        ch = FactoryChange(factory_id=f.id, proposed_by=user.id, data={}, before={})
        db.add(ch)
    ch.data = {**(ch.data or {}), **changed}
    ch.before = {**{k: cur.get(k) for k in changed}, **(ch.before or {})}
    ch.comment = comment or ch.comment
    ch.proposed_at = datetime.now(timezone.utc)
    db.flush()
    _notify_approvers(db, user, f"Поставщик «{f.short_name or f.name}»: предложены изменения ({', '.join(FACTORY_FIELD_TITLES.get(k, k) for k in ch.data)}) — утвердите")
    return ch


def _change_payload(db: Session, ch, user: CurrentUser) -> dict:
    from ..models import User
    u = db.get(User, ch.proposed_by) if ch.proposed_by else None
    hide = not user.can("factory_price_history")
    fields = [{"field": k, "title": FACTORY_FIELD_TITLES.get(k, k), "old": (ch.before or {}).get(k), "new": v}
              for k, v in (ch.data or {}).items() if not (hide and k == "bank_details")]
    return {"id": ch.id, "factory_id": ch.factory_id, "proposed_by": ch.proposed_by, "proposed_by_name": u.display_name if u else "",
            "proposed_at": ch.proposed_at, "comment": ch.comment, "status": ch.status, "fields": fields}


def _factory_out(db: Session, f: Factory, user: CurrentUser) -> dict:
    from ..models import FactoryChange, User
    d = serialize(f)
    d["approval_status"] = f.approval_status or "approved"
    if not user.can("factory_price_history"):
        d.pop("bank_details", None)
    d["pending_changes"] = [_change_payload(db, c, user) for c in db.scalars(select(FactoryChange).where(
        FactoryChange.factory_id == f.id, FactoryChange.status == "pending").order_by(FactoryChange.proposed_at)).all()]
    cb = db.get(User, f.created_by) if f.created_by else None
    ab = db.get(User, f.approved_by) if f.approved_by else None
    d["created_by_name"], d["approved_by_name"] = (cb.display_name if cb else ""), (ab.display_name if ab else "")
    d["can_approve"] = user.has(*APPROVERS)
    return d


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
    d = {}
    d["models"] = [{"id": m.id, "model": m.model, "machine_type": m.machine_type, "status": m.status}
                   for m in db.scalars(select(EquipmentModel).where(EquipmentModel.factory_id == f.id).order_by(EquipmentModel.model)).all()]
    d.update(_factory_out(db, f, user))
    d["deals"] = len({p.deal_id for p in db.scalars(select(Position).where(Position.factory_id == f.id)).all()})
    return d


@router.post("/factories", status_code=201)
def create_factory(body: FactoryIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_factory_editor)):
    if db.scalar(select(Factory).where(Factory.name == body.name)):
        raise HTTPException(409, "Завод с таким наименованием уже есть")
    f = Factory()
    _set_factory(f, _apply_factory(f, body, user))
    f.created_by = user.id
    if user.has(*APPROVERS):
        f.approval_status, f.approved_by, f.approved_at = "approved", user.id, datetime.now(timezone.utc)
    else:
        f.approval_status = "pending"
    db.add(f)
    db.flush()
    if f.approval_status == "pending":
        _notify_approvers(db, user, f"Новый поставщик «{f.short_name or f.name}» — утвердите карточку")
    audit(db, user.id, None, "factory", f.id, "create", after={"name": f.name, "approval": f.approval_status})
    db.commit()
    return _factory_out(db, f, user)


@router.put("/factories/{factory_id}")
def update_factory(factory_id: str, body: FactoryIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_factory_editor)):
    f = db.get(Factory, factory_id)
    if f is None:
        raise HTTPException(404)
    if body.name != f.name and db.scalar(select(Factory).where(Factory.name == body.name)):
        raise HTTPException(409, "Завод с таким наименованием уже есть")
    before = {"name": f.name, "contacts": len(f.contacts or [])}
    ch = _save_factory(db, f, _apply_factory(f, body, user), user)
    audit(db, user.id, None, "factory", f.id, "propose" if ch else "update", before, {"name": f.name, "contacts": len(f.contacts or []),
                                                                                   "fields": list((ch.data if ch else {}).keys())})
    db.commit()
    out = _factory_out(db, f, user)
    out["proposed"] = bool(ch)
    return out


class ContactsIn(BaseModel):
    contacts: list[FactoryContact]


@router.put("/factories/{factory_id}/contacts")
def update_factory_contacts(factory_id: str, body: ContactsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Контакты поставщика: правка утверждённой карточки — через утверждение КД или ЗГД."""
    _factory_editor(user)
    f = db.get(Factory, factory_id)
    if f is None:
        raise HTTPException(404)
    before = {"contacts": f.contacts or []}
    ch = _save_factory(db, f, {"contacts": [c.model_dump() for c in body.contacts]}, user)
    audit(db, user.id, None, "factory", f.id, "propose" if ch else "contacts", before, {"contacts": [c.model_dump() for c in body.contacts]})
    db.commit()
    out = _factory_out(db, f, user)
    out["proposed"] = bool(ch)
    return out


def _approver(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(*APPROVERS):
        raise HTTPException(403, "Утверждает КД или ЗГД")
    return user


class DecisionIn(BaseModel):
    comment: str = ""


@router.post("/factories/{factory_id}/approve")
def approve_factory(factory_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_approver)):
    """Утвердить нового поставщика (КД или ЗГД)."""
    f = db.get(Factory, factory_id)
    if f is None:
        raise HTTPException(404)
    f.approval_status, f.approved_by, f.approved_at = "approved", user.id, datetime.now(timezone.utc)
    audit(db, user.id, None, "factory", f.id, "approve", None, {"comment": body.comment})
    if f.created_by and f.created_by != user.id:
        from ..services.deal_service import notify
        notify(db, None, f.created_by, "factory_approval", None, f"Поставщик «{f.short_name or f.name}» утверждён")
    db.commit()
    return _factory_out(db, f, user)


@router.post("/factories/{factory_id}/reject")
def reject_factory(factory_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_approver)):
    """Не утверждать нового поставщика: карточка уходит в архив с причиной."""
    f = db.get(Factory, factory_id)
    if f is None:
        raise HTTPException(404)
    if _approved(f):
        raise HTTPException(409, "Поставщик уже утверждён — его можно перевести в архив («Работаем: нет»)")
    f.approval_status, f.is_active = "rejected", False
    f.notes = (f.notes + "\n" if f.notes else "") + f"Не утверждён: {body.comment}".strip()
    audit(db, user.id, None, "factory", f.id, "reject", None, {"comment": body.comment})
    if f.created_by and f.created_by != user.id:
        from ..services.deal_service import notify
        notify(db, None, f.created_by, "factory_approval", None, f"Поставщик «{f.short_name or f.name}» не утверждён: {body.comment}")
    db.commit()
    return _factory_out(db, f, user)


@router.get("/factory-changes")
def factory_changes(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """На утверждении: новые поставщики и предложенные изменения карточек."""
    from ..models import FactoryChange
    news = [{"id": f.id, "name": f.short_name or f.name, "created_at": f.created_at} for f in
            db.scalars(select(Factory).where(Factory.approval_status == "pending").order_by(Factory.created_at)).all()]
    changes = []
    for ch in db.scalars(select(FactoryChange).where(FactoryChange.status == "pending").order_by(FactoryChange.proposed_at)).all():
        f = db.get(Factory, ch.factory_id)
        changes.append({**_change_payload(db, ch, user), "factory_name": (f.short_name or f.name) if f else ""})
    return {"new": news, "changes": changes}


def _change_or_404(db: Session, cid: str):
    from ..models import FactoryChange
    ch = db.get(FactoryChange, cid)
    if ch is None or ch.status != "pending":
        raise HTTPException(404, "Изменение не найдено или уже рассмотрено")
    return ch


@router.post("/factory-changes/{change_id}/approve")
def approve_change(change_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_approver)):
    """Утвердить изменения: поля попадают в карточку поставщика."""
    from ..services.deal_service import notify
    ch = _change_or_404(db, change_id)
    f = db.get(Factory, ch.factory_id)
    data = dict(ch.data or {})
    if "name" in data and data["name"] != f.name and db.scalar(select(Factory).where(Factory.name == data["name"], Factory.id != f.id)):
        raise HTTPException(409, "Поставщик с таким наименованием уже есть")
    _set_factory(f, data)
    ch.status, ch.decided_by, ch.decided_at, ch.decision_comment = "approved", user.id, datetime.now(timezone.utc), body.comment
    audit(db, user.id, None, "factory", f.id, "change_approve", ch.before, data)
    if ch.proposed_by and ch.proposed_by != user.id:
        notify(db, None, ch.proposed_by, "factory_approval", None, f"Изменения поставщика «{f.short_name or f.name}» утверждены")
    db.commit()
    return _factory_out(db, f, user)


@router.post("/factory-changes/{change_id}/reject")
def reject_change(change_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_approver)):
    from ..services.deal_service import notify
    ch = _change_or_404(db, change_id)
    f = db.get(Factory, ch.factory_id)
    ch.status, ch.decided_by, ch.decided_at, ch.decision_comment = "rejected", user.id, datetime.now(timezone.utc), body.comment
    audit(db, user.id, None, "factory", f.id, "change_reject", None, {"comment": body.comment})
    if ch.proposed_by and ch.proposed_by != user.id:
        notify(db, None, ch.proposed_by, "factory_approval", None, f"Изменения поставщика «{f.short_name or f.name}» не утверждены: {body.comment}")
    db.commit()
    return _factory_out(db, f, user)


@router.delete("/factories/{factory_id}")
def delete_factory(factory_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Удаление завода — только ЗГД. Если завод уже есть в сделках, каталоге или истории цен — он переносится в архив
    (скрывается из выбора, история и сделки сохраняются)."""
    from sqlalchemy.exc import IntegrityError
    from ..models import FactoryOffer, Position, RfqImport
    if not user.has(Role.GD):
        raise HTTPException(403, "Удалить завод может только ЗГД")
    f = db.get(Factory, factory_id)
    if f is None:
        raise HTTPException(404)
    used = (db.scalar(select(Position.id).where(Position.factory_id == f.id).limit(1)) or db.scalar(select(EquipmentModel.id).where(EquipmentModel.factory_id == f.id).limit(1))
            or db.scalar(select(FactoryPriceHistory.id).where(FactoryPriceHistory.factory_id == f.id).limit(1))
            or db.scalar(select(FactoryOffer.id).where(FactoryOffer.factory_id == f.id).limit(1))      # ответы завода / запросы заводу
            or db.scalar(select(RfqImport.id).where(RfqImport.factory_id == f.id).limit(1)))           # импорт RFQ этого завода
    if used:
        f.is_active = False
        audit(db, user.id, None, "factory", f.id, "archive", {"name": f.name}, {"is_active": False}, "завод используется — перенесён в архив")
        db.commit()
        return {"deleted": False, "archived": True, "message": f"Завод «{f.name}» используется в сделках или каталоге — перенесён в архив"}
    name = f.name
    try:
        audit(db, user.id, None, "factory", f.id, "delete", {"name": name})
        db.delete(f)
        db.commit()
    except IntegrityError:   # на завод ссылаются другие записи — не удаляем, а переносим в архив
        db.rollback()
        f = db.get(Factory, factory_id)
        f.is_active = False
        audit(db, user.id, None, "factory", f.id, "archive", {"name": name}, {"is_active": False}, "завод используется — перенесён в архив")
        db.commit()
        return {"deleted": False, "archived": True, "message": f"Завод «{name}» используется — перенесён в архив"}
    return {"deleted": True, "archived": False, "message": f"Завод «{name}» удалён"}


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
