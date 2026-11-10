"""Запчасти, шаг 1: компании группы, маршруты поставки, политика наценок с визами ЗГД и ФД (ТЗ запчастей, раздел 2)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..db import get_db
from ..models import Factory, FxRate, LegalEntity, MarkupPolicy, RouteStep, SupplyRoute, User
from ..services.deal_service import audit, notify
from ..services.markup_chain import ChainError, simulate, validate_steps
from .common import serialize

router = APIRouter(prefix="/api/parts", tags=["parts-setup"])

WAREHOUSE_KINDS = {"physical": "физический склад", "virtual": "виртуальный склад", "none": "нет склада"}
INCOTERMS = ["EXW", "FCA", "FAS", "FOB", "CPT", "CIP", "CFR", "CIF", "DAP", "DPU", "DDP"]
CURRENCIES = ["RUB", "CNY", "EUR", "USD", "KGS"]
POLICY_STATUS = {"draft": "черновик", "on_approval": "на утверждении", "approved": "действует", "rejected": "отклонена", "superseded": "заменена"}
APPROVER_SLOTS = {"gd": "ЗГД", "fd": "ФД"}


# ------------------------------------------------------------------ права

def _setup_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(Role.CD, Role.GD, Role.ADMIN):
        raise HTTPException(403, "Компании группы и маршруты ведут КД, ЗГД и администратор")
    return user


def _route_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.has(Role.CD, Role.GD, Role.ADMIN, Role.LOGISTICIAN):
        raise HTTPException(403, "Маршруты ведут КД, ЗГД, логист и администратор")
    return user


def _markup_reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not (user.can("cost") or user.has(Role.CD, Role.FD, Role.GD)):
        raise HTTPException(403, "Наценки между компаниями видят КД, ФД и ЗГД")
    return user


def _approver_slot(user: CurrentUser) -> str:
    """Чью визу ставит пользователь: ЗГД или ФД (у пользователя с обеими ролями — визу ЗГД)."""
    if user.has(Role.GD):
        return "gd"
    if user.has(Role.FD):
        return "fd"
    raise HTTPException(403, "Политику наценок утверждают ЗГД и ФД")


# ------------------------------------------------------------------ справочники для форм

@router.get("/meta")
def meta(user: CurrentUser = Depends(get_current_user)):
    return {"warehouse_kinds": WAREHOUSE_KINDS, "incoterms": INCOTERMS, "currencies": CURRENCIES, "policy_status": POLICY_STATUS,
            "approvers": APPROVER_SLOTS}


# ------------------------------------------------------------------ компании группы

class EntityIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1)
    country: str = ""
    currency: str = "RUB"
    warehouse: str = "none"
    sells_to_clients: bool = False
    is_active: bool = True
    notes: str = ""
    channels: Optional[dict] = None   # {"mail_profile", "mail_from", "wecom_profile"} — запросы от имени компании (D-151); секреты — в окружении


def _check_entity(body: EntityIn) -> None:
    if body.channels is not None:
        bad = set(body.channels) - {"mail_profile", "mail_from", "wecom_profile"}
        if bad:
            raise HTTPException(422, "Каналы компании: mail_profile, mail_from, wecom_profile (пароли и ключи — только в настройках сервера)")
        body.channels = {k: str(v or "").strip()[:255] for k, v in body.channels.items()}
    if body.warehouse not in WAREHOUSE_KINDS:
        raise HTTPException(422, "Вид склада: физический, виртуальный или нет склада")
    if body.currency not in CURRENCIES:
        raise HTTPException(422, f"Валюта учёта — одна из: {', '.join(CURRENCIES)}")


@router.get("/entities")
def entities(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return [serialize(e) for e in db.scalars(select(LegalEntity).order_by(LegalEntity.sells_to_clients.desc(), LegalEntity.name)).all()]


@router.post("/entities", status_code=201)
def create_entity(body: EntityIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_setup_editor)):
    _check_entity(body)
    if db.scalar(select(LegalEntity).where(func.lower(LegalEntity.code) == body.code.strip().lower())):
        raise HTTPException(409, f"Компания с кодом «{body.code}» уже есть")
    e = LegalEntity(**{**body.model_dump(exclude={"channels"}), "code": body.code.strip().lower(), "channels": body.channels or {}})
    db.add(e)
    db.flush()
    audit(db, user.id, None, "legal_entity", e.id, "create", None, body.model_dump())
    db.commit()
    return serialize(e)


@router.put("/entities/{entity_id}")
def update_entity(entity_id: str, body: EntityIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_setup_editor)):
    _check_entity(body)
    e = db.get(LegalEntity, entity_id)
    if e is None:
        raise HTTPException(404, "Компания не найдена")
    clash = db.scalar(select(LegalEntity).where(func.lower(LegalEntity.code) == body.code.strip().lower(), LegalEntity.id != entity_id))
    if clash:
        raise HTTPException(409, f"Код «{body.code}» уже занят компанией «{clash.name}»")
    before = serialize(e)
    for k, v in {**body.model_dump(exclude={"channels"}), "code": body.code.strip().lower()}.items():
        setattr(e, k, v)
    if body.channels is not None:
        e.channels = body.channels
    audit(db, user.id, None, "legal_entity", e.id, "update", before, body.model_dump())
    db.commit()
    return serialize(e)


# ------------------------------------------------------------------ маршруты

class StepIn(BaseModel):
    seller_entity_id: Optional[str] = None
    buyer_entity_id: str
    incoterms: str = ""
    place: str = ""
    currency: str = "CNY"
    customs_country: str = ""
    pay_days: Optional[int] = None
    transit_days: Optional[int] = None
    comment: str = ""


class RouteIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    supplier_country: str = ""
    is_active: bool = True
    steps: list[StepIn] = Field(default_factory=list)


def _route_payload(r: SupplyRoute, names: dict[str, str]) -> dict:
    d = serialize(r)
    d["steps"] = [{**serialize(s), "seller": names.get(s.seller_entity_id, "Поставщик") if s.seller_entity_id else "Поставщик",
                   "buyer": names.get(s.buyer_entity_id, "")} for s in r.steps]
    d["chain"] = " → ".join(["Поставщик"] + [names.get(s.buyer_entity_id, "?") for s in r.steps])
    return d


def _names(db: Session) -> dict[str, str]:
    return {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}


def _apply_route(db: Session, r: SupplyRoute, body: RouteIn) -> None:
    steps = [s.model_dump() for s in body.steps]
    errs = validate_steps(steps)
    known = set(_names(db))
    for i, s in enumerate(steps, start=1):
        for k in ("seller_entity_id", "buyer_entity_id"):
            if s.get(k) and s[k] not in known:
                errs.append(f"Ступень {i}: компания группы не найдена")
        if s["incoterms"] and s["incoterms"] not in INCOTERMS:
            errs.append(f"Ступень {i}: неизвестный базис Incoterms «{s['incoterms']}»")
        if s["currency"] not in CURRENCIES:
            errs.append(f"Ступень {i}: валюта — одна из {', '.join(CURRENCIES)}")
    if errs:
        raise HTTPException(422, "; ".join(errs))
    r.name, r.description, r.supplier_country, r.is_active = body.name.strip(), body.description, body.supplier_country, body.is_active
    r.steps.clear()
    db.flush()
    for i, s in enumerate(steps, start=1):
        r.steps.append(RouteStep(no=i, **s))


@router.get("/routes")
def routes(include_archived: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    names = _names(db)
    return [_route_payload(r, names) for r in db.scalars(select(SupplyRoute).order_by(SupplyRoute.name)).all()
            if include_archived or r.is_active]


@router.post("/routes", status_code=201)
def create_route(body: RouteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_route_editor)):
    if db.scalar(select(SupplyRoute).where(SupplyRoute.name == body.name.strip())):
        raise HTTPException(409, f"Маршрут «{body.name}» уже есть")
    r = SupplyRoute(name=body.name.strip())
    db.add(r)
    _apply_route(db, r, body)
    db.flush()
    audit(db, user.id, None, "supply_route", r.id, "create", None, body.model_dump())
    db.commit()
    return _route_payload(r, _names(db))


@router.put("/routes/{route_id}")
def update_route(route_id: str, body: RouteIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_route_editor)):
    r = db.get(SupplyRoute, route_id)
    if r is None:
        raise HTTPException(404, "Маршрут не найден")
    clash = db.scalar(select(SupplyRoute).where(SupplyRoute.name == body.name.strip(), SupplyRoute.id != route_id))
    if clash:
        raise HTTPException(409, f"Маршрут «{body.name}» уже есть")
    before = _route_payload(r, _names(db))
    _apply_route(db, r, body)
    audit(db, user.id, None, "supply_route", r.id, "update", before, body.model_dump())
    db.commit()
    db.refresh(r)
    return _route_payload(r, _names(db))


# ------------------------------------------------------------------ политика наценок

class RulesIn(BaseModel):
    default: dict[str, float] = Field(default_factory=dict)        # entity_id → %
    by_group: list[dict] = Field(default_factory=list)             # {entity_id, part_group, pct}
    by_supplier: list[dict] = Field(default_factory=list)          # {entity_id, supplier_id, pct}
    min_margin_pct: Optional[float] = None
    # ГШО через компанию группы (D-153): ставка по умолчанию и исключения по поставщику и типу машины
    gsho_default: dict[str, float] = Field(default_factory=dict)            # entity_id → %
    gsho_by_supplier: list[dict] = Field(default_factory=list)              # {entity_id, supplier_id, pct}
    gsho_by_machine_type: list[dict] = Field(default_factory=list)          # {entity_id, machine_type, pct}


class PolicyIn(BaseModel):
    rules: RulesIn = Field(default_factory=RulesIn)
    valid_from: Optional[date] = None
    reason: str = ""


def _check_rules(rules: RulesIn) -> None:
    pcts = list(rules.default.values()) + [float(r.get("pct") or 0) for r in rules.by_group + rules.by_supplier]
    if any(p < 0 or p > 300 for p in pcts):
        raise HTTPException(422, "Наценка должна быть от 0 до 300 %")
    for r in rules.by_group:
        if not r.get("entity_id") or not (r.get("part_group") or "").strip():
            raise HTTPException(422, "В исключении по группе товара укажите компанию и группу")
    for r in rules.by_supplier:
        if not r.get("entity_id") or not r.get("supplier_id"):
            raise HTTPException(422, "В исключении по поставщику укажите компанию и поставщика")


def _policy_payload(p: MarkupPolicy) -> dict:
    d = serialize(p)
    d["status_title"] = POLICY_STATUS.get(p.status, p.status)
    d["missing_approvals"] = [t for k, t in APPROVER_SLOTS.items() if k not in (p.approvals or {})]
    return d


def _approver_ids(db: Session) -> list[str]:
    return [u.id for u in db.scalars(select(User).where(User.is_active.is_(True))).all()
            if {"gd", "fd"} & set(u.roles or [])]


def current_policy(db: Session, on: Optional[date] = None) -> Optional[MarkupPolicy]:
    """Действующая на дату версия: утверждённая (или уже заменённая позже), с наибольшей датой начала не позже даты."""
    on = on or date.today()
    rows = db.scalars(select(MarkupPolicy).where(MarkupPolicy.status.in_(("approved", "superseded")))).all()
    rows = [p for p in rows if (p.valid_from or date.min) <= on]
    return max(rows, key=lambda p: ((p.valid_from or date.min), p.version_no), default=None)


@router.get("/markup-policies")
def policies(db: Session = Depends(get_db), user: CurrentUser = Depends(_markup_reader)):
    cur = current_policy(db)
    return {"current_id": cur.id if cur else None,
            "items": [_policy_payload(p) for p in db.scalars(select(MarkupPolicy).order_by(MarkupPolicy.version_no.desc())).all()]}


@router.post("/markup-policies", status_code=201)
def create_policy(body: PolicyIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_markup_reader)):
    if not user.has(Role.CD, Role.GD, Role.FD):
        raise HTTPException(403, "Черновик политики наценок готовят КД, ФД или ЗГД")
    _check_rules(body.rules)
    n = (db.scalar(select(func.max(MarkupPolicy.version_no))) or 0) + 1
    p = MarkupPolicy(version_no=n, rules=body.rules.model_dump(), valid_from=body.valid_from, reason=body.reason, created_by=user.id)
    db.add(p)
    db.flush()
    audit(db, user.id, None, "markup_policy", p.id, "create", None, {"version_no": n, "rules": p.rules})
    db.commit()
    return _policy_payload(p)


def _policy_or_404(db: Session, policy_id: str) -> MarkupPolicy:
    p = db.get(MarkupPolicy, policy_id)
    if p is None:
        raise HTTPException(404, "Версия политики не найдена")
    return p


@router.put("/markup-policies/{policy_id}")
def update_policy(policy_id: str, body: PolicyIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_markup_reader)):
    if not user.has(Role.CD, Role.GD, Role.FD):
        raise HTTPException(403, "Черновик политики наценок правят КД, ФД или ЗГД")
    p = _policy_or_404(db, policy_id)
    if p.status not in ("draft", "rejected", "on_approval"):
        raise HTTPException(409, "Утверждённую версию изменить нельзя — создайте новую")
    _check_rules(body.rules)
    before = {"rules": p.rules, "valid_from": p.valid_from, "status": p.status}
    p.rules, p.valid_from, p.reason = body.rules.model_dump(), body.valid_from, body.reason
    p.status, p.approvals, p.rejection = "draft", {}, {}      # любая правка снимает визы
    audit(db, user.id, None, "markup_policy", p.id, "update", before, {"rules": p.rules, "valid_from": p.valid_from})
    db.commit()
    return _policy_payload(p)


@router.post("/markup-policies/{policy_id}/submit")
def submit_policy(policy_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(_markup_reader)):
    p = _policy_or_404(db, policy_id)
    if p.status not in ("draft", "rejected"):
        raise HTTPException(409, "На утверждение отправляется черновик")
    p.status, p.approvals, p.rejection = "on_approval", {}, {}
    text = f"Политика наценок между компаниями группы, версия {p.version_no}, ждёт вашей визы"
    for uid in _approver_ids(db):
        notify(db, None, uid, "markup_policy", None, text)
    audit(db, user.id, None, "markup_policy", p.id, "submit", None, None)
    db.commit()
    return _policy_payload(p)


class DecisionIn(BaseModel):
    comment: str = ""


@router.post("/markup-policies/{policy_id}/approve")
def approve_policy(policy_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    slot = _approver_slot(user)
    p = _policy_or_404(db, policy_id)
    if p.status != "on_approval":
        raise HTTPException(409, "Визу ставят на версию, отправленную на утверждение")
    approvals = dict(p.approvals or {})
    if any(a.get("user_id") == user.id for a in approvals.values()):
        raise HTTPException(409, "Вы уже поставили визу на эту версию")
    now = datetime.now(timezone.utc)
    approvals[slot] = {"user_id": user.id, "name": user.display_name, "at": now.isoformat(), "comment": body.comment}
    p.approvals = approvals
    if all(k in approvals for k in APPROVER_SLOTS):
        p.status, p.approved_at = "approved", now
        p.valid_from = p.valid_from or date.today()
        # ранее утверждённые версии с той же или более ранней датой начала — заменены этой
        for old in db.scalars(select(MarkupPolicy).where(MarkupPolicy.status == "approved", MarkupPolicy.id != p.id)).all():
            if (old.valid_from or date.min) <= p.valid_from:
                old.status = "superseded"
        if p.created_by:
            notify(db, None, p.created_by, "markup_policy", None, f"Политика наценок, версия {p.version_no}, утверждена и действует с {p.valid_from:%d.%m.%Y}")
    audit(db, user.id, None, "markup_policy", p.id, f"approve_{slot}", None, {"comment": body.comment})
    db.commit()
    return _policy_payload(p)


@router.post("/markup-policies/{policy_id}/reject")
def reject_policy(policy_id: str, body: DecisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    _approver_slot(user)
    p = _policy_or_404(db, policy_id)
    if p.status != "on_approval":
        raise HTTPException(409, "Отклонить можно версию, отправленную на утверждение")
    if not body.comment.strip():
        raise HTTPException(422, "Укажите причину отказа")
    p.status, p.approvals = "rejected", {}
    p.rejection = {"user_id": user.id, "name": user.display_name, "at": datetime.now(timezone.utc).isoformat(), "comment": body.comment}
    if p.created_by:
        notify(db, None, p.created_by, "markup_policy", None, f"Политика наценок, версия {p.version_no}, возвращена: {body.comment}")
    audit(db, user.id, None, "markup_policy", p.id, "reject", None, {"comment": body.comment})
    db.commit()
    return _policy_payload(p)


# ------------------------------------------------------------------ симулятор

class SimulateIn(BaseModel):
    route_id: str
    purchase_price: float
    policy_id: Optional[str] = None            # нет — действующая версия
    rules: Optional[RulesIn] = None            # черновик прямо из формы, без сохранения
    part_group: str = ""
    supplier_id: Optional[str] = None
    client_price_rub: Optional[float] = None
    extra_costs_rub: float = 0.0
    fx: dict[str, float] = Field(default_factory=dict)   # ручные курсы поверх курсов ЦБ (напр. KGS)
    mode: str = Field("parts", pattern="^(parts|gsho)$")   # gsho — наценки на технику (D-153)
    machine_type: str = ""


def latest_fx(db: Session) -> dict[str, float]:
    out: dict[str, float] = {}
    for cur in CURRENCIES:
        if cur == "RUB":
            continue
        row = db.scalars(select(FxRate).where(FxRate.currency == cur).order_by(FxRate.on.desc())).first()
        if row:
            out[cur] = row.rate
    return out


@router.post("/markup-policies/simulate")
def simulate_policy(body: SimulateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(_markup_reader)):
    r = db.get(SupplyRoute, body.route_id)
    if r is None:
        raise HTTPException(404, "Маршрут не найден")
    if body.rules is not None:
        rules, policy = body.rules.model_dump(), None
    else:
        policy = _policy_or_404(db, body.policy_id) if body.policy_id else current_policy(db)
        rules = policy.rules if policy else {}
    fx = {**latest_fx(db), **{k: v for k, v in body.fx.items() if v}}
    steps = [{"seller_entity_id": s.seller_entity_id, "buyer_entity_id": s.buyer_entity_id, "currency": s.currency,
              "incoterms": s.incoterms} for s in r.steps]
    try:
        res = simulate(body.purchase_price, steps, rules, fx=fx, part_group=body.part_group or None, supplier_id=body.supplier_id,
                       client_price_rub=body.client_price_rub, extra_costs_rub=body.extra_costs_rub, entity_names=_names(db),
                       mode=body.mode, machine_type=body.machine_type or None)
    except ChainError as e:
        raise HTTPException(422, str(e))
    res["fx"] = fx
    res["policy_version"] = policy.version_no if policy else None
    return res


# ------------------------------------------------------------------ правила внутригрупповых цепочек по умолчанию (D-164)

class ChainRulesIn(BaseModel):
    rules: list[dict] = Field(default_factory=list)


@router.get("/intragroup-rules")
def chain_rules_get(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Через какую компанию группы идёт закупка у поставщика: по виду сделки, стране или поставщику. Ведут ФД и ЗГД."""
    from ..services import chain_rules as cr
    if not user.has(Role.CD, Role.FD, Role.GD, Role.LOGISTICIAN, Role.ADMIN):
        raise HTTPException(403, "Правила цепочек видят КД, ФД, ЗГД и логист")
    rows = cr.rules(db)
    names = {f.id: (f.short_name or f.name) for f in db.scalars(select(Factory)).all()}
    routes = {r.id: r.name for r in db.scalars(select(SupplyRoute)).all()}
    return {"rules": [{**r, "supplier_name": names.get(r["supplier_id"], ""), "route_name": routes.get(r["route_id"] or "", "")} for r in rows],
            "kinds": cr.KINDS, "can_edit": user.has(Role.FD, Role.GD), "countries": _rule_countries(db)}


def _rule_countries(db: Session) -> list[str]:
    """Страны для правил: страны поставщиков из справочника, затем остальные из списка стран."""
    from ..services.countries import country_names, normalize_country
    used = sorted({normalize_country(f.country) for f in db.scalars(select(Factory)).all() if f.country} - {""})
    return used + [c for c in country_names() if c not in used]


@router.put("/intragroup-rules")
def chain_rules_put(body: ChainRulesIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from ..services import chain_rules as cr
    if not user.has(Role.FD, Role.GD):
        raise HTTPException(403, "Правила цепочек утверждают ФД или ЗГД")
    before = cr.rules(db)
    rows = cr.save(db, body.rules, user.id)
    audit(db, user.id, None, "settings", cr.KEY, "update", before=before, after=rows)
    db.commit()
    return chain_rules_get(db, user)
