"""Задачи и доска, файлы хранилища (с проверкой роли), план vs факт."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc.types import COST_ITEMS
from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.download_audit import note
from ..core.files import file_response
from ..core.config import get_settings
from ..core.roles import Role
from ..core.workflow import DealStatus, STATUS_TITLES, overdue
from ..db import get_db
from ..models import Attachment, Deal, DealVersion, PlanFactEntry, Task
from ..services import settings_service
from ..services.deal_service import audit
from .common import can_see_deal, get_deal_or_404, serialize

router = APIRouter(prefix="/api", tags=["tasks-files-planfact"])


# ------------------------------------------------------------------ задачи

@router.get("/tasks/my")
def my_tasks(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    rows = db.scalars(select(Task).where(Task.done_at.is_(None)).order_by(Task.due)).all()
    today = date.today()
    out = []
    for t in rows:
        if t.assignee_id == user.id or t.assignee_id in user.acting_for or (t.assignee_id is None and Role(t.role) in user.roles):
            deal = db.get(Deal, t.deal_id)
            d = serialize(t)
            d["as_deputy"] = bool(t.assignee_id and t.assignee_id in user.acting_for)
            poss = list(deal.positions) if deal else []
            d.update({"deal_number": deal.number if deal else None, "deal_status": deal.status.value if deal else None, "overdue": overdue(t.due, today),
                      "days_left": (t.due - today).days,
                      "client_name": deal.client.name if deal and deal.client else "", "site": deal.site if deal else "",
                      "equipment": "; ".join(f"{int(p.qty or 1)} × {p.designation}" for p in poss[:3]) + (f" … ещё {len(poss) - 3}" if len(poss) > 3 else ""),
                      "hint": TASK_HINTS.get(t.block.split(":")[0], ""), "tab": TASK_TABS.get(t.block.split(":")[0], "main"),
                      "can_close": t.assignee_id == user.id or user.has(Role.CD, Role.GD)})
            out.append(d)
    from ..services.finance import questions as fin_questions   # уточнения «Финансов» — task-like элементы (D-141), без финансового контекста
    out.extend(fin_questions.task_items(db, user))
    return out


# что закрывает задачу и куда ведёт — по блоку
TASK_HINTS = {"manager": "заполните данные сделки и отправьте на расчёт", "logistics": "внесите расчёт логистики в сделке", "service": "внесите расчёт сервиса",
              "pricing": "назначьте цену и утвердите", "visa": "поставьте визу или верните на доработку", "gd": "поставьте визу ЗГД",
              "kp": "сформируйте и отправьте КП клиенту", "contract": "отметьте подписание контракта", "stage": "внесите факт или дату этапа",
              "local_option_quote": "запросите цену у поставщика опции", "waiting": "уточните у клиента решение по КП и отметьте результат"}
TASK_TABS = {"manager": "main", "logistics": "logistics", "service": "service", "pricing": "pricing", "visa": "pricing", "gd": "pricing", "kp": "pricing",
             "contract": "execution", "stage": "execution", "local_option_quote": "positions", "waiting": "main"}


class TaskDoneIn(BaseModel):
    comment: str = ""


@router.post("/tasks/{task_id}/done")
def task_done(task_id: str, body: TaskDoneIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Закрыть задачу вручную (исполнитель, КД, ЗГД) — например, «уточнить статус у клиента» или задача, сделанная вне системы."""
    t = db.get(Task, task_id)
    if t is None or t.done_at is not None:
        raise HTTPException(404, "Задача не найдена или уже закрыта")
    if not (t.assignee_id == user.id or t.assignee_id in user.acting_for or (t.assignee_id is None and Role(t.role) in user.roles) or user.has(Role.CD, Role.GD)):
        raise HTTPException(403, "Закрыть задачу может исполнитель, КД или ЗГД")
    t.done_at = datetime.now(timezone.utc)
    audit(db, user.id, t.deal_id, "task", t.id, "done", None, {"title": t.title, "comment": body.comment, "manual": True})
    db.commit()
    return {"ok": True}


@router.get("/board")
def board(db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.CD, Role.FD, Role.GD, Role.OBSERVER))):
    """Доска сделок для руководства: по статусам, с просрочками."""
    today = date.today()
    deals = [d for d in db.scalars(select(Deal).where(Deal.archived_at.is_(None)).order_by(Deal.updated_at.desc())).all() if can_see_deal(user, d)]
    tasks = db.scalars(select(Task).where(Task.done_at.is_(None))).all()
    by_deal: dict[str, list] = {}
    for t in tasks:
        by_deal.setdefault(t.deal_id, []).append({"title": t.title, "role": t.role, "due": t.due.isoformat(), "overdue": overdue(t.due, today)})
    from ..services.execution_service import status_detail
    columns = {s.value: {"title": STATUS_TITLES[s], "deals": []} for s in DealStatus}
    # задача 9: связанные заявки одного запроса клиента (исходная + дубли) — общий ключ группы и число связанных
    group_of = {d.id: (d.parent_deal_id or d.id) for d in deals}
    group_size: dict[str, int] = {}
    for g in group_of.values():
        group_size[g] = group_size.get(g, 0) + 1
    numbers = {d.id: d.number for d in deals}
    for d in deals:
        g = group_of[d.id]
        columns[d.status.value]["deals"].append({"id": d.id, "number": d.number, "client": d.client.name if d.client else None,
                                                 "priority": d.priority, "tasks": by_deal.get(d.id, []), "status_detail": status_detail(d),
                                                 "overdue": any(t["overdue"] for t in by_deal.get(d.id, [])),
                                                 "group": g, "group_number": numbers.get(g, ""), "related": group_size.get(g, 1) - 1})
    # заявки на запчасти — свои колонки по общим этапам (единый раздел «Сделки»)
    from ..services import parts_deals
    for st, title in parts_deals.STAGE_TITLES.items():
        columns[f"parts_{st}"] = {"title": f"ЗЧ · {title}", "deals": [], "kind": "parts"}
    for p in parts_deals.rows(db, user, include_closed=False):
        age = (today - parts_deals.as_of(p)).days
        columns[f"parts_{p['stage']}"]["deals"].append({"id": p["id"], "number": p["number"], "client": p["client_name"], "priority": "urgent" if p["urgent"] else "normal", "tasks": [],
                                                        "status_detail": f"{p['status_text']} · {p['lines']} поз." + (f" · {p['machine']}" if p["machine"] else ""),
                                                        "overdue": bool(p["urgent"] and p["stage"] in ("request", "calc")) or (age >= 5 and p["stage"] in ("request", "calc")),
                                                        "group": p["id"], "group_number": "", "related": 0, "url": p["url"]})
    return columns


@router.post("/tasks/check-overdue")
def check_overdue(db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.ADMIN, Role.GD))):
    """Ручной запуск проверки просрочек; автоматически выполняется фоновой задачей раз в час (main.py)."""
    from ..services.deal_service import check_overdue_tasks
    n = check_overdue_tasks(db)
    db.commit()
    return {"notified": n}


@router.get("/reports/overdue")
def overdue_report(date_from: Optional[date] = None, date_to: Optional[date] = None, user_id: str = "", db: Session = Depends(get_db),
                   user: CurrentUser = Depends(get_current_user)):
    """История просрочек выполнения задач по сотрудникам — только ЗГД.

    Просрочка: задача выполнена позже срока или не выполнена, а срок прошёл. Исполнитель — назначенный сотрудник; задачи без
    назначенного исполнителя показываются по роли («не назначен»). Период — по сроку задачи."""
    from datetime import datetime as _dt
    from ..core.workflow import role_title
    from ..models import User
    if not user.has(Role.GD):
        raise HTTPException(403, "История просрочек доступна только ЗГД")
    today = date.today()
    users = {u.id: u for u in db.scalars(select(User)).all()}
    q = select(Task)
    if date_from:
        q = q.where(Task.due >= date_from)
    if date_to:
        q = q.where(Task.due <= date_to)
    deals = {d.id: d.number for d in db.scalars(select(Deal)).all()}
    summary: dict[str, dict] = {}
    rows = []
    for t in db.scalars(q).all():
        if not t.due:
            continue
        key = t.assignee_id or f"role:{t.role}"
        if user_id and key != user_id:
            continue
        u = users.get(t.assignee_id) if t.assignee_id else None
        s = summary.setdefault(key, {"user_id": key, "name": u.display_name if u else f"не назначен ({role_title(t.role)})",
                                     "roles": [role_title(r) for r in (u.roles if u else [t.role])], "tasks": 0, "overdue": 0,
                                     "open_overdue": 0, "days_total": 0, "days_max": 0})
        s["tasks"] += 1
        done_on = t.done_at.date() if isinstance(t.done_at, _dt) else t.done_at
        late = (done_on - t.due).days if done_on else (today - t.due).days
        if late <= 0:
            continue
        s["overdue"] += 1
        s["days_total"] += late
        s["days_max"] = max(s["days_max"], late)
        if not done_on:
            s["open_overdue"] += 1
        rows.append({"user_id": key, "name": s["name"], "deal_id": t.deal_id, "deal_number": deals.get(t.deal_id, ""), "title": t.title,
                     "role": role_title(t.role), "due": t.due.isoformat(), "done_on": done_on.isoformat() if done_on else None,
                     "days_late": late, "open": not done_on, "notified": bool(t.overdue_notified)})
    out = []
    for s in summary.values():
        s["overdue_pct"] = round(s["overdue"] / s["tasks"] * 100, 1) if s["tasks"] else 0.0
        s["days_avg"] = round(s["days_total"] / s["overdue"], 1) if s["overdue"] else 0.0
        out.append(s)
    out.sort(key=lambda x: (-x["overdue"], -x["days_total"], x["name"]))
    rows.sort(key=lambda r: (not r["open"], -r["days_late"]))
    return {"summary": out, "rows": rows}


# ------------------------------------------------------------------ файлы (локальное хранилище)

BLOCKED_SCAN = {"infected", "error", "pending"}   # 04 §8: заражённые и непроверенные новые файлы не выдаются (D-049)


@router.get("/files/{path:path}")
def get_file(path: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    s = get_settings()
    if s.storage_provider not in ("local", "s3"):
        raise HTTPException(404, "Файлы выдаются ссылками хранилища")
    att = db.scalar(select(Attachment).where(Attachment.storage_path == path))
    ver = None if att else db.scalar(select(DealVersion).where((DealVersion.excel_path == path) | (DealVersion.excel_manager_path == path) | (DealVersion.kp_path == path)))
    if att is None and ver is None:
        raise HTTPException(404)
    deal = db.get(Deal, att.deal_id if att else ver.deal_id)
    if not can_see_deal(user, deal):
        raise HTTPException(404)
    if att is not None and att.folder == "08_legal" and not user.can("legal_docs"):
        raise HTTPException(403, "Документы сделки закрыты для роли")
    restricted = (att and att.folder == "04_cost") or (ver and path == ver.excel_path)
    if restricted and not user.can("cost_files"):
        raise HTTPException(403, "Папка «04 Расчёт себестоимости» недоступна для роли")
    if att is not None and att.scan_status in BLOCKED_SCAN:
        raise HTTPException(410, "Файл заблокирован проверкой безопасности — обратитесь к администратору")
    # журнал скачиваний (core/download_audit.py): объект и сделка — без пути хранения и имени файла
    note(entity_type="attachment" if att else "deal_version", entity_id=att.id if att else ver.id, deal_id=deal.id)
    if s.storage_provider == "s3":
        # права проверены выше — только теперь короткая ссылка (5 минут); в ответе и журнале её нет, кроме Location
        from fastapi.responses import RedirectResponse
        from .. import adapters
        name = att.file_name if att else os.path.basename(path)
        note(presigned=True)
        url = adapters.storage(s).presigned(path, name, inline=False)
        return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    full = os.path.join(s.storage_local_root, path)
    if ver is not None and path in (ver.excel_path, ver.excel_manager_path):
        # Excel версии собирается заново по сохранённому расчёту (цифры те же) — в текущем формате подписей
        from ..services.deal_service import rebuild_version_excel
        try:
            fresh = rebuild_version_excel(db, ver, manager=path == ver.excel_manager_path)
            if fresh:
                return file_response(fresh, os.path.basename(full) if os.path.basename(full) else os.path.basename(fresh), inline=False)
        except Exception:   # pragma: no cover — при ошибке отдаём сохранённый файл
            pass
    if not os.path.isfile(full):
        raise HTTPException(404)
    return file_response(full, os.path.basename(full), inline=False)


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
                  "service_head": {"service", "local_completion"},
                  "fd": {"finance", "warranty_reserve", "overhead", "factory_price", "local_completion", "external_commission", "manager_commission", "cd_commission"},
                  "manager": {"factory_price", "local_completion"}, "cd": {"local_completion"}, "purchasing": {"local_completion"}},
}


def _allowed_items(user: CurrentUser) -> set[str]:
    if user.has(Role.GD):
        return {k for _, k, _ in COST_ITEMS}
    allowed: set[str] = set()
    for r in user.roles:
        allowed |= FACT_ROLES["cost_item"].get(r.value, set())
    return allowed


@router.get("/deals/{deal_id}/fact/form")
def fact_form(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Что пользователь может вносить в факт: статьи себестоимости по роли и (ФД) фактические платежи; записи, внесённые им."""
    deal = get_deal_or_404(db, deal_id, user)
    from ..core.workflow import POST_WIN_STATUSES
    allowed = _allowed_items(user)
    mine = db.scalars(select(PlanFactEntry).where(PlanFactEntry.deal_id == deal.id).order_by(PlanFactEntry.on)).all()
    can_all = user.has(Role.CD, Role.FD, Role.GD)
    return {"open": deal.status in POST_WIN_STATUSES and (not deal.plan_fact_closed or user.has(Role.FD)),
            "closed": deal.plan_fact_closed, "items": [{"no": n, "code": k, "title": t} for n, k, t in COST_ITEMS if k in allowed],
            "payments": user.can("execution_payments", 2), "can_close": user.has(Role.FD) and not deal.plan_fact_closed,
            "can_report": can_all, "deviation_pct": settings_service.get(db, "plan_fact_deviation_pct"),
            "entries": [serialize(e) for e in mine if can_all or e.entered_by == user.id]}


@router.post("/deals/{deal_id}/fact", status_code=201)
def add_fact(deal_id: str, body: FactIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    from ..core.workflow import POST_WIN_STATUSES
    if deal.status not in POST_WIN_STATUSES:
        raise HTTPException(409, "Факт вносится после выигрыша (стадии контракта и исполнения)")
    if deal.plan_fact_closed and not user.has(Role.FD):
        raise HTTPException(403, "План/факт закрыт — редактирует только ФД")
    if body.currency != "RUB" and not body.fx_rate:
        raise HTTPException(422, "Для суммы в валюте укажите фактический курс")
    if body.kind == "cost_item":
        allowed = _allowed_items(user)
        if body.cost_item_code not in allowed:
            raise HTTPException(403, f"Статью «{next((t for _, k, t in COST_ITEMS if k == body.cost_item_code), body.cost_item_code)}» вносит другая роль")
    elif body.kind in ("payment_in", "payment_out", "fx") and not user.can("execution_payments", 2):
        raise HTTPException(403, "Фактические платежи вносит финансовый директор")
    thr = settings_service.get(db, "plan_fact_deviation_pct")
    e = PlanFactEntry(deal_id=deal.id, entered_by=user.id, **body.model_dump())
    db.add(e)
    db.flush()
    audit(db, user.id, deal.id, "fact", "new", "create", after=body.model_dump(mode="json"))
    from ..services.finance import legacy   # «Финансы»: ручной платёж план/факта — запись «требует сверки» (D-117)
    fin = legacy.from_planfact(db, user.id, e)
    from ..services.finance import recognition   # статья себестоимости → одно событие признания расхода (D-028, D-133)
    recognition.from_planfact(db, user.id, e)
    db.commit()
    out = {"entry": serialize(e), "deviation_threshold_pct": thr}
    if fin.get("status") != "none":
        out["finance"] = {"status": fin["status"], "warning": fin.get("warning", "")}
    return out


@router.delete("/deals/{deal_id}/fact/{entry_id}")
def delete_fact(deal_id: str, entry_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Ошибочная запись факта: удаляет автор (или ФД / ЗГД), пока план/факт не закрыт; запись остаётся в журнале действий."""
    deal = get_deal_or_404(db, deal_id, user)
    e = db.get(PlanFactEntry, entry_id)
    if e is None or e.deal_id != deal.id:
        raise HTTPException(404)
    if deal.plan_fact_closed:
        raise HTTPException(409, "План/факт закрыт")
    if e.entered_by != user.id and not user.has(Role.FD, Role.GD):
        raise HTTPException(403, "Удалить запись может её автор, ФД или ЗГД")
    audit(db, user.id, deal.id, "fact", str(e.id)[:128], "delete", before=serialize(e))
    if e.kind in ("payment_in", "payment_out") and not (e.ref or "").startswith("stage:"):
        from ..services.finance import legacy   # запись удалена — непроведённая запись сверки отзывается, история остаётся
        legacy.record(db, user.id, origin_type="planfact_legacy", origin_id=e.id, deal_id=deal.id, direction="in" if e.kind == "payment_in" else "out",
                      amount=None, currency=(e.currency or "RUB").upper(), on=None)
    if e.kind == "cost_item":
        from ..services.finance import recognition   # событие признания сторнируется с причиной, история остаётся
        recognition.planfact_deleted(db, user, e)
    db.delete(e)
    db.commit()
    return {"deleted": True}


@router.get("/deals/{deal_id}/plan-fact")
def plan_fact(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.FD, Role.GD, Role.CD))):
    """Отчёт: по каждой из 14 статей план (баз.), план (пессим.), факт, отклонение RUB и %."""
    deal = get_deal_or_404(db, deal_id, user)
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    if ver is None:
        raise HTTPException(409, "Нет утверждённой версии")
    facts = db.scalars(select(PlanFactEntry).where(PlanFactEntry.deal_id == deal.id)).all()
    thr = settings_service.get(db, "plan_fact_deviation_pct")
    from ..services.execution_service import won_variant as _won
    won_variant = _won(deal)
    if won_variant is None:
        raise HTTPException(409, "Нет вариантов")
    res = ver.calc_result["variants"][won_variant.id]
    base, pess = res["base"], res["pessimistic"]
    rows = []
    # факт P&L — по событиям признания (D-134): расход по статьям (старые факты без события учитываются один раз), выручка — только признанная
    from ..services.finance import access as fin_access, economy as fin_economy
    rec = fin_economy.recognized(db, deal.id)
    fact_by_item: dict[str, float] = {k: float(v) for k, v in rec["items_rub"].items()}
    for no, key, title in COST_ITEMS:
        plan_b, plan_p = base["items_rub"].get(key, 0), pess["items_rub"].get(key, 0)
        fact = fact_by_item.get(key)
        dev = None if fact is None else fact - plan_b
        dev_pct = None if dev is None or not plan_b else dev / plan_b * 100
        rows.append({"no": no, "code": key, "title": title, "plan_base": plan_b, "plan_pess": plan_p, "fact": fact, "deviation_rub": dev,
                     "deviation_pct": None if dev_pct is None else round(dev_pct, 2), "flag": dev_pct is not None and abs(dev_pct) > thr})
    cost_fact = sum(fact_by_item.values()) if fact_by_item else None
    revenue_fact = float(rec["revenue_rub"]) if rec["revenue_rub"] is not None else None
    cash = fin_economy.cash(db, fin_access.Visibility(db, user), deal.id, date.today())
    cf = cash["fact"]
    legacy_in = sum(f.amount * ((f.fx_rate or 1.0) if f.currency != "RUB" else 1.0) for f in facts if f.kind == "payment_in")
    legacy_out = sum(f.amount * ((f.fx_rate or 1.0) if f.currency != "RUB" else 1.0) for f in facts if f.kind == "payment_out")
    totals = {
        "cost_total": {"plan_base": base["cost_total_rub"], "plan_pess": pess["cost_total_rub"], "fact": cost_fact},
        "revenue": {"plan_base": base["price_rub"], "plan_pess": pess["price_rub"], "fact": revenue_fact},
        "margin_pct": {"plan_base": base["margin_pct"], "plan_pess": pess["margin_pct"],
                       "fact": None if not (revenue_fact and cost_fact) else round((revenue_fact - cost_fact) / revenue_fact * 100, 2)},
        "profit": {"plan_base": base["profit_rub"], "plan_pess": pess["profit_rub"], "fact": None if not (revenue_fact and cost_fact) else revenue_fact - cost_fact},
        "cash_gap": {"plan_base": base["cash_gap"], "plan_pess": pess["cash_gap"],
                     "fact": None if cf["gap"] is None else {"amount_rub": float(cf["gap"]["amount_rub"]), "starts_on": cf["gap"]["starts_on"], "days": cf["gap"]["days"]}},
        "delivery_days": {"plan_base": base["delivery_days"], "plan_pess": pess["delivery_days"],
                          "fact": next((f.amount for f in facts if f.kind == "delivery"), None)},
        "weighted_fx": {"plan_base": _weighted_fx(ver.calc_result, "base"), "plan_pess": _weighted_fx(ver.calc_result, "pessimistic"),
                        "fact": _fact_fx(facts)},
    }
    # неустойки по договорам сделки: к получению уменьшают, к уплате увеличивают себестоимость сделки
    from ..services.legal_contracts import deal_penalties
    pen = deal_penalties(db, deal.id)
    if pen["rows"]:
        net = pen["net_rub"]
        totals["penalties"] = {"plan_base": 0.0, "plan_pess": round(pen["receivable_accruing_rub"] - pen["payable_accruing_rub"], 2), "fact": net}
        pf = totals["profit"]["fact"]
        totals["profit_after_penalties"] = {"plan_base": base["profit_rub"], "plan_pess": pess["profit_rub"], "fact": None if pf is None else round(pf + net, 2)}
    money = {"status": cf["status"], "in_rub": None if cf["in_rub"] is None else float(cf["in_rub"]), "out_rub": None if cf["out_rub"] is None else float(cf["out_rub"]),
             "legacy_in_rub": round(legacy_in, 2) or None, "legacy_out_rub": round(legacy_out, 2) or None,
             "data_gaps": [g["text"] for g in cash["data_gaps"]], "revenue_events": rec["revenue_events"]}
    return {"variant": won_variant.name, "rows": rows, "totals": totals, "entries": [serialize(f) for f in facts], "closed": deal.plan_fact_closed, "penalties": pen,
            "money": money, "economy_url": f"/finance?tab=economy&deal={deal.id}" if fin_access.can_read(user) else None}


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
            by_item[f.cost_item_code] = by_item.get(f.cost_item_code, 0) + f.amount * ((f.fx_rate or 1.0) if f.currency != "RUB" else 1.0)
        for k, fact in by_item.items():
            if base.get(k):
                acc.setdefault(k, []).append(abs(fact - base[k]) / base[k] * 100)
    return {"deals": len(deals), "mape_by_item": {k: round(sum(v) / len(v), 2) for k, v in acc.items()}}
