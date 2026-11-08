"""Реестр договоров (юридический блок): карточка, условия оплаты по событиям, сроки исполнения, неустойки, файлы, доп. соглашения.
Завести договор может любой сотрудник (становится куратором); подписанный договор меняется только доп. соглашением."""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.config import get_settings
from ..core.files import file_response
from ..core.roles import Role
from ..db import get_db
from ..models import Client, Contract, Deal, Factory, LegalEntity
from ..services import legal_contracts as svc
from ..services.deal_service import audit
from .common import can_see_deal

router = APIRouter(prefix="/api", tags=["legal"])

ALL_SEE = (Role.LAWYER, Role.GD, Role.CD, Role.FD, Role.ADMIN, Role.OBSERVER)
EDIT_ROLES = (Role.LAWYER, Role.GD, Role.CD, Role.FD)


def _can_see(db: Session, user: CurrentUser, c: Contract) -> bool:
    if user.has(*ALL_SEE) or c.curator_id == user.id or c.created_by == user.id:
        return True
    if c.deal_id:
        d = db.get(Deal, c.deal_id)
        return bool(d and can_see_deal(user, d))
    return False


def _can_edit(user: CurrentUser, c: Contract) -> bool:
    if c.status in svc.LOCKED:
        return user.has(Role.LAWYER, Role.GD)          # подписанный: только служебные поля (файлы, оригинал, примечание); условия — доп. соглашением
    return user.has(*EDIT_ROLES) or c.curator_id == user.id or c.created_by == user.id


def _out(db: Session, user: CurrentUser, c: Contract) -> dict:
    """Карточка + что может сделать текущий пользователь (визы, регистрация подписанного, подпись)."""
    mine = c.curator_id == user.id or c.created_by == user.id
    return {**svc.payload(db, c), "can_edit": _can_edit(user, c), "can_edit_number": user.has(Role.GD, Role.CD),
            "my_steps": [x["title"] for x in svc.user_steps(c, user.roles)], "can_register": user.has(Role.LAWYER, Role.GD),
            "can_sign": user.has(Role.LAWYER, Role.GD, Role.CD, Role.FD) or mine}


def _get(db: Session, cid: str, user: CurrentUser) -> Contract:
    c = db.get(Contract, cid)
    if c is None or not _can_see(db, user, c):
        raise HTTPException(404, "Договор не найден")
    return c


def _files_dir(cid: str) -> str:
    d = os.path.join(get_settings().storage_local_root, "_legal", "contracts", cid)
    os.makedirs(d, exist_ok=True)
    return d


_imported = {"done": False}


class ContractIn(BaseModel):
    doc_type: Optional[str] = None
    parent_id: Optional[str] = None
    kind: Optional[str] = None
    direction: Optional[str] = None
    cp_number: Optional[str] = None
    title: Optional[str] = None
    subject: Optional[str] = None
    own_entity_id: Optional[str] = None
    own_profile_id: Optional[str] = None
    own_bank_id: Optional[str] = None
    signatory: Optional[dict] = None
    cp_kind: Optional[str] = None
    cp_id: Optional[str] = None
    cp_name: Optional[str] = None
    cp_inn: Optional[str] = None
    cp_profile_id: Optional[str] = None
    deal_id: Optional[str] = None
    deal_ids: Optional[list[str]] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    vat_mode: Optional[str] = None
    vat_rate: Optional[float] = None
    price_type: Optional[str] = None
    fx_clause: Optional[str] = None
    signed_on: Optional[date] = None
    effective_from: Optional[date] = None
    valid_to: Optional[date] = None
    auto_renew: Optional[bool] = None
    payment_terms: Optional[list[dict]] = None
    performance_terms: Optional[list[dict]] = None
    penalties: Optional[list[dict]] = None
    liability_cap_pct: Optional[float] = None
    security: Optional[dict] = None
    warranty: Optional[dict] = None
    law: Optional[dict] = None
    sign_method: Optional[str] = None
    edo: Optional[dict] = None
    original: Optional[dict] = None
    notes: Optional[str] = None
    curator_id: Optional[str] = None
    lawyer_id: Optional[str] = None
    client_order: Optional[bool] = None


TERMS_FIELDS = {"client_order", "kind", "direction", "amount", "currency", "vat_mode", "vat_rate", "price_type", "fx_clause", "payment_terms", "performance_terms", "penalties", "liability_cap_pct",
                "security", "warranty", "law", "own_entity_id", "cp_id", "cp_name", "cp_kind", "subject", "valid_to", "effective_from", "auto_renew"}
LOCKED_OK = {"original", "notes", "edo", "lawyer_id", "curator_id", "deal_ids", "title"}   # можно править у подписанного договора
NUMBER_FIELDS = {"cp_number", "signed_on"}                                                   # у подписанного — только ЗГД или КД


@router.get("/legal/contracts/catalog")
def contracts_catalog(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return {**svc.catalog(), "penalty_defaults": svc.penalty_defaults(db)}


@router.get("/legal/contracts")
def contracts_registry(kind: str = "", status: str = "", direction: str = "", entity_id: str = "", cp_id: str = "", deal_id: str = "", q: str = "", include_children: bool = False,
                       db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Реестр договоров группы: фильтры, сводка (действуют, проекты, истекают, без скана, без условий оплаты)."""
    if not _imported["done"]:
        if svc.import_from_deals(db):
            db.commit()
        _imported["done"] = True
    stmt = select(Contract).order_by(Contract.created_at.desc())
    if not include_children:
        stmt = stmt.where(Contract.parent_id.is_(None))
    for col, val in ((Contract.kind, kind), (Contract.status, status), (Contract.direction, direction), (Contract.own_entity_id, entity_id), (Contract.cp_id, cp_id), (Contract.deal_id, deal_id)):
        if val:
            stmt = stmt.where(col == val)
    names = svc._names(db)
    ents = {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}
    rows = [c for c in db.scalars(stmt).all() if _can_see(db, user, c)]
    items = [svc.payload(db, c, names, ents, full=False) for c in rows]
    if q:
        ql = q.lower()
        items = [x for x in items if ql in f"{x['number']} {x['cp_number']} {x['cp_name']} {x['subject']} {x['title']} {x['deal_number']}".lower()]
    pen_in = pen_out = 0.0
    overdue = 0
    by_id = {c.id: c for c in rows}
    for it in items:
        c = by_id[it["id"]]
        if c.status != "active" or c.direction == "none":
            continue
        ob = svc.obligations(db, c)
        t = ob["totals"]
        it["exec"] = {"overdue": t["overdue_payments"] + t["overdue_performance"], "pen_in": t["receivable"] + t["receivable_accruing"], "pen_out": t["payable"] + t["payable_accruing"]}
        overdue += 1 if it["exec"]["overdue"] else 0
        pen_in += svc.to_rub(db, it["exec"]["pen_in"], c.currency) or 0
        pen_out += svc.to_rub(db, it["exec"]["pen_out"], c.currency) or 0
    summary = {"total": len(items), "overdue": overdue, "penalties_in_rub": round(pen_in, 2), "penalties_out_rub": round(pen_out, 2), "active": sum(1 for x in items if x["status"] == "active"), "draft": sum(1 for x in items if x["status"] not in svc.LOCKED and x["status"] != "cancelled"),
               "expiring": sum(1 for x in items if x["status"] == "active" and x["days_to_expiry"] is not None and 0 <= x["days_to_expiry"] <= 30),
               "no_scan": sum(1 for x in items if x["status"] in svc.LOCKED and not x["has_scan"]),
               "no_terms": sum(1 for x in items if x["direction"] in ("income", "expense") and not x["terms_text"])}
    return {"items": items, "summary": summary, "entities": [{"id": k, "name": v} for k, v in ents.items()],
            "can_create": True, "can_manage": user.has(*EDIT_ROLES)}


@router.get("/deals/{deal_id}/contracts")
def deal_contracts(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    d = db.get(Deal, deal_id)
    if d is None or not can_see_deal(user, d):
        raise HTTPException(404, "Сделка не найдена")
    rows = db.scalars(select(Contract).where(Contract.deal_id == deal_id).order_by(Contract.created_at)).all()
    names = svc._names(db)
    return {"items": [svc.payload(db, c, names, full=False) for c in rows]}


def _apply(c: Contract, data: dict, db: Session) -> dict:
    changes = {}
    if "kind" in data and data["kind"] not in svc.KINDS:
        raise HTTPException(422, "Неизвестный вид договора")
    if "direction" in data and data["direction"] not in svc.DIRECTIONS:
        raise HTTPException(422, "Направление: income | expense | none")
    if "sign_method" in data and data["sign_method"] not in ("paper", "edo"):
        raise HTTPException(422, "Способ подписания: paper | edo")
    for k in ("payment_terms", "performance_terms", "penalties"):
        if k in data:
            data[k] = [dict(x, no=i + 1) if k == "payment_terms" else dict(x) for i, x in enumerate(data[k] or [])]
    for p in data.get("penalties") or []:
        if p.get("reason") not in svc.PENALTY_REASONS or p.get("rate_kind") not in svc.RATE_KINDS or p.get("payer") not in svc.PENALTY_PAYERS:
            raise HTTPException(422, "Неустойка: укажите кто платит, за что и вид ставки")
    for l in data.get("payment_terms") or []:
        if l.get("event") and l["event"] not in svc.EVENTS:
            raise HTTPException(422, f"Неизвестное событие оплаты: {l['event']}")
    if data.get("cp_kind") == "client" and data.get("cp_id"):
        cl = db.get(Client, data["cp_id"])
        if cl is None:
            raise HTTPException(404, "Клиент не найден")
        data.setdefault("cp_inn", cl.inn)
    if data.get("cp_kind") == "factory" and data.get("cp_id") and db.get(Factory, data["cp_id"]) is None:
        raise HTTPException(404, "Поставщик не найден")
    for k, v in data.items():
        old = getattr(c, k)
        if isinstance(v, str):
            v = v.strip()
        if k == "currency" and v:
            v = v.upper()
        if old != v:
            changes[k] = {"from": old.isoformat() if isinstance(old, date) else old, "to": v.isoformat() if isinstance(v, date) else v} if k not in ("payment_terms", "penalties", "performance_terms") else "изменено"
            setattr(c, k, v)
    return changes


@router.post("/legal/contracts", status_code=201)
def create_contract(body: ContractIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Новый договор (проект). Заводит любой сотрудник — он становится куратором; номер присваивается сразу по компании и году."""
    data = body.model_dump(exclude_none=True)
    kind = data.get("kind") or "other"
    if kind not in svc.KINDS:
        raise HTTPException(422, "Неизвестный вид договора")
    direction = data.get("direction") or svc.KINDS[kind]["direction"]
    parent = None
    if data.get("parent_id"):
        parent = _get(db, data["parent_id"], user)
        for k in ("own_entity_id", "cp_kind", "cp_id", "cp_name", "cp_inn", "deal_id", "currency"):
            data.setdefault(k, getattr(parent, k))
        kind, direction = parent.kind, parent.direction
        if data.get("doc_type") not in ("amendment", "specification"):
            data["doc_type"] = "amendment"
    if data.get("deal_id"):
        d = db.get(Deal, data["deal_id"])
        if d is None or not can_see_deal(user, d):
            raise HTTPException(404, "Сделка не найдена")
        data.setdefault("deal_ids", [d.id])
    ent = db.get(LegalEntity, data["own_entity_id"]) if data.get("own_entity_id") else None
    num, seq = svc.next_number(db, ent, direction, date.today())
    if parent:
        k = db.scalars(select(Contract).where(Contract.parent_id == parent.id)).all()
        num, seq = f"{parent.number}/{'ДС' if data['doc_type'] == 'amendment' else 'СП'}-{len(k) + 1}", None
    c = Contract(kind=kind, direction=direction, number=num, seq=seq, created_by=user.id, curator_id=data.pop("curator_id", None) or user.id, status="draft", source="manual",
                 doc_type=data.pop("doc_type", None) or "contract", parent_id=data.pop("parent_id", None))
    if "penalties" not in data and not parent:
        data["penalties"], cap = svc.default_penalties(db, direction)
        data.setdefault("liability_cap_pct", cap)
    data.setdefault("law", {"claim_days": 30, "pre_trial": True})
    data.pop("kind", None)
    data.pop("direction", None)
    _apply(c, data, db)
    svc.hist(c, user.id, "created", "проект" + (f" к договору {parent.number}" if parent else ""))
    db.add(c)
    db.flush()
    audit(db, user.id, c.deal_id, "contract", c.id, "create", after={"number": c.number, "kind": c.kind, "cp": svc.cp_display(db, c)})
    db.commit()
    return _out(db, user, c)


@router.get("/legal/contracts/{cid}")
def get_contract(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = _get(db, cid, user)
    return _out(db, user, c)


@router.put("/legal/contracts/{cid}")
def update_contract(cid: str, body: ContractIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = _get(db, cid, user)
    data = body.model_dump(exclude_unset=True)
    for k in ("doc_type", "parent_id"):
        data.pop(k, None)
    only_number = bool(data) and all(k in NUMBER_FIELDS for k in data)
    if not (_can_edit(user, c) or (c.status in svc.LOCKED and only_number and user.has(Role.CD))):
        raise HTTPException(403, "Править договор могут куратор, юрист, ФД, КД и ЗГД; подписанный — юрист и ЗГД, условия — только доп. соглашением")
    if c.status in svc.LOCKED:
        bad = [k for k in data if k not in LOCKED_OK and k not in NUMBER_FIELDS and getattr(c, k) != data[k]]
        if bad:
            raise HTTPException(409, "Договор подписан — условия меняются доп. соглашением")
        num = [k for k in data if k in NUMBER_FIELDS and getattr(c, k) != data[k]]
        if num and not user.has(Role.GD, Role.CD):
            raise HTTPException(403, "Номер и дату подписанного договора меняет только ЗГД или КД")
    in_route = c.status in svc.IN_ROUTE
    if in_route:
        terms = [k for k in data if k in TERMS_FIELDS and getattr(c, k) != data[k]]
        if terms and not user.has(Role.LAWYER, Role.GD):
            raise HTTPException(409, "Договор на согласовании: условия меняют юрист или ЗГД. Чтобы поправить самому — верните договор на доработку")
    changes = _apply(c, data, db)
    if in_route and any(k in TERMS_FIELDS for k in changes):
        if svc.reset_visas(db, c, user.id, "изменены условия: " + ", ".join(k for k in changes if k in TERMS_FIELDS)):
            svc.notify_pending(db, c)
    if changes:
        svc.hist(c, user.id, "edited", ", ".join(changes.keys()), {k: v for k, v in changes.items() if k not in TERMS_FIELDS or k in ("amount", "currency")})
        audit(db, user.id, c.deal_id, "contract", c.id, "update", after={"fields": list(changes.keys())})
    db.commit()
    return _out(db, user, c)


@router.post("/legal/contracts/{cid}/pull-terms")
def pull_terms(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Перенести цену и условия оплаты из утверждённого расчёта сделки (клиент — выигранное предложение; завод — условия позиции)."""
    c = _get(db, cid, user)
    if not _can_edit(user, c) or c.status in svc.LOCKED:
        raise HTTPException(409, "Условия подписанного договора меняются доп. соглашением")
    if not c.deal_id:
        raise HTTPException(409, "Договор не привязан к сделке")
    deal = db.get(Deal, c.deal_id)
    if c.direction == "income":
        lines, amount, cur = svc.deal_client_terms(deal)
    else:
        lines, amount, cur = svc.deal_factory_terms(deal, c.cp_id or "")
    c.payment_terms, c.amount, c.currency = lines, amount, cur
    svc.hist(c, user.id, "pull_terms", f"цена и условия оплаты из расчёта сделки {deal.number}")
    db.commit()
    return _out(db, user, c)


class StatusIn(BaseModel):
    status: str
    comment: str = ""


@router.post("/legal/contracts/{cid}/status")
def set_status(cid: str, body: StatusIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Передать на подпись (после согласования), отметить подписанным (дата, скан, при ЭДО — файл ЭДО), исполнен, расторгнут, аннулировать проект.
    Уже подписанный вне системы договор юрист или ЗГД регистрирует сразу, минуя маршрут."""
    c = _get(db, cid, user)
    to = body.status
    allowed = {"draft": {"active", "cancelled"}, "legal_review": {"active", "cancelled"}, "approval": {"active", "cancelled"}, "approved": {"signing", "active", "cancelled"},
               "signing": {"active", "cancelled"}, "active": {"closed", "terminated"}}
    if to not in allowed.get(c.status, set()):
        raise HTTPException(409, f"Из статуса «{svc.STATUSES.get(c.status)}» нельзя перейти в «{svc.STATUSES.get(to, to)}»")
    curator = c.curator_id == user.id or c.created_by == user.id
    signers = user.has(Role.LAWYER, Role.GD, Role.CD, Role.FD) or curator
    if to == "cancelled":
        if not (_can_edit(user, c)):
            raise HTTPException(403, "Аннулировать проект могут куратор, юрист, ФД, КД и ЗГД")
    elif to == "signing":
        if not signers:
            raise HTTPException(403, "Передают на подпись куратор, юрист, ФД, КД или ЗГД")
    elif to == "active" and c.status in ("draft", "legal_review", "approval"):
        if not user.has(Role.LAWYER, Role.GD):
            raise HTTPException(403, "Договор сначала проходит согласование. Уже подписанный вне системы договор регистрирует юрист или ЗГД")
    elif to == "active":
        if not signers:
            raise HTTPException(403, "Отмечают куратор, юрист, ФД, КД или ЗГД")
    elif not user.has(Role.LAWYER, Role.GD, Role.CD, Role.FD):
        raise HTTPException(403, "Отмечают юрист, ФД, КД или ЗГД")
    if to == "active":
        chk = svc.check(c)
        if chk["errors"]:
            raise HTTPException(422, {"message": "Договор заполнен не полностью", "errors": chk["errors"]})
        if not c.signed_on:
            raise HTTPException(422, {"message": "Укажите дату подписания", "errors": ["Не указана дата подписания"]})
        if not any(f.get("kind") == "signed_scan" for f in (c.files or [])):
            raise HTTPException(422, {"message": "Загрузите подписанный скан", "errors": ["Нет подписанного скана — он обязателен и при подписании в ЭДО"]})
        if c.sign_method == "edo" and not any(f.get("kind") == "edo" for f in (c.files or [])):
            raise HTTPException(422, {"message": "Загрузите файл ЭДО", "errors": ["Подписание в ЭДО: загрузите подписанный файл с ЭП или квитанции оператора"]})
    if to in ("terminated", "cancelled") and len(body.comment.strip()) < 3:
        raise HTTPException(422, "Укажите причину")
    before = c.status
    c.status = to
    note = ""
    if to == "active":
        if before in ("draft", "legal_review", "approval"):
            note = " (зарегистрирован подписанный, без маршрута согласования)"
        svc.apply_signed_to_deal(db, c)
        changed = svc.apply_amendment(db, c, user.id)
        if changed:
            note += " · изменения внесены в основной договор: " + ", ".join(changed)
        svc.notify_users(db, c, [c.curator_id or ""] if c.curator_id != user.id else [], f"Договор {c.number} ({svc.cp_display(db, c)}) подписан и действует")
    if to == "signing":
        svc.notify_users(db, c, [c.curator_id or ""] if c.curator_id != user.id else [], f"Договор {c.number} передан на подпись")
    svc.hist(c, user.id, "status", f"{svc.STATUSES.get(before)} → {svc.STATUSES.get(to)}" + note + (f": {body.comment.strip()}" if body.comment.strip() else ""))
    audit(db, user.id, c.deal_id, "contract", c.id, "status", {"status": before}, {"status": to, "comment": body.comment})
    db.commit()
    return _out(db, user, c)


class CommentIn(BaseModel):
    comment: str = ""


class VisaIn(BaseModel):
    decision: str = "approve"     # approve | reject
    comment: str = ""


@router.post("/legal/contracts/{cid}/submit")
def submit(cid: str, body: Optional[CommentIn] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Отправить проект на согласование: юрист → затем параллельно ФД, КД (закупки под заказы клиентов), ЗГД (дороже порога)."""
    c = _get(db, cid, user)
    if c.status != "draft":
        raise HTTPException(409, "На согласование отправляется проект")
    if not _can_edit(user, c):
        raise HTTPException(403, "Отправляет куратор договора, юрист, ФД, КД или ЗГД")
    chk = svc.check(c)
    if chk["errors"]:
        raise HTTPException(422, {"message": "Договор заполнен не полностью", "errors": chk["errors"]})
    svc.start_route(db, c, user.id)
    comment = (body.comment.strip() if body else "")
    svc.hist(c, user.id, "submitted", "на согласование: " + " → ".join(x["title"] for x in c.approval["steps"]) + (f" · {comment}" if comment else ""))
    svc.notify_pending(db, c)
    audit(db, user.id, c.deal_id, "contract", c.id, "submit", after={"route": [x["code"] for x in c.approval["steps"]]})
    db.commit()
    return _out(db, user, c)


@router.post("/legal/contracts/{cid}/visa")
def visa(cid: str, body: VisaIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = _get(db, cid, user)
    mine = svc.user_steps(c, set(user.roles))
    if not mine:
        wait = ", ".join(x["title"] for x in svc.pending_steps(c))
        raise HTTPException(403, f"Сейчас визу ставит: {wait}" if wait else "Договор не на согласовании")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "Решение: approve | reject")
    if body.decision == "reject" and len(body.comment.strip()) < 3:
        raise HTTPException(422, "Укажите, что исправить")
    now = datetime.now(timezone.utc).isoformat()
    codes = {x["code"] for x in mine}
    a = dict(c.approval)
    a["steps"] = [{**x, "decision": body.decision, "by": user.id, "at": now, "comment": body.comment.strip()} if x["code"] in codes else x for x in a["steps"]]
    c.approval = a
    titles = ", ".join(svc.STEP_TITLES[x] for x in codes)
    if body.decision == "reject":
        c.status = "draft"
        svc.hist(c, user.id, "visa", f"{titles}: на доработку — {body.comment.strip()}")
        svc.notify_users(db, c, [c.curator_id or "", a.get("submitted_by") or ""], f"Договор {c.number} возвращён на доработку ({titles}): {body.comment.strip()}")
    else:
        svc.hist(c, user.id, "visa", f"{titles}: согласовано" + (f" — {body.comment.strip()}" if body.comment.strip() else ""))
        if not svc.pending_steps(c):
            c.status = "approved"
            svc.notify_users(db, c, [c.curator_id or "", a.get("submitted_by") or ""], f"Договор {c.number} ({svc.cp_display(db, c)}) согласован — можно передавать на подпись")
        elif c.status == "legal_review" and "lawyer" in codes:
            c.status = "approval"
            svc.notify_pending(db, c)
    audit(db, user.id, c.deal_id, "contract", c.id, "visa", after={"steps": sorted(codes), "decision": body.decision, "comment": body.comment})
    db.commit()
    return _out(db, user, c)


@router.post("/legal/contracts/{cid}/recall")
def recall(cid: str, body: Optional[CommentIn] = None, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Вернуть с согласования / подписания в проект (куратор, юрист, ЗГД) — например, чтобы поправить условия самому."""
    c = _get(db, cid, user)
    if c.status not in svc.IN_ROUTE:
        raise HTTPException(409, "Договор не на согласовании")
    if not (_can_edit(user, c)):
        raise HTTPException(403)
    before = c.status
    c.status = "draft"
    comment = (body.comment.strip() if body else "")
    svc.hist(c, user.id, "recalled", f"{svc.STATUSES.get(before)} → проект" + (f": {comment}" if comment else ""))
    db.commit()
    return _out(db, user, c)


class FactIn(BaseModel):
    kind: str                         # pay | perf | pen
    key: str                          # pay: № платежа; perf: № срока; pen: ключ неустойки
    paid_on: Optional[date] = None
    done_on: Optional[date] = None
    amount: Optional[float] = None
    status: Optional[str] = None      # pen: claimed | recognized | paid | waived
    on: Optional[date] = None
    due_on: Optional[date] = None     # pen: ожидаемая дата оплаты неустойки
    comment: str = ""
    clear: bool = False


@router.get("/legal/contracts/{cid}/obligations")
def contract_obligations(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return svc.obligations(db, _get(db, cid, user))


@router.post("/legal/contracts/{cid}/facts")
def contract_fact(cid: str, body: FactIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Исполнение договора: оплата платежа (ФД, юрист, ЗГД, куратор), исполнение срока (куратор, юрист, КД, ФД, ЗГД),
    решение по неустойке — предъявлена, признана, оплачена, не взыскиваем (юрист, ФД, ЗГД; «не взыскиваем» — с причиной)."""
    c = _get(db, cid, user)
    if c.status not in svc.LOCKED:
        raise HTTPException(409, "Исполнение отмечается по подписанному договору")
    curator = c.curator_id == user.id or c.created_by == user.id
    if body.kind == "pay":
        if not (user.has(Role.FD, Role.LAWYER, Role.GD) or curator):
            raise HTTPException(403, "Оплату отмечают ФД, юрист, ЗГД или куратор")
        if not body.clear and not body.paid_on:
            raise HTTPException(422, "Укажите дату оплаты")
        data, txt = {"paid_on": body.paid_on.isoformat() if body.paid_on else None, "amount": body.amount, "comment": body.comment.strip(), "by": user.id, "clear": body.clear}, "оплата"
    elif body.kind == "perf":
        if not (user.has(Role.FD, Role.LAWYER, Role.GD, Role.CD) or curator):
            raise HTTPException(403, "Исполнение отмечают куратор, юрист, КД, ФД или ЗГД")
        if not body.clear and not body.done_on:
            raise HTTPException(422, "Укажите дату исполнения")
        data, txt = {"done_on": body.done_on.isoformat() if body.done_on else None, "comment": body.comment.strip(), "by": user.id, "clear": body.clear}, "исполнение"
    elif body.kind == "pen":
        if not user.has(Role.FD, Role.LAWYER, Role.GD):
            raise HTTPException(403, "Решение по неустойке принимают юрист, ФД или ЗГД")
        if not body.clear and body.status not in svc.PEN_MANUAL:
            raise HTTPException(422, "Статус неустойки: " + ", ".join(svc.PEN_MANUAL))
        if body.status == "waived" and len(body.comment.strip()) < 3:
            raise HTTPException(422, "Укажите, почему неустойку не взыскиваем")
        if body.amount is not None and body.amount < 0:
            raise HTTPException(422, "Сумма не может быть отрицательной")
        data = {"status": body.status, "amount": body.amount, "on": (body.on or date.today()).isoformat(), "due_on": body.due_on.isoformat() if body.due_on else None,
                "comment": body.comment.strip(), "by": user.id, "clear": body.clear}
        txt = "неустойка: " + (svc.PEN_STATUS.get(body.status or "", "") if not body.clear else "решение снято")
    else:
        raise HTTPException(422, "kind: pay | perf | pen")
    svc.set_fact(c, body.kind, body.key, data)
    svc.hist(c, user.id, "fact", f"{txt} · {body.key}" + (" — снято" if body.clear else "") + (f" · {body.comment.strip()}" if body.comment.strip() else ""))
    audit(db, user.id, c.deal_id, "contract", c.id, "fact", after=body.model_dump(mode="json"))
    db.commit()
    return _out(db, user, c)


@router.get("/legal/contracts-inbox")
def inbox(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Мои договоры к действию: визы, на подпись, без скана, истекающие (для рабочего места)."""
    return svc.inbox(db, user)


@router.post("/legal/contracts/{cid}/files", status_code=201)
async def upload_file(cid: str, file: UploadFile = File(...), kind: str = Form("draft"), note: str = Form(""), db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = _get(db, cid, user)
    if not (_can_edit(user, c) or c.curator_id == user.id or user.has(Role.LAWYER, Role.GD)):
        raise HTTPException(403, "Загружают куратор, юрист, ФД, КД и ЗГД")
    if kind not in svc.FILE_KINDS:
        raise HTTPException(422, "Вид файла: " + ", ".join(svc.FILE_KINDS))
    name = os.path.basename(file.filename or "file")
    if os.path.splitext(name)[1].lower() not in (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".docx", ".doc", ".xlsx", ".zip", ".sig", ".xml", ".p7s"):
        raise HTTPException(422, "Файл: PDF, JPG, PNG, TIFF, DOCX, XLSX, ZIP или файлы ЭДО (SIG, XML, P7S)")
    fid = uuid.uuid4().hex
    dst = os.path.join(_files_dir(c.id), f"{fid}{os.path.splitext(name)[1].lower()}")
    with open(dst, "wb") as f:
        f.write(await file.read())
    version = sum(1 for x in (c.files or []) if x.get("kind") == kind) + 1
    c.files = list(c.files or []) + [{"id": fid, "kind": kind, "name": name, "size": os.path.getsize(dst), "version": version, "uploaded_by": user.id,
                                     "uploaded_at": datetime.now(timezone.utc).isoformat(), "note": note.strip()}]
    svc.hist(c, user.id, "file", f"{svc.FILE_KINDS[kind]} v{version}: {name}")
    audit(db, user.id, c.deal_id, "contract", c.id, "file", after={"kind": kind, "file": name})
    db.commit()
    return _out(db, user, c)


@router.get("/legal/contracts/{cid}/files/{fid}")
def download_file(cid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = _get(db, cid, user)
    f = next((x for x in (c.files or []) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404, "Файл не найден")
    d = _files_dir(c.id)
    path = next((os.path.join(d, x) for x in os.listdir(d) if x.startswith(fid)), None)
    if not path:
        raise HTTPException(404, "Файл не найден на сервере")
    return file_response(path, f["name"], inline=True)


@router.delete("/legal/contracts/{cid}/files/{fid}")
def delete_file(cid: str, fid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    c = _get(db, cid, user)
    f = next((x for x in (c.files or []) if x["id"] == fid), None)
    if f is None:
        raise HTTPException(404)
    if f["kind"] in ("signed_scan", "edo") and c.status in svc.LOCKED:
        raise HTTPException(409, "Подписанный экземпляр не удаляется — загрузите новую версию с причиной")
    if not _can_edit(user, c):
        raise HTTPException(403)
    c.files = [x for x in c.files if x["id"] != fid]
    d = _files_dir(c.id)
    for x in os.listdir(d):
        if x.startswith(fid):
            os.remove(os.path.join(d, x))
    svc.hist(c, user.id, "file_removed", f["name"])
    db.commit()
    return _out(db, user, c)


@router.post("/legal/contracts/{cid}/check")
def check_contract(cid: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    return svc.check(_get(db, cid, user))

