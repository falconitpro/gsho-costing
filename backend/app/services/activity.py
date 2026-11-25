"""D-175 (пакет A2, согласовано владельцем 26.11): «Последняя активность» на рабочем месте — только чтение.

Источник — журнал действий (audit_log), только записи по сделкам, которые сотрудник и так видит (can_see_deal),
и только понятные человеку события из белого списка. Суммы, цены и содержимое обсуждений не выводятся;
события, которые раскрывают закрытые разделы, требуют того же уровня доступа, что и сам раздел.
Задачи и поручения сюда не попадают (у них свои блоки).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser
from ..core.workflow import STATUS_TITLES, DealStatus
from ..models import AuditLog, Deal, ExecutionStage, User
from .acceptance_service import DECISIONS

MAX_LIMIT = 50


def _d(v: Any) -> str:
    try:
        return date.fromisoformat(str(v)[:10]).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ""


def _status(v: Any) -> str:
    try:
        return STATUS_TITLES.get(DealStatus(v), str(v))
    except ValueError:
        return str(v or "")


# (сущность, действие) → (группа доступа или None, текст события по записи журнала, хвост адреса карточки сделки)
Rule = tuple[Optional[str], Callable[[AuditLog, dict], str], str]
RULES: dict[tuple[str, str], Rule] = {
    ("deal", "create"): (None, lambda e, x: "Создана сделка", ""),
    ("deal", "status"): (None, lambda e, x: f"Статус: {_status((e.after or {}).get('status'))}", ""),
    ("deal", "rework_reason"): (None, lambda e, x: f"Возврат на доработку: {(e.after or {}).get('title', '')}".rstrip(": "), ""),
    ("deal", "contract_step"): ("legal_docs", lambda e, x: f"Договор: {(e.after or {}).get('step', 'шаг')}{' — выполнено' if (e.after or {}).get('done') else ''}", "?tab=legal"),
    ("deal", "payment_terms"): ("payment_terms", lambda e, x: "Изменены условия оплаты", ""),
    ("version", "create"): (None, lambda e, x: "Сформирован расчёт" + (f", версия {(e.after or {})['version_no']}" if (e.after or {}).get('version_no') else ""), "?tab=pricing"),
    ("position", "create"): (None, lambda e, x: f"Добавлена позиция {(e.after or {}).get('model', '')}".rstrip(), ""),
    ("negotiation", "negotiation_step"): ("price", lambda e, x: f"Переговоры: шаг {(e.after or {}).get('no', '')}".rstrip(), "/negotiation"),
    ("execution_stage", "actual"): (None, lambda e, x: f"Выполнен этап «{x.get('stage', 'этап')}»", "?tab=execution"),
    ("execution_stage", "refine"): (None, lambda e, x: f"Новый срок этапа «{x.get('stage', 'этап')}» — {_d((e.after or {}).get('refined_on'))}", "?tab=execution"),
    ("acceptance_unit", "decision"): (None, lambda e, x: f"Приёмка {(e.after or {}).get('serial', '')}: {DECISIONS.get((e.after or {}).get('decision', ''), '')}".replace("  ", " "), "?tab=acceptance"),
}


def build(db: Session, user: CurrentUser, can_see: Callable[[CurrentUser, Deal], bool], limit: int = 15,
          before: Optional[datetime] = None) -> dict:
    limit = max(1, min(int(limit or 15), MAX_LIMIT))
    allowed = [k for k, (grp, _, _) in RULES.items() if grp is None or user.can(grp)]
    items: list[dict] = []
    cursor = before
    deals: dict[str, Optional[Deal]] = {}
    # читаем журнал порциями, пока не наберём нужное число видимых событий (сделки вне доступа пропускаются)
    for _ in range(8):
        q = select(AuditLog).where(AuditLog.deal_id.is_not(None), tuple_(AuditLog.entity, AuditLog.action).in_(allowed))
        if cursor is not None:
            q = q.where(AuditLog.at < cursor)
        rows = db.scalars(q.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit * 6)).all()
        if not rows:
            break
        need = {r.deal_id for r in rows} - deals.keys()
        if need:
            for d in db.scalars(select(Deal).where(Deal.id.in_(need))).all():
                deals[d.id] = d if can_see(user, d) else None
            for k in need:
                deals.setdefault(k, None)
        stage_ids = {r.entity_id for r in rows if r.entity == "execution_stage"}
        stages = {s.id: s.title for s in db.scalars(select(ExecutionStage).where(ExecutionStage.id.in_(stage_ids))).all()} if stage_ids else {}
        for r in rows:
            d = deals.get(r.deal_id)
            if d is None:
                continue
            grp, text, tab = RULES[(r.entity, r.action)]
            items.append({"id": r.id, "at": r.at.isoformat() if r.at else None, "user_id": r.user_id, "kind": f"{r.entity}.{r.action}",
                          "title": text(r, {"stage": stages.get(r.entity_id, "")}),
                          "deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "",
                          "url": f"/deals/{d.id}{tab}"})
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
        cursor = rows[-1].at
    names = {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_({i["user_id"] for i in items if i["user_id"]}))).all()} if items else {}
    for i in items:
        uid = i.pop("user_id")
        i["author"] = names.get(uid, "") if uid else "система"
    more = len(items) >= limit
    return {"items": items, "next_before": items[-1]["at"] if more and items else None}
