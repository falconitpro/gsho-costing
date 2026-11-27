"""D-175 (пакет A2, согласовано владельцем 26.11): «Сводка → Показатели» — период, направление, динамика, сравнение
с прошлым периодом и таблица «план / факт / отклонение». Только чтение, только данные, которые сотрудник и так видит.

Источники и правила (без выдуманных показателей):
* выигрыш / проигрыш сделки ГШО — дата перехода статуса из журнала действий (audit_log), а если записи нет —
  дата подписания контракта; заявки на запчасти — дата последнего изменения (помечено «приблизительно»);
* сумма сделки — действующая цена без НДС в рублях (как на Пульте); только при доступе «Финансовая аналитика»;
  сделки без цены не входят в сумму, покрытие показывается;
* этапы исполнения: план — базовая дата (если нет — плановая), факт — отметка факта; отклонение — дни;
* деньги — общий сервис «Финансов» (план/факт по обязательствам, D-167), валюты не складываются;
* сравнение: текущий период с начала по сегодня (или до конца, если закончился) против такого же числа дней
  прошлого периода; если прошлый период пуст — сравнения нет, а не «+100 %»;
* плана продаж в системе нет — он не показывается и не придумывается.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.workflow import OPEN_STATUSES, POST_WIN_STATUSES, STATUS_TITLES, DealStatus
from ..models import AuditLog, Deal, ExecutionStage, User
from . import parts_deals
from ..core.clock import msk_date

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
KINDS = ("month", "quarter", "year")
DIRECTIONS = {"all": "все направления", "equipment": "ГШО (оборудование)", "parts": "запасные части"}
FUNNEL = [("request", "запрос и расчёт логистики", (DealStatus.DRAFT, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC)),
          ("calc", "расчёт", (DealStatus.CALC_READY, DealStatus.REWORK)),
          ("approval", "согласование", (DealStatus.CD_APPROVAL, DealStatus.FD_VISA, DealStatus.GD_VISA)),
          ("kp", "КП", (DealStatus.APPROVED, DealStatus.KP_SENT)),
          ("contract", "заключение контракта", (DealStatus.WON, DealStatus.CONTRACT)),
          ("execution", "исполнение", (DealStatus.EXECUTION,))]
PARTS_FUNNEL = {"request": "request", "calc": "calc", "kp": "kp"}


def period_bounds(kind: str, anchor: date) -> tuple[date, date, str]:
    if kind == "month":
        a = anchor.replace(day=1)
        return a, a.replace(day=calendar.monthrange(a.year, a.month)[1]), f"{MONTHS[a.month - 1]} {a.year}"
    if kind == "year":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31), f"{anchor.year} год"
    q = (anchor.month - 1) // 3
    a = date(anchor.year, 3 * q + 1, 1)
    b = date(anchor.year, 3 * q + 3, calendar.monthrange(anchor.year, 3 * q + 3)[1])
    return a, b, f"{q + 1} квартал {anchor.year}"


def previous_anchor(kind: str, a: date) -> date:
    return a - timedelta(days=1) if kind != "year" else date(a.year - 1, 6, 1)


def buckets(kind: str, a: date, b: date) -> list[dict]:
    out = []
    if kind == "month":   # недели (с понедельника), обрезанные границами месяца
        s = a
        while s <= b:
            e = min(b, s + timedelta(days=6 - s.weekday()))
            out.append({"key": s.isoformat(), "title": f"{s.day}–{e.day}", "from": s, "to": e})
            s = e + timedelta(days=1)
        return out
    m = date(a.year, a.month, 1)
    while m <= b:
        e = m.replace(day=calendar.monthrange(m.year, m.month)[1])
        out.append({"key": m.isoformat()[:7], "title": MONTHS_SHORT[m.month - 1] + (f" {m.year % 100:02d}" if kind == "year" and a.year != b.year else ""), "from": m, "to": e})
        m = e + timedelta(days=1)
    return out


def _status_dates(db: Session, deal_ids: list[str], statuses: set[str]) -> dict[str, dict[str, date]]:
    """Первая дата перехода в каждый из статусов — из журнала действий."""
    out: dict[str, dict[str, date]] = defaultdict(dict)
    if not deal_ids:
        return out
    rows = db.execute(select(AuditLog.deal_id, AuditLog.after, AuditLog.at).where(AuditLog.entity == "deal", AuditLog.action == "status",
                                                                                  AuditLog.deal_id.in_(deal_ids)).order_by(AuditLog.at)).all()
    for did, after, at in rows:
        st = (after or {}).get("status")
        if st in statuses and st not in out[did] and at is not None:
            out[did][st] = at.date()
    return out


def _cmp(cur: Optional[float], prev: Optional[float]) -> dict:
    if cur is None or prev is None:
        return {"current": cur, "previous": prev, "delta": None, "delta_pct": None}
    d = cur - prev
    return {"current": cur, "previous": prev, "delta": round(d, 2), "delta_pct": None if prev == 0 else round(d / prev * 100, 1)}


def build(db: Session, user: CurrentUser, *, kind: str, anchor: date, direction: str, today: date,
          can_see: Callable[[CurrentUser, Deal], bool], price: Callable[[Deal], Optional[float]]) -> dict:
    kind = kind if kind in KINDS else "quarter"
    direction = direction if direction in DIRECTIONS else "all"
    fin = user.can("finance_analytics")
    a, b, title = period_bounds(kind, anchor)
    pa, pb, ptitle = period_bounds(kind, previous_anchor(kind, a))
    cur_to = min(b, today)
    closed = b < today
    span = (cur_to - a).days
    prev_to = min(pb, pa + timedelta(days=span))
    future = a > today
    eq = direction in ("all", "equipment")
    pt = direction in ("all", "parts")

    deals = [d for d in db.scalars(select(Deal).where(Deal.archived_at.is_(None), Deal.deleted_at.is_(None))).all() if can_see(user, d)] if eq else []
    sd = _status_dates(db, [d.id for d in deals], {"won", "lost", "cancelled", "contract"})

    def won_on(d: Deal) -> Optional[date]:
        if d.status not in POST_WIN_STATUSES:
            return None
        x = sd.get(d.id, {})
        return x.get("won") or x.get("contract") or d.contract_signed_at

    def lost_on(d: Deal) -> Optional[date]:
        if d.status not in (DealStatus.LOST, DealStatus.CANCELLED):
            return None
        x = sd.get(d.id, {})
        return x.get("lost") or x.get("cancelled") or (msk_date(d.updated_at) if d.updated_at else None)

    parts = parts_deals.rows(db, user) if pt else []

    def in_(v: Optional[date], s: date, e: date) -> bool:
        return v is not None and s <= v <= e

    def metrics(s: date, e: date) -> dict:
        created = [d for d in deals if d.created_at and in_(msk_date(d.created_at), s, e)]
        won = [d for d in deals if in_(won_on(d), s, e)]
        lost = [d for d in deals if in_(lost_on(d), s, e)]
        p_created = [p for p in parts if p["created_at"] and in_(p["created_at"].date(), s, e)]
        p_won = [p for p in parts if p["block"] == "won" and in_(parts_deals.as_of(p), s, e)]
        p_lost = [p for p in parts if p["closed"] in ("lost", "cancelled") and in_(parts_deals.as_of(p), s, e)]
        won_prices = [price(d) for d in won]
        have = [x for x in won_prices if x is not None]
        won_sum = (sum(have) + sum(p["price_rub"] or 0 for p in p_won)) if fin else None
        decided = len(won) + len(p_won) + len(lost) + len(p_lost)
        return {"created": len(created) + len(p_created), "won": len(won) + len(p_won), "lost": len(lost) + len(p_lost),
                "won_sum": round(won_sum, 2) if won_sum is not None else None, "won_priced": len(have) + len(p_won), "won_total": len(won) + len(p_won),
                "win_rate": round((len(won) + len(p_won)) / decided * 100, 1) if decided else None, "decided": decided,
                "_won": won, "_p_won": p_won}

    cur = metrics(a, cur_to) if not future else None
    prev = metrics(pa, prev_to)
    full_prev = metrics(pa, pb)

    # этапы исполнения в периоде (только ГШО)
    stage_rows: list[dict] = []
    st_plan = st_done = st_ontime = st_overdue = st_all = 0
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    if eq and deals:
        by_id = {d.id: d for d in deals if d.status in POST_WIN_STATUSES}
        for s in db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id.in_(list(by_id)))).all() if by_id else []:
            plan = s.baseline_on or s.planned_on
            if not in_(plan, a, b):
                continue
            st_all += 1
            d = by_id[s.deal_id]
            due = plan <= today          # план к сегодняшнему дню: будущие этапы периода в отклонение не входят
            st_plan += due
            if s.actual_on:
                st_done += due
                dev = (s.actual_on - plan).days
                if dev <= 0:
                    st_ontime += 1
            else:
                dev = (today - plan).days if plan < today else None
                if dev is not None:
                    st_overdue += 1
            if dev is not None and dev > 0:
                stage_rows.append({"stage": s.title, "deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "",
                                   "plan": plan.isoformat(), "refined": s.refined_on.isoformat() if s.refined_on else None,
                                   "fact": s.actual_on.isoformat() if s.actual_on else None, "days": dev,
                                   "responsible": names.get(d.manager_id or "", ""), "url": f"/deals/{d.id}?tab=execution"})
    stage_rows.sort(key=lambda r: (r["fact"] is not None, -r["days"]))

    # деньги — план/факт «Финансов» по месяцам периода (D-167)
    money = None
    if fin and direction == "all":   # обязательства «Финансов» не делятся на ГШО и запчасти — только при «все направления»
        from .finance import access as fin_access, planfact
        months_back = max(0, (today.year - a.year) * 12 + today.month - a.month)
        months_fwd = max(0, (b.year - today.year) * 12 + b.month - today.month)
        pf = planfact.build(db, fin_access.Visibility(db, user), today, back=months_back, forward=months_fwd)
        keys = {p["key"] for p in pf["periods"] if a.isoformat()[:7] <= p["key"] <= b.isoformat()[:7]}
        money = []
        for sr in pf["series"]:
            rows = [r for r in sr["rows"] if r["period"] in keys]
            plan_all = sum(Decimal(str(r["plan"] or 0)) for r in rows)
            fact_rows = [r for r in rows if r["fact"] is not None]          # прошедшие и текущий месяц
            plan = sum(Decimal(str(r["plan"] or 0)) for r in fact_rows)
            fact = sum(Decimal(str(r["fact"] or 0)) + Decimal(str(r["fact_provisional"] or 0)) for r in fact_rows) if fact_rows else None
            if plan_all == 0 and not fact:
                continue
            money.append({"currency": sr["currency"], "direction": sr["direction"], "plan": float(plan), "plan_all": float(plan_all),
                          "fact": float(fact) if fact is not None else None, "deviation": float(fact - plan) if fact is not None else None, "partial": not closed})

    # динамика по интервалам
    series = []
    for bk in buckets(kind, a, b):
        f = bk["from"] > today
        m = metrics(bk["from"], min(bk["to"], today)) if not f else None
        series.append({"key": bk["key"], "title": bk["title"], "from": bk["from"].isoformat(), "to": bk["to"].isoformat(), "future": f,
                       "created": m["created"] if m else None, "won": m["won"] if m else None, "lost": m["lost"] if m else None,
                       "won_sum": m["won_sum"] if m else None})

    # структура: открытые сейчас по этапам воронки (снимок на сегодня, не зависит от периода)
    structure = []
    for code, ttl, sts in FUNNEL:
        n = sum(1 for d in deals if d.status in sts)
        np_ = sum(1 for p in parts if p["block"] == "work" and p.get("stage") == PARTS_FUNNEL.get(code)) if code in PARTS_FUNNEL else 0
        structure.append({"code": code, "title": ttl, "equipment": n, "parts": np_, "total": n + np_})

    def strip(m: Optional[dict]) -> Optional[dict]:
        return {k: v for k, v in m.items() if not k.startswith("_")} if m else None

    kpis = []
    if cur:
        kpis = [
            {"key": "created", "title": "Новые сделки", "unit": "шт.", **_cmp(cur["created"], prev["created"]), "url": "/deals?block=work",
             "definition": "Созданные в периоде сделки ГШО и заявки на запчасти."},
            {"key": "won", "title": "Выиграно", "unit": "шт.", **_cmp(cur["won"], prev["won"]), "url": "/deals?block=won",
             "definition": "Сделки ГШО — по дате перехода в «выиграна» (журнал действий), заявки на запчасти — по дате последнего изменения."},
            {"key": "won_sum", "title": "Сумма выигранных", "unit": "₽ без НДС", **_cmp(cur["won_sum"], prev["won_sum"]), "url": "/deals?block=won",
             "coverage": f"сумма по {cur['won_priced']} из {cur['won_total']}" if cur["won_priced"] < cur["won_total"] else "",
             "definition": "Действующая цена выигранных сделок без НДС в рублях; без цены — не входят (покрытие в подписи).", "finance": True} if fin else None,
            {"key": "lost", "title": "Проиграно и отменено", "unit": "шт.", **_cmp(cur["lost"], prev["lost"]), "url": "/deals?block=lost", "bad_up": True,
             "definition": "Сделки, перешедшие в «проиграна» или «отменена» в периоде."},
            {"key": "win_rate", "title": "Доля выигранных", "unit": "%", **_cmp(cur["win_rate"], prev["win_rate"]),
             "definition": f"Выиграно ÷ (выиграно + проиграно и отменено) среди решённых в периоде: решено {cur['decided']}." if cur["decided"] else "В периоде нет решённых сделок — доля не считается."},
        ]
        kpis = [k for k in kpis if k]
    table = []
    if eq:
        table.append({"key": "stages", "title": "Этапы исполнения (ГШО)", "unit": "этапов", "plan": st_plan, "fact": st_done,
                      "deviation": (st_done - st_plan) if st_plan else None,
                      "note": f"план — к сегодняшнему дню; в срок {st_ontime}, просрочено без факта {st_overdue}" + (f"; всего в периоде {st_all}" if st_all != st_plan else ""),
                      "source": "этапы исполнения: базовая дата в периоде", "url": "/deals?block=won&flag=overdue"})
    for mrow in money or []:
        dir_t = "поступления" if mrow["direction"] == "in" else "платежи"
        table.append({"key": f"money_{mrow['direction']}_{mrow['currency']}", "title": f"Деньги: {dir_t}, {mrow['currency']}", "unit": mrow["currency"],
                      "plan": mrow["plan"], "fact": mrow["fact"], "deviation": mrow["deviation"],
                      "note": (f"план — по прошедшим и текущему месяцу; всего в периоде {mrow['plan_all']:,.2f} {mrow['currency']}".replace(",", " ").replace(".", ",") if mrow["partial"] and mrow["plan_all"] != mrow["plan"] else ("период не закончен — факт на сегодня" if mrow["partial"] else "")),
                      "source": "«Финансы»: план/факт обязательств", "url": "/finance"})
    table.append({"key": "sales_plan", "title": "Продажи к плану", "unit": "", "plan": None, "fact": cur["won"] if cur else None, "deviation": None,
                  "note": "план продаж в системе не задан — отклонение не считается", "source": "", "url": "/deals?block=won"})

    return {"kind": kind, "direction": direction, "directions": DIRECTIONS, "finance": fin, "today": today.isoformat(),
            "period": {"from": a.isoformat(), "to": b.isoformat(), "title": title, "closed": closed, "future": future, "to_date": cur_to.isoformat()},
            "previous": {"from": pa.isoformat(), "to": pb.isoformat(), "title": ptitle, "compare_to": prev_to.isoformat()},
            "kpis": kpis, "current": strip(cur), "previous_full": strip(full_prev), "series": series, "structure": structure,
            "table": table, "deviations": stage_rows[:60], "deviations_total": len(stage_rows),
            "definitions": {"compare": "Текущий период с начала по сегодня (или целиком, если закончился) против того же числа дней прошлого периода.",
                            "parts_dates": "Для заявок на запчасти дата выигрыша — дата последнего изменения заявки (приблизительно)."}}
