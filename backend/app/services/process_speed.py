"""D-144: «Скорость процесса» — сколько сделка проводит на каждом этапе, где ждут дольше всего и у кого.

Источник — уже накопленная история: смены статуса сделки (журнал действий, action=status) и задачи (создана → выполнена).
Этап сделки = время от входа в статус до выхода из него. Длительности — в календарных часах; на экране — часы или дни.
Исполнение контракта сюда не входит (длится до поставки — это не ожидание).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.workflow import STATUS_TITLES, DealStatus
from ..models import AuditLog, Deal, Task, User
from ..core.clock import msk_date

WHO = {"draft": "менеджер", "logistics_calc": "логист", "service_calc": "сервис", "calc_ready": "КД", "cd_approval": "КД",
       "fd_visa": "ФД", "gd_visa": "ЗГД", "rework": "на доработке", "approved": "менеджер — отправить КП", "kp_sent": "клиент — решение по КП",
       "contract": "менеджер, юрист — контракт"}
ORDER = list(WHO)
SKIP = {"execution", "closed", "won", "lost", "cancelled"}


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def segments(db: Session) -> dict[str, list[tuple[str, datetime, Optional[datetime]]]]:
    """По каждой сделке: [(статус, вход, выход | None — сейчас в нём)]."""
    deals = {d.id: d for d in db.scalars(select(Deal).where(Deal.deleted_at.is_(None))).all()}
    tr: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
    for a in db.scalars(select(AuditLog).where(AuditLog.entity == "deal", AuditLog.action == "status").order_by(AuditLog.at)).all():
        if a.deal_id in deals:
            tr[a.deal_id].append((_utc(a.at), (a.before or {}).get("status", ""), (a.after or {}).get("status", "")))
    out: dict[str, list[tuple[str, datetime, Optional[datetime]]]] = {}
    for did, d in deals.items():
        segs = []
        cur = (tr[did][0][1] if tr[did] else (d.status.value if hasattr(d.status, "value") else str(d.status))) or "draft"
        start = _utc(d.created_at)
        for at, _b, after in tr[did]:
            if at < start:
                at = start
            segs.append((cur, start, at))
            cur, start = after, at
        segs.append((cur, start, None))
        out[did] = segs
    return out


def report(db: Session, days: int = 90, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    segs = segments(db)
    deals = {d.id: d for d in db.scalars(select(Deal).where(Deal.id.in_(list(segs)))).all()}
    done: dict[str, list[float]] = defaultdict(list)
    waiting: dict[str, list[tuple[float, str]]] = defaultdict(list)
    rounds: dict[str, int] = defaultdict(int)
    for did, ss in segs.items():
        for st, a, b in ss:
            if st in SKIP:
                continue
            if b is None:
                waiting[st].append(((now - a).total_seconds() / 3600, did))
            elif b >= since:
                done[st].append((b - a).total_seconds() / 3600)
                if st == "rework":
                    rounds[did] += 1
    stages = []
    for st in ORDER:
        xs = done.get(st, [])
        ws = sorted(waiting.get(st, []), reverse=True)
        if not xs and not ws:
            continue
        stages.append({
            "status": st, "title": STATUS_TITLES.get(DealStatus(st), st), "who": WHO[st], "passes": len(xs),
            "median_h": round(median(xs), 1) if xs else None, "p80_h": round(_pct(xs, 0.8), 1) if xs else None,
            "total_h": round(sum(xs), 1), "now": len(ws),
            "now_longest": [{"deal_id": did, "number": deals[did].number, "hours": round(h, 1)} for h, did in ws[:5]],
        })
    # узкое место: где за период сделки суммарно провели больше всего времени (без «КП у клиента» — это не наша очередь)
    ours = [s for s in stages if s["status"] not in ("kp_sent",) and s["passes"]]
    bottleneck = max(ours, key=lambda s: s["total_h"])["status"] if ours else None
    # сквозные сроки: от создания сделки до первого входа в «Утверждено», «КП отправлено», «Выиграна / контракт»
    def first(ss, names):
        return next((a for st, a, _ in ss if st in names), None)
    e2e: dict[str, list[float]] = defaultdict(list)
    for did, ss in segs.items():
        c = ss[0][1]
        for key, names in (("approved", {"approved"}), ("kp_sent", {"kp_sent"}), ("won", {"won", "contract", "execution", "closed"})):
            t = first(ss, names)
            if t and t >= since:
                e2e[key].append((t - c).total_seconds() / 3600)
    end_to_end = [{"key": k, "title": t, "deals": len(e2e[k]), "median_h": round(median(e2e[k]), 1) if e2e[k] else None,
                   "p80_h": round(_pct(e2e[k], 0.8), 1) if e2e[k] else None}
                  for k, t in (("approved", "заявка → утверждено"), ("kp_sent", "заявка → КП отправлено"), ("won", "заявка → выиграна"))]
    # люди: задачи, выполненные за период, — сколько, медиана от постановки до выполнения, доля в срок; открытые сейчас
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    per: dict[str, dict] = {}
    for t in db.scalars(select(Task).where((Task.done_at >= since) | Task.done_at.is_(None))).all():
        if not t.assignee_id:
            continue
        p = per.setdefault(t.assignee_id, {"user_id": t.assignee_id, "name": names.get(t.assignee_id, ""), "done": 0, "hours": [], "on_time": 0,
                                           "open": 0, "overdue": 0})
        if t.done_at:
            p["done"] += 1
            p["hours"].append((_utc(t.done_at) - _utc(t.created_at)).total_seconds() / 3600)
            if msk_date(_utc(t.done_at)) <= t.due:
                p["on_time"] += 1
        else:
            p["open"] += 1
            if t.due < now.date():
                p["overdue"] += 1
    people = []
    for p in per.values():
        hs = p.pop("hours")
        p["median_h"] = round(median(hs), 1) if hs else None
        p["on_time_pct"] = round(100 * p["on_time"] / p["done"]) if p["done"] else None
        people.append(p)
    people.sort(key=lambda p: (-(p["overdue"]), -(p["median_h"] or 0)))
    return {"days": days, "since": since.date().isoformat(), "stages": stages, "bottleneck": bottleneck, "end_to_end": end_to_end,
            "people": people, "rework_deals": sum(1 for v in rounds.values() if v), "rework_rounds": sum(rounds.values())}
