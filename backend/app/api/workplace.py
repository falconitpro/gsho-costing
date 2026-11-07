"""Рабочее место — главная страница под роль: показатели, «главное сейчас», календарь дня и недели, задачи, сделки с действием,
согласования, уведомления, сроки, деньги и команда. Набор и порядок блоков — по роли; сотрудник скрывает и переставляет блоки,
ЗГД закрепляет обязательные блоки для роли."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import ROLE_TITLES, Role
from ..core.workflow import OPEN_STATUSES, STATUS_TITLES, DealStatus
from ..db import get_db
from ..models import Deal, ExecutionStage, FxRate, LegalPackage, NotificationLog, PowerOfAttorney, Task, User
from ..services import settings_service
from ..services.deal_service import audit
from .common import can_see_deal

router = APIRouter(prefix="/api/workplace", tags=["workplace"])

# ------------------------------------------------------------------ блоки и их набор по ролям
BLOCKS: dict[str, str] = {
    "kpis": "Показатели", "focus": "Главное сейчас", "today": "Сегодня и неделя", "tasks": "Задачи и поручения", "deals": "Сделки — требуют действия",
    "approvals": "Ждут моего решения", "attention": "Требует вмешательства", "money": "Денежный поток", "funnel": "Воронка квартала",
    "team": "Команда · нагрузка", "notifications": "Уведомления", "deadlines": "Ближайшие сроки", "legal": "Юридический блок", "quick": "Быстрые действия",
}
ROLE_BLOCKS: dict[str, list[str]] = {
    "manager":      ["kpis", "focus", "today", "tasks", "deals", "funnel", "notifications", "deadlines", "quick"],
    "cd":           ["kpis", "focus", "approvals", "today", "tasks", "deals", "funnel", "team", "attention", "notifications", "deadlines", "quick"],
    "gd":           ["kpis", "focus", "approvals", "attention", "money", "today", "tasks", "team", "legal", "notifications", "deadlines", "quick"],
    "fd":           ["kpis", "focus", "money", "approvals", "attention", "today", "tasks", "notifications", "deadlines", "quick"],
    "lawyer":       ["kpis", "focus", "legal", "today", "tasks", "approvals", "notifications", "deadlines", "quick"],
    "logistician":  ["kpis", "focus", "today", "tasks", "deals", "deadlines", "notifications", "quick"],
    "service_head": ["kpis", "focus", "today", "tasks", "deals", "deadlines", "notifications", "quick"],
    "purchasing":   ["kpis", "focus", "today", "tasks", "deals", "deadlines", "notifications", "quick"],
    "admin":        ["kpis", "approvals", "today", "tasks", "notifications", "quick"],
    "observer":     ["kpis", "deals", "funnel", "notifications"],
}
APPROVAL_STATUS_ROLE = {DealStatus.CD_APPROVAL: Role.CD, DealStatus.FD_VISA: Role.FD, DealStatus.GD_VISA: Role.GD}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _primary_role(user: CurrentUser) -> str:
    for r in (Role.GD, Role.CD, Role.FD, Role.LAWYER, Role.MANAGER, Role.LOGISTICIAN, Role.SERVICE_HEAD, Role.PURCHASING, Role.ADMIN, Role.OBSERVER):
        if r in user.roles:
            return r.value
    return "observer"


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return ""
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.1f} млн ₽".replace(",", " ").replace(".", ",")
    return f"{v:,.0f} ₽".replace(",", " ")


def _price(d: Deal) -> Optional[float]:
    from ..services.execution_service import won_variant
    v = won_variant(d) if d.variants else None
    return float(v.price_rub) if v is not None and v.price_rub is not None else None


def _deals(db: Session, user: CurrentUser) -> list[Deal]:
    rows = db.scalars(select(Deal).where(Deal.archived_at.is_(None), Deal.deleted_at.is_(None)).order_by(Deal.updated_at.desc())).all()
    return [d for d in rows if can_see_deal(user, d)]


def _equipment(d: Deal) -> str:
    poss = list(d.positions or [])
    return "; ".join(f"{int(p.qty or 1)} × {' '.join(x for x in (p.trademark or '', p.model or '') if x).strip() or p.machine_type or 'позиция'}" for p in poss[:2]) + (f" … +{len(poss) - 2}" if len(poss) > 2 else "")


def _my_tasks(db: Session, user: CurrentUser, today: date) -> list[dict]:
    out = []
    deals = {}
    for t in db.scalars(select(Task).where(Task.done_at.is_(None)).order_by(Task.due)).all():
        mine = t.assignee_id == user.id or t.assignee_id in user.acting_for or (t.assignee_id is None and Role(t.role) in user.roles)
        if not mine:
            continue
        d = deals.get(t.deal_id) or db.get(Deal, t.deal_id)
        deals[t.deal_id] = d
        days = (t.due - today).days
        out.append({"id": t.id, "title": t.title, "due": t.due.isoformat(), "days_left": days, "overdue": days < 0, "deal_id": t.deal_id,
                    "deal_number": d.number if d else "", "client_name": d.client.name if d and d.client else "", "block": t.block.split(":")[0],
                    "priority": "urgent" if days < 0 else "high" if days <= 1 else "normal", "as_deputy": bool(t.assignee_id and t.assignee_id in user.acting_for)})
    return out


def _action_for(d: Deal, user: CurrentUser, today: date, tasks_by_deal: dict[str, list[dict]]) -> tuple[str, str]:
    """Что сделать по сделке и тон (bad / warn / info / ok)."""
    ts = tasks_by_deal.get(d.id) or []
    over = [t for t in ts if t["overdue"]]
    if over:
        return f"{over[0]['title']} — просрочено {-over[0]['days_left']} дн.", "bad"
    if d.status in OPEN_STATUSES and d.kp_due:
        left = (d.kp_due - today).days
        if left < 0:
            return f"срок КП прошёл {-left} дн. назад", "bad"
        if left <= 3:
            return f"КП до {d.kp_due:%d.%m}" + (" — сегодня" if left == 0 else f" — через {left} дн."), "warn"
    if ts:
        return ts[0]["title"] + (f" — до {ts[0]['due'][8:10]}.{ts[0]['due'][5:7]}" if ts[0]["due"] else ""), "info"
    if d.status == DealStatus.KP_SENT:
        exp = d.decision_expected_on
        return "ждём решение клиента" + (f" до {exp:%d.%m}" if exp else ""), "info"
    if d.status == DealStatus.REWORK:
        return "доработать расчёт и отправить повторно", "warn"
    return "", ""


@router.get("")
def workplace(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    local = now + timedelta(hours=3)          # московское время: дата и приветствие — по нему, а не по UTC сервера
    today = local.date()
    role = _primary_role(user)
    u = db.get(User, user.id)
    names = {x.id: x.display_name for x in db.scalars(select(User)).all()}
    fin = user.can("finance_analytics")
    lead = user.has(Role.CD, Role.GD, Role.FD)
    deals = _deals(db, user)
    open_deals = [d for d in deals if d.status in OPEN_STATUSES]
    exec_deals = [d for d in deals if d.status in (DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION)]
    tasks = _my_tasks(db, user, today)
    tasks_by_deal: dict[str, list[dict]] = {}
    for t in tasks:
        tasks_by_deal.setdefault(t["deal_id"], []).append(t)
    overdue_tasks = [t for t in tasks if t["overdue"]]

    # этапы исполнения (сроки, отставания)
    stages: dict[str, list[ExecutionStage]] = {}
    if exec_deals:
        for s in db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id.in_([d.id for d in exec_deals]))).all():
            stages.setdefault(s.deal_id, []).append(s)
    exec_rows = []
    for d in exec_deals:
        sts = stages.get(d.id) or []
        open_ = sorted((s for s in sts if not s.actual_on and (s.refined_on or s.planned_on)), key=lambda s: (s.refined_on or s.planned_on))
        if not sts:
            continue
        nxt = open_[0] if open_ else None
        delay = max([((s.actual_on or s.refined_on or s.planned_on) - s.baseline_on).days for s in sts if s.baseline_on and (s.actual_on or s.refined_on or s.planned_on)], default=0)
        exec_rows.append({"deal": d, "next": nxt, "overdue": sum(1 for s in open_ if (s.refined_on or s.planned_on) < today), "delay": delay, "done": sum(1 for s in sts if s.actual_on), "total": len(sts), "stages": sts})

    # ---------------- календарь: сегодня + неделя + ближайшие 14 дней
    events: list[dict] = []
    for t in tasks:
        due = date.fromisoformat(t["due"])
        if today <= due <= today + timedelta(days=14):
            events.append({"on": due.isoformat(), "time": "", "title": t["title"], "sub": f"Задача · {t['deal_number']} {t['client_name']}".strip(), "tone": "warn" if due == today else "info", "url": f"/deals/{t['deal_id']}", "kind": "task"})
    for d in open_deals:
        if d.kp_due and today <= d.kp_due <= today + timedelta(days=14):
            events.append({"on": d.kp_due.isoformat(), "time": "", "title": f"Срок КП — {d.number}", "sub": f"{d.client.name if d.client else ''} · {STATUS_TITLES.get(d.status, '')}", "tone": "warn", "url": f"/deals/{d.id}", "kind": "kp"})
        if d.decision_expected_on and today <= d.decision_expected_on <= today + timedelta(days=14):
            events.append({"on": d.decision_expected_on.isoformat(), "time": "", "title": f"Решение клиента — {d.number}", "sub": d.client.name if d.client else "", "tone": "info", "url": f"/deals/{d.id}", "kind": "decision"})
    for r in exec_rows:
        for s in r["stages"]:
            on = s.refined_on or s.planned_on
            if on and not s.actual_on and today <= on <= today + timedelta(days=14):
                events.append({"on": on.isoformat(), "time": "", "title": f"{s.title} — {r['deal'].number}", "sub": (r["deal"].client.name if r["deal"].client else "") + (f" · {_fmt_money(float(s.amount))} {s.currency}" if s.amount and fin else ""), "tone": "ok", "url": f"/deals/{r['deal'].id}?tab=execution", "kind": "stage"})
    poas = []
    if user.can("legal_profiles") or user.can("legal_docs"):
        poas = db.scalars(select(PowerOfAttorney).where(PowerOfAttorney.status == "active")).all()
        for p in poas:
            if p.valid_to and today <= p.valid_to <= today + timedelta(days=30):
                events.append({"on": p.valid_to.isoformat(), "time": "", "title": f"Истекает доверенность {p.number}", "sub": (p.attorney or {}).get("full_name", ""), "tone": "warn", "url": "/legal?tab=poa", "kind": "poa"})
    events.sort(key=lambda e: (e["on"], e["time"], e["title"]))
    today_events = [e for e in events if e["on"] == today.isoformat()]
    monday = today - timedelta(days=today.weekday())
    week = []
    for i in range(7):
        dd = monday + timedelta(days=i)
        week.append({"date": dd.isoformat(), "wd": WEEKDAYS[i], "day": dd.day, "today": dd == today, "count": sum(1 for e in events if e["on"] == dd.isoformat()),
                     "tones": sorted({e["tone"] for e in events if e["on"] == dd.isoformat()})})
    upcoming = [e for e in events if e["on"] > today.isoformat()][:8]

    # ---------------- сделки с действием
    deal_rows = []
    for d in open_deals + [r["deal"] for r in exec_rows]:
        action, tone = _action_for(d, user, today, tasks_by_deal)
        r = next((x for x in exec_rows if x["deal"].id == d.id), None)
        if r is not None and not action:
            if r["overdue"]:
                action, tone = f"этап просрочен: {r['next'].title}" if r["next"] else "просроченные этапы", "bad"
            elif r["next"] and (r["next"].refined_on or r["next"].planned_on) <= today + timedelta(days=7):
                action, tone = f"{r['next'].title} — {(r['next'].refined_on or r['next'].planned_on):%d.%m}", "info"
        if not action and not lead:
            continue
        deal_rows.append({"id": d.id, "number": d.number, "client_name": d.client.name if d.client else "", "site": d.site, "equipment": _equipment(d), "status": d.status.value,
                          "status_title": STATUS_TITLES.get(d.status, d.status.value), "price_rub": _price(d) if fin else None, "manager_name": names.get(d.manager_id, ""),
                          "action": action, "tone": tone, "exec": {"done": r["done"], "total": r["total"], "delay": r["delay"]} if r else None, "priority": d.priority})
    rank = {"bad": 0, "warn": 1, "info": 2, "ok": 3, "": 4}
    deal_rows.sort(key=lambda x: (rank.get(x["tone"], 5), x["number"]))
    deal_rows = deal_rows[:8]

    # ---------------- согласования
    approvals: list[dict] = []
    for d in open_deals:
        need = APPROVAL_STATUS_ROLE.get(d.status)
        if need and need in user.roles:
            v = None
            from ..services.execution_service import won_variant
            v = won_variant(d) if d.variants else None
            sub = f"{_equipment(d)} · менеджер {names.get(d.manager_id, '')}"
            if fin and v is not None and v.price_rub:
                sub += f" · {_fmt_money(float(v.price_rub))}" + (f" · маржа {v.margin_pct:.1f} %" if getattr(v, 'margin_pct', None) is not None else "")
            approvals.append({"kind": "deal", "title": f"{'КП' if d.status == DealStatus.CD_APPROVAL else 'Виза'} {d.number} · {d.client.name if d.client else ''}", "sub": sub,
                              "url": f"/deals/{d.id}?tab=pricing", "waiting_since": d.updated_at.isoformat() if d.updated_at else None, "tone": "warn"})
    if user.has(Role.LAWYER, Role.CD, Role.GD):
        want = {"lawyer_review"} if user.has(Role.LAWYER) else set()
        if user.has(Role.CD, Role.GD):
            want.add("commercial_review")
        for p in db.scalars(select(LegalPackage).where(LegalPackage.status.in_(sorted(want))).order_by(LegalPackage.updated_at.desc())).all():
            d = db.get(Deal, p.deal_id)
            if d is None or not can_see_deal(user, d):
                continue
            approvals.append({"kind": "package", "title": f"{'Закупочный' if p.kind == 'purchase' else 'Клиентский'} пакет · {d.number} · {d.client.name if d.client else ''}",
                              "sub": "на проверке юриста" if p.status == "lawyer_review" else "коммерческое согласование", "url": f"/deals/{d.id}?tab=legal", "waiting_since": p.updated_at.isoformat() if p.updated_at else None, "tone": "info"})
    if user.has(Role.ADMIN, Role.GD):
        for x in db.scalars(select(User).where(User.is_active.is_(True))).all():
            pc = x.pending_changes or {}
            if pc.get("fields"):
                approvals.append({"kind": "user", "title": f"Изменение данных сотрудника · {x.display_name}", "sub": ", ".join(pc["fields"].keys()) if isinstance(pc["fields"], dict) else ", ".join(pc["fields"]), "url": "/settings?s=team", "waiting_since": pc.get("requested_at"), "tone": "info"})

    # ---------------- требует вмешательства (руководители)
    attention: list[dict] = []
    receivables: list[dict] = []
    if lead:
        for r in exec_rows:
            for s in r["stages"]:
                if s.code in ("client_advance", "client_final") and not s.actual_on:
                    on = s.refined_on or s.planned_on
                    if on and on < today:
                        receivables.append({"deal": r["deal"], "days": (today - on).days, "amount": float(s.amount or 0), "currency": s.currency, "title": s.title})
        for rc in sorted(receivables, key=lambda x: -x["days"])[:3]:
            attention.append({"title": f"{rc['title']} · {rc['deal'].client.name if rc['deal'].client else ''}" + (f" · {_fmt_money(rc['amount'])}" if fin and rc["amount"] else "") + f" — просрочен {rc['days']} дн.",
                              "sub": f"{rc['deal'].number} · менеджер {names.get(rc['deal'].manager_id, '')}", "url": f"/deals/{rc['deal'].id}?tab=execution", "tone": "bad"})
        for r in sorted((x for x in exec_rows if x["delay"] and x["delay"] > 3), key=lambda x: -x["delay"])[:3]:
            attention.append({"title": f"{r['deal'].number} · отставание от плана {r['delay']} дн.", "sub": (r["next"].title if r["next"] else "") + f" · {names.get(r['deal'].manager_id, '')}", "url": f"/deals/{r['deal'].id}?tab=execution", "tone": "warn"})
        all_over = db.scalars(select(Task).where(Task.done_at.is_(None), Task.due < today)).all()
        per: dict[str, int] = {}
        for t in all_over:
            if t.assignee_id:
                per[t.assignee_id] = per.get(t.assignee_id, 0) + 1
        if per:
            attention.append({"title": f"{len(all_over)} задач просрочено у {len(per)} сотрудников", "sub": ", ".join(f"{names.get(k, '?')} — {v}" for k, v in sorted(per.items(), key=lambda x: -x[1])[:4]), "url": "/tasks", "tone": "warn"})
        for p in poas:
            if p.valid_to and (p.valid_to - today).days <= 30:
                attention.append({"title": f"Доверенность {p.number} истекает через {(p.valid_to - today).days} дн.", "sub": (p.attorney or {}).get("full_name", "") + " · продлить или отозвать", "url": "/legal?tab=poa", "tone": "warn"})

    # ---------------- деньги (8 недель, RUB) — ФД, КД, ЗГД
    money = None
    if fin:
        weeks: dict[str, dict[str, float]] = {}
        horizon = today + timedelta(days=56)
        for r in exec_rows:
            for s in r["stages"]:
                kind = {"client_advance": "in", "client_final": "in", "factory_advance": "out", "factory_final": "out"}.get(s.code)
                on = s.refined_on or s.planned_on
                if not kind or s.actual_on or not on or on > horizon or s.currency != "RUB" or not s.amount:
                    continue
                wk = max(on, today)
                wk = (wk - timedelta(days=wk.weekday())).isoformat()
                w = weeks.setdefault(wk, {"in": 0.0, "out": 0.0})
                w[kind] += float(s.amount)
        curve, cum = [], 0.0
        for i in range(8):
            wk = (monday + timedelta(days=7 * i)).isoformat()
            w = weeks.get(wk, {"in": 0.0, "out": 0.0})
            cum += w["in"] - w["out"]
            curve.append({"week": wk, "label": f"{int(wk[8:10])} {MONTHS[int(wk[5:7]) - 1][:3]}", "in": round(w["in"]), "out": round(w["out"]), "net": round(w["in"] - w["out"]), "cum": round(cum)})
        money = {"curve": curve, "receivable_sum": round(sum(r["amount"] for r in receivables if r["currency"] == "RUB")), "receivable_count": len(receivables),
                 "min_cum": min((c["cum"] for c in curve), default=0), "min_week": min(curve, key=lambda c: c["cum"])["label"] if curve else ""}

    # ---------------- воронка и команда
    mine = [d for d in deals if user.has(Role.CD, Role.GD, Role.FD, Role.OBSERVER) or d.manager_id == user.id or d.manager_id in user.acting_for]
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    won_q = [d for d in mine if d.status in (DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION, DealStatus.CLOSED) and (d.contract_signed_at or (d.updated_at.date() if d.updated_at else today)) >= q_start]
    funnel = [{"code": "request", "title": "запросы", "count": sum(1 for d in mine if d.status in (DealStatus.DRAFT, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC))},
              {"code": "calc", "title": "расчёт", "count": sum(1 for d in mine if d.status in (DealStatus.CALC_READY, DealStatus.REWORK))},
              {"code": "approval", "title": "согласование", "count": sum(1 for d in mine if d.status in (DealStatus.CD_APPROVAL, DealStatus.FD_VISA, DealStatus.GD_VISA))},
              {"code": "kp", "title": "КП", "count": sum(1 for d in mine if d.status in (DealStatus.APPROVED, DealStatus.KP_SENT))},
              {"code": "won", "title": "выиграно", "count": len(won_q)}]
    team = []
    if user.has(Role.CD, Role.GD):
        load: dict[str, dict[str, int]] = {}
        for d in deals:
            if d.status in OPEN_STATUSES or d.status in (DealStatus.CONTRACT, DealStatus.EXECUTION):
                m = load.setdefault(d.manager_id, {"open": 0, "overdue": 0, "exec": 0})
                m["open" if d.status in OPEN_STATUSES else "exec"] += 1
        for t in db.scalars(select(Task).where(Task.done_at.is_(None), Task.due < today)).all():
            if t.assignee_id in load:
                load[t.assignee_id]["overdue"] += 1
        mx = max((v["open"] + v["exec"] for v in load.values()), default=1) or 1
        team = sorted([{"name": names.get(k, "?"), **v, "share": round((v["open"] + v["exec"]) / mx, 2)} for k, v in load.items() if k], key=lambda x: -(x["open"] + x["exec"]))[:8]

    # ---------------- показатели по роли
    kpis: list[dict] = []
    in_work_sum = sum(_price(d) or 0 for d in open_deals)
    won_sum = sum(_price(d) or 0 for d in won_q)
    if role in ("manager", "observer", "logistician", "service_head", "purchasing"):
        kp_week = [d for d in open_deals if d.kp_due and today <= d.kp_due <= today + timedelta(days=7) and (d.manager_id == user.id or d.manager_id in user.acting_for or role != "manager")]
        kpis = [{"title": "Сделок в работе", "value": str(len([d for d in open_deals if role != "manager" or d.manager_id == user.id or d.manager_id in user.acting_for])), "sub": (_fmt_money(in_work_sum) if fin else "") , "tone": ""},
                {"title": "КП со сроком на неделе", "value": str(len(kp_week)), "sub": f"ближайшее — {min(d.kp_due for d in kp_week):%d.%m}" if kp_week else "сроков нет", "tone": "warn" if kp_week else ""},
                {"title": "Просрочено задач", "value": str(len(overdue_tasks)), "sub": f"и {sum(1 for r in exec_rows if r['overdue'])} этапов исполнения" if exec_rows else "", "tone": "bad" if overdue_tasks else "ok"},
                {"title": "Выиграно в квартале", "value": str(len(won_q)), "sub": (_fmt_money(won_sum) if fin else "сделок"), "tone": "ok" if won_q else ""}]
    else:
        kpis = [{"title": "Воронка · в работе", "value": str(len(open_deals)), "sub": (_fmt_money(in_work_sum) + " · " if fin else "") + f"{len({d.manager_id for d in open_deals})} менеджеров", "tone": ""},
                {"title": "Выиграно в квартале", "value": str(len(won_q)), "sub": _fmt_money(won_sum) if fin else "", "tone": "ok" if won_q else ""},
                {"title": "Ждут решения", "value": str(len(approvals)), "sub": "согласований", "tone": "warn" if approvals else "ok"},
                {"title": "Дебиторка просрочена", "value": str(len(receivables)), "sub": _fmt_money(sum(r["amount"] for r in receivables if r["currency"] == "RUB")) if fin and receivables else "платежей", "tone": "bad" if receivables else "ok"} if lead else
                {"title": "Просрочено задач", "value": str(len(overdue_tasks)), "sub": "", "tone": "bad" if overdue_tasks else "ok"},
                {"title": "Исполнение", "value": str(len(exec_rows)), "sub": f"{sum(1 for r in exec_rows if r['delay'] > 0)} с отставанием", "tone": "warn" if any(r["delay"] > 0 for r in exec_rows) else ""}]

    # ---------------- главное сейчас
    focus = None
    cands = []
    for t in overdue_tasks[:1]:
        cands.append((0, {"title": t["title"], "text": f"Просрочено {-t['days_left']} дн. · {t['deal_number']} {t['client_name']}", "action": "Открыть сделку", "url": f"/deals/{t['deal_id']}", "tone": "bad"}))
    for a in approvals[:1]:
        cands.append((1, {"title": a["title"], "text": a["sub"] + " · ждёт вашего решения", "action": "Рассмотреть", "url": a["url"], "tone": "warn"}))
    for e in today_events[:1]:
        cands.append((2, {"title": e["title"], "text": e["sub"] + " · сегодня", "action": "Открыть", "url": e["url"], "tone": "warn"}))
    for a in attention[:1]:
        cands.append((3, {"title": a["title"], "text": a["sub"], "action": "Разобраться", "url": a["url"], "tone": a["tone"]}))
    for r in deal_rows[:1]:
        if r["action"]:
            cands.append((4, {"title": f"{r['number']} · {r['client_name']}", "text": r["action"], "action": "Открыть сделку", "url": f"/deals/{r['id']}", "tone": r["tone"] or "info"}))
    if cands:
        focus = sorted(cands, key=lambda x: x[0])[0][1]

    # ---------------- уведомления, курсы, юр. блок
    notes = db.scalars(select(NotificationLog).where(NotificationLog.user_id == user.id, NotificationLog.channel == "app").order_by(NotificationLog.at.desc()).limit(8)).all()
    notifications = [{"id": n.id, "text": n.text, "at": n.at.isoformat() if n.at else None, "read": n.read_at is not None, "deal_id": n.deal_id, "event": n.event} for n in notes]
    fx = {}
    for cur in ("USD", "CNY", "EUR"):
        r = db.scalars(select(FxRate).where(FxRate.currency == cur).order_by(FxRate.on.desc()).limit(1)).first()
        if r:
            fx[cur] = {"rate": round(r.rate, 2), "on": r.on.isoformat()}
    legal = None
    if user.has(Role.LAWYER, Role.GD):
        pk = db.scalars(select(LegalPackage.status)).all()
        legal = {"lawyer_review": sum(1 for s in pk if s == "lawyer_review"), "commercial_review": sum(1 for s in pk if s == "commercial_review"),
                 "poa_active": len(poas), "poa_expiring": sum(1 for p in poas if p.valid_to and (p.valid_to - today).days <= 30),
                 "poa_no_scan": sum(1 for p in poas if not any(f.get("kind") == "signed_scan" for f in (p.files or [])))}

    # ---------------- раскладка
    default_order = ROLE_BLOCKS.get(role, ROLE_BLOCKS["observer"])
    all_required = settings_service.get(db, "workplace_blocks") or {}
    required = all_required.get(role, [])
    prefs = ((u.prefs if u else None) or {}).get("workplace", {})
    order = [b for b in (prefs.get("order") or []) if b in default_order] + [b for b in default_order if b not in (prefs.get("order") or [])]
    hidden = [b for b in (prefs.get("hidden") or []) if b in default_order and b not in required]
    greeting = "Доброе утро" if 5 <= local.hour < 12 else "Добрый день" if 12 <= local.hour < 18 else "Добрый вечер"
    first = (user.display_name or "").split()
    return {"user": {"name": user.display_name, "first_name": first[1] if len(first) > 1 and role != "observer" else (first[0] if first else ""), "role": role, "role_title": ROLE_TITLES.get(Role(role), role), "greeting": greeting},
            "date": {"iso": today.isoformat(), "text": f"{WEEKDAYS[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}", "weekday": today.weekday()},
            "layout": {"order": order, "hidden": hidden, "required": required, "available": [{"code": b, "title": BLOCKS[b]} for b in default_order], "can_lock": user.has(Role.GD), "role_required": all_required if user.has(Role.GD) else {}, "roles": {k: ROLE_TITLES.get(Role(k), k) for k in ROLE_BLOCKS}, "role_blocks": ROLE_BLOCKS, "all_blocks": BLOCKS},
            "kpis": kpis, "focus": focus, "today": {"events": today_events, "week": week}, "upcoming": upcoming, "tasks": tasks[:10], "tasks_total": len(tasks), "tasks_overdue": len(overdue_tasks),
            "deals": deal_rows, "approvals": approvals, "attention": attention, "money": money, "funnel": funnel, "team": team, "notifications": notifications, "fx": fx, "legal": legal,
            "fin": fin, "quick": _quick(user)}


def _quick(user: CurrentUser) -> list[dict]:
    q = []
    if user.has(Role.MANAGER, Role.CD, Role.GD):
        q += [{"title": "Новый расчёт", "url": "/deals?new=1"}, {"title": "Новый клиент", "url": "/counterparties?type=clients&new=1"}]
    if user.has(Role.MANAGER, Role.SERVICE_HEAD, Role.PURCHASING, Role.CD, Role.GD):
        q.append({"title": "Запасные части", "url": "/parts/setup"})
    if user.has(Role.LOGISTICIAN):
        q.append({"title": "Отгрузки", "url": "/summary?kind=shipments"})
    if user.has(Role.FD, Role.GD):
        q.append({"title": "Деньги", "url": "/summary?kind=money"})
    if user.has(Role.LAWYER, Role.GD, Role.FD):
        q.append({"title": "Доверенность", "url": "/legal?tab=poa"})
    q.append({"title": "Мои задачи", "url": "/tasks"})
    return q[:6]


class LayoutIn(BaseModel):
    order: list[str] = []
    hidden: list[str] = []


@router.put("/layout")
def save_layout(body: LayoutIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Личная раскладка: порядок и скрытые блоки (закреплённые ЗГД блоки скрыть нельзя)."""
    u = db.get(User, user.id)
    role = _primary_role(user)
    allowed = set(ROLE_BLOCKS.get(role, []))
    required = set((settings_service.get(db, "workplace_blocks") or {}).get(role, []))
    prefs = dict(u.prefs or {})
    prefs["workplace"] = {"order": [b for b in body.order if b in allowed], "hidden": [b for b in body.hidden if b in allowed and b not in required]}
    u.prefs = prefs
    db.commit()
    return prefs["workplace"]


class RoleBlocksIn(BaseModel):
    role: str
    required: list[str]


@router.put("/role-blocks")
def save_role_blocks(body: RoleBlocksIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """ЗГД закрепляет обязательные блоки для роли."""
    if not user.has(Role.GD):
        raise HTTPException(403, "Обязательные блоки по ролям задаёт ЗГД")
    if body.role not in ROLE_BLOCKS:
        raise HTTPException(422, "Неизвестная роль")
    cur = dict(settings_service.get(db, "workplace_blocks") or {})
    cur[body.role] = [b for b in body.required if b in ROLE_BLOCKS[body.role]]
    settings_service.set_value(db, "workplace_blocks", cur, user.id, comment=f"роль {body.role}")
    audit(db, user.id, None, "settings", "workplace_blocks", "update", after=cur)
    db.commit()
    return cur
