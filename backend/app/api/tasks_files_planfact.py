"""Задачи и доска, файлы хранилища (с проверкой роли), план vs факт."""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc.types import COST_ITEMS
from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.config import get_settings
from ..core.roles import Role, filter_payload
from ..core.workflow import DealStatus, STATUS_TITLES, overdue
from ..db import get_db
from ..models import Attachment, Deal, DealVersion, PlanFactEntry, Task
from ..services import settings_service
from ..services.deal_service import audit, notify
from .common import can_see_deal, get_deal_or_404, serialize

router = APIRouter(prefix="/api", tags=["tasks-files-planfact"])


# ------------------------------------------------------------------ задачи

@router.get("/tasks/my")
def my_tasks(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(Task).where(Task.done_at.is_(None)).order_by(Task.due)).all()
    today = date.today()
    out = []
    for t in rows:
        if t.assignee_id == user.id or (t.assignee_id is None and Role(t.role) in user.roles):
            deal = db.get(Deal, t.deal_id)
            d = serialize(t)
            d.update({"deal_number": deal.number if deal else None, "deal_status": deal.status.value if deal else None, "overdue": overdue(t.due, today)})
            out.append(d)
    return out


@router.get("/board")
def board(db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.CD, Role.FD, Role.GD, Role.OBSERVER))):
    """Доска сделок для руководства: по статусам, с просрочками."""
    today = date.today()
    deals = [d for d in db.scalars(select(Deal).order_by(Deal.updated_at.desc())).all() if can_see_deal(user, d)]
    tasks = db.scalars(select(Task).where(Task.done_at.is_(None))).all()
    by_deal: dict[str, list] = {}
    for t in tasks:
        by_deal.setdefault(t.deal_id, []).append({"title": t.title, "role": t.role, "due": t.due.isoformat(), "overdue": overdue(t.due, today)})
    columns = {s.value: {"title": STATUS_TITLES[s], "deals": []} for s in DealStatus}
    for d in deals:
        columns[d.status.value]["deals"].append({"id": d.id, "number": d.number, "client": d.client.name if d.client else None, "site": d.site,
                                                 "priority": d.priority, "tasks": by_deal.get(d.id, []),
                                                 "overdue": any(t["overdue"] for t in by_deal.get(d.id, []))})
    return columns


@router.post("/tasks/check-overdue")
def check_overdue(db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.ADMIN, Role.GD))):
    """Запускается планировщиком: просрочка → уведомление исполнителю и КД (Outlook + WeCom)."""
    today = date.today()
    n = 0
    for t in db.scalars(select(Task).where(Task.done_at.is_(None), Task.overdue_notified.is_(False))).all():
        if overdue(t.due, today):
            deal = db.get(Deal, t.deal_id)
            notify(db, deal, t.assignee_id, "task_overdue", ["outlook", "wecom"], f"{deal.number}: просрочена задача «{t.title}»")
            from ..models import User
            for u in db.scalars(select(User).where(User.is_active.is_(True))).all():
                if "cd" in (u.roles or []):
                    notify(db, deal, u.id, "task_overdue", ["outlook", "wecom"], f"{deal.number}: просрочена задача «{t.title}» ({t.role})")
            t.overdue_notified = True
            n += 1
    db.commit()
    return {"notified": n}


# ------------------------------------------------------------------ файлы (локальное хранилище)

@router.get("/files/{path:path}")
def get_file(path: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    s = get_settings()
    if s.storage_provider != "local":
        raise HTTPException(404, "Файлы выдаются ссылками хранилища")
    att = db.scalar(select(Attachment).where(Attachment.storage_path == path))
    ver = None if att else db.scalar(select(DealVersion).where((DealVersion.excel_path == path) | (DealVersion.excel_manager_path == path) | (DealVersion.kp_path == path)))
    if att is None and ver is None:
        raise HTTPException(404)
    deal = db.get(Deal, att.deal_id if att else ver.deal_id)
    if not can_see_deal(user, deal):
        raise HTTPException(404)
    restricted = (att and att.folder == "04_cost") or (ver and path == ver.excel_path)
    if restricted and not user.has(Role.CD, Role.FD, Role.GD):
        raise HTTPException(403, "Папка «04 Расчёт себестоимости» недоступна для роли")
    full = os.path.join(s.storage_local_root, path)
    if not os.path.isfile(full):
        raise HTTPException(404)
    return FileResponse(full, filename=os.path.basename(full))


# ------------------------------------------------------------------ план vs факт

class FactIn(BaseModel):
    kind: str                       # cost_item | payment_in | payment_out | delivery | fx
    cost_item_code: Optional[str] = None
    position_id: Optional[str] = None
    ref: str = ""
    amount: float
    currency: str = "RUB"
    fx_rate: Optional[float] = None
    on: date
    deviation_reason: str = ""
    comment: str = ""


FACT_ROLES = {
    "cost_item": {"logistician": {"domestic_logistics", "export_formalities", "intl_freight", "insurance", "customs_duty", "import_vat",
                                  "broker_util", "certification", "rf_delivery"},
                  "service_head": {"service"}, "fd": {"finance", "warranty_reserve", "overhead", "factory_price"}, "manager": {"factory_price"}},
}


@router.post("/deals/{deal_id}/fact", status_code=201)
def add_fact(deal_id: str, body: FactIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if deal.status != DealStatus.WON:
        raise HTTPException(409, "Факт вносится после статуса «Выиграна»")
    if deal.plan_fact_closed and not user.has(Role.FD):
        raise HTTPException(403, "План/факт закрыт — редактирует только ФД")
    if body.kind == "cost_item":
        allowed = set()
        for r in user.roles:
            allowed |= FACT_ROLES["cost_item"].get(r.value, set())
        if user.has(Role.GD):
            allowed = {k for _, k, _ in COST_ITEMS}
        if body.cost_item_code not in allowed:
            raise HTTPException(403, f"Статья {body.cost_item_code} вносится другой ролью")
    elif body.kind in ("payment_in", "payment_out", "fx") and not user.has(Role.MANAGER, Role.FD, Role.GD):
        raise HTTPException(403, "Платежи вносит менеджер")
    thr = settings_service.get(db, "plan_fact_deviation_pct")
    e = PlanFactEntry(deal_id=deal.id, entered_by=user.id, **body.model_dump())
    db.add(e)
    audit(db, user.id, deal.id, "fact", "new", "create", after=body.model_dump(mode="json"))
    db.commit()
    return {"entry": serialize(e), "deviation_threshold_pct": thr}


@router.get("/deals/{deal_id}/plan-fact")
def plan_fact(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.FD, Role.GD, Role.CD))):
    """Отчёт: по каждой из 14 статей план (баз.), план (пессим.), факт, отклонение RUB и %."""
    deal = get_deal_or_404(db, deal_id, user)
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    if ver is None:
        raise HTTPException(409, "Нет утверждённой версии")
    facts = db.scalars(select(PlanFactEntry).where(PlanFactEntry.deal_id == deal.id)).all()
    thr = settings_service.get(db, "plan_fact_deviation_pct")
    won_variant = next((v for v in deal.variants if v.in_kp and v.price_rub), deal.variants[0] if deal.variants else None)
    if won_variant is None:
        raise HTTPException(409, "Нет вариантов")
    res = ver.calc_result["variants"][won_variant.id]
    base, pess = res["base"], res["pessimistic"]
    rows = []
    fact_by_item: dict[str, float] = {}
    for f in facts:
        if f.kind == "cost_item" and f.cost_item_code:
            fact_by_item[f.cost_item_code] = fact_by_item.get(f.cost_item_code, 0) + f.amount * (f.fx_rate or 1.0 if f.currency != "RUB" else 1.0)
    for no, key, title in COST_ITEMS:
        plan_b, plan_p = base["items_rub"].get(key, 0), pess["items_rub"].get(key, 0)
        fact = fact_by_item.get(key)
        dev = None if fact is None else fact - plan_b
        dev_pct = None if dev is None or not plan_b else dev / plan_b * 100
        rows.append({"no": no, "code": key, "title": title, "plan_base": plan_b, "plan_pess": plan_p, "fact": fact, "deviation_rub": dev,
                     "deviation_pct": None if dev_pct is None else round(dev_pct, 2), "flag": dev_pct is not None and abs(dev_pct) > thr})
    cost_fact = sum(fact_by_item.values()) if fact_by_item else None
    revenue_fact = sum(f.amount for f in facts if f.kind == "payment_in") or None
    totals = {
        "cost_total": {"plan_base": base["cost_total_rub"], "plan_pess": pess["cost_total_rub"], "fact": cost_fact},
        "revenue": {"plan_base": base["price_rub"], "plan_pess": pess["price_rub"], "fact": revenue_fact},
        "margin_pct": {"plan_base": base["margin_pct"], "plan_pess": pess["margin_pct"],
                       "fact": None if not (revenue_fact and cost_fact) else round((revenue_fact - cost_fact) / revenue_fact * 100, 2)},
        "profit": {"plan_base": base["profit_rub"], "plan_pess": pess["profit_rub"], "fact": None if not (revenue_fact and cost_fact) else revenue_fact - cost_fact},
        "cash_gap": {"plan_base": base["cash_gap"], "plan_pess": pess["cash_gap"], "fact": None},
        "delivery_days": {"plan_base": base["delivery_days"], "plan_pess": pess["delivery_days"],
                          "fact": next((f.amount for f in facts if f.kind == "delivery"), None)},
        "weighted_fx": {"plan_base": _weighted_fx(ver.calc_result, "base"), "plan_pess": _weighted_fx(ver.calc_result, "pessimistic"),
                        "fact": _fact_fx(facts)},
    }
    return {"variant": won_variant.name, "rows": rows, "totals": totals, "entries": [serialize(f) for f in facts], "closed": deal.plan_fact_closed}


def _weighted_fx(res: dict, scen: str) -> Optional[float]:
    comps = [c for c in res.get("fx_components", []) if "завод" in c["payment_ref"]]
    if not comps:
        return None
    return round(sum(c["rate_by_scenario"][scen] for c in comps) / len(comps), 4)


def _fact_fx(facts) -> Optional[float]:
    pts = [(f.amount, f.fx_rate) for f in facts if f.kind == "payment_out" and f.fx_rate and f.currency != "RUB"]
    if not pts:
        return None
    return round(sum(a * r for a, r in pts) / sum(a for a, _ in pts), 4)


@router.post("/deals/{deal_id}/plan-fact/close")
def close_plan_fact(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.FD))):
    deal = get_deal_or_404(db, deal_id, user)
    deal.plan_fact_closed = True
    audit(db, user.id, deal.id, "deal", deal.id, "plan_fact_close")
    db.commit()
    return {"closed": True}


@router.get("/reports/estimate-accuracy")
def estimate_accuracy(db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.FD, Role.GD))):
    """Сводный отчёт по закрытым сделкам: средняя точность оценки по статьям."""
    deals = db.scalars(select(Deal).where(Deal.plan_fact_closed.is_(True))).all()
    acc: dict[str, list[float]] = {}
    for d in deals:
        ver = db.get(DealVersion, d.approved_version_id) if d.approved_version_id else None
        if not ver:
            continue
        v = next((v for v in d.variants if v.price_rub), None)
        if not v:
            continue
        base = ver.calc_result["variants"][v.id]["base"]["items_rub"]
        facts = db.scalars(select(PlanFactEntry).where(PlanFactEntry.deal_id == d.id, PlanFactEntry.kind == "cost_item")).all()
        by_item: dict[str, float] = {}
        for f in facts:
            by_item[f.cost_item_code] = by_item.get(f.cost_item_code, 0) + f.amount
        for k, fact in by_item.items():
            if base.get(k):
                acc.setdefault(k, []).append(abs(fact - base[k]) / base[k] * 100)
    return {"deals": len(deals), "mape_by_item": {k: round(sum(v) / len(v), 2) for k, v in acc.items()}}
