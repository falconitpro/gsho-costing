"""Заказ-наряды сервиса (I-0, D-191): статусы, переходы, применение событий и правила конфликтов.

Один путь изменения наряда — событие (WorkOrderEvent). Веб отправляет его сразу, будущее приложение без сети (I-4) —
из очереди после восстановления связи; сервер применяет оба одинаково. Протокол — docs/operations/21_….

Правила конфликтов (без «последняя запись побеждает»):
- base_version == версия наряда → событие применяется, версия +1;
- base_version > версии → отклонено (отправитель не мог видеть будущую версию);
- base_version < версии (отправитель видел старое состояние):
  · note — объединяется всегда (заметки только добавляются);
  · checklist — объединяется, если тот же пункт после base_version никто не отмечал иначе; иначе конфликт;
  · смена статуса — применяется, только если после base_version не было других смен статуса и назначений
    и переход допустим из текущего статуса; иначе конфликт;
  · assign / plan — конфликт, если после base_version было назначение или перенос сроков.
- Конфликт не меняет наряд: событие сохраняется с result=conflict, руководитель сервиса решает его событием resolve.
- Закрытие без связи не проводится (решение 5 владельца): наряд переходит в «готов к закрытию», подпись сохраняется.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.roles import EDIT, Role
from ..models import User, ServiceRequest, ServiceWorkType, WorkOrder, WorkOrderChecklistItem, WorkOrderEvent
from .change_events import record
from .deal_service import audit

STATUSES = {
    "draft": "Черновик", "assigned": "Назначен", "in_progress": "В работе", "on_hold": "Приостановлен",
    "ready_to_close": "Готов к закрытию", "closed": "Закрыт", "cancelled": "Отменён",
}
FINAL = {"closed", "cancelled"}
REQUEST_STATUSES = {"new": "Новое", "accepted": "Принято", "rejected": "Отклонено", "done": "Выполнено"}
CHANNELS = {"phone": "Телефон", "email": "Почта", "visit": "Визит", "portal": "Портал", "other": "Другое"}
CATEGORIES = {"diagnostics": "Диагностика", "maintenance": "Техническое обслуживание", "repair": "Ремонт",
              "commissioning": "Пусконаладка", "other": "Другое"}

# переход: событие → (из каких статусов, в какой статус)
TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "start": ({"assigned"}, "in_progress"),
    "hold": ({"in_progress"}, "on_hold"),
    "resume": ({"on_hold"}, "in_progress"),
    "ready": ({"in_progress"}, "ready_to_close"),
    "reopen": ({"ready_to_close"}, "in_progress"),
    "close": ({"in_progress", "ready_to_close"}, "closed"),
    "cancel": ({"draft", "assigned", "in_progress", "on_hold"}, "cancelled"),
}
STATUS_KINDS = set(TRANSITIONS) | {"assign"}
HEAD_ONLY = {"assign", "plan", "cancel", "resolve"}
KINDS = STATUS_KINDS | {"plan", "note", "checklist", "resolve"}
KIND_TITLES = {"create": "Создан", "assign": "Назначен инженер", "plan": "Сроки", "start": "Начат", "hold": "Приостановлен",
               "resume": "Возобновлён", "ready": "Готов к закрытию", "reopen": "Возвращён в работу", "close": "Закрыт",
               "cancel": "Отменён", "note": "Заметка", "checklist": "Чек-лист", "resolve": "Решение по конфликту"}


class Forbidden(Exception):
    pass


@dataclass
class Actor:
    id: str
    is_head: bool            # руководитель сервиса / ЗГД: назначает, отменяет, решает конфликты
    engineer_only: bool


def actor_of(user) -> Actor:
    eng_only = bool(user.roles) and all(r == Role.ENGINEER for r in user.roles)
    return Actor(id=user.id, is_head=user.can("service_orders", EDIT) and not eng_only, engineer_only=eng_only)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _number(db: Session, model, prefix: str) -> str:
    p = f"{prefix}-{date.today().year}-"
    last = db.scalars(select(model.number).where(model.number.like(f"{p}%"))).all()
    n = max([int(x.rsplit("-", 1)[1]) for x in last if x.rsplit("-", 1)[1].isdigit()] or [0]) + 1
    return f"{p}{n:04d}"


def request_number(db: Session) -> str:
    return _number(db, ServiceRequest, "СО")


def order_number(db: Session) -> str:
    return _number(db, WorkOrder, "НР")


def create_order(db: Session, actor: Actor, *, client_id: str, site_id: Optional[str] = None, machine_id: Optional[str] = None,
                 request_id: Optional[str] = None, work_type_id: Optional[str] = None, description: str = "") -> WorkOrder:
    if not actor.is_head:
        raise Forbidden("Наряды создаёт руководитель сервиса")
    wo = WorkOrder(id=str(uuid.uuid4()), number=order_number(db), client_id=client_id, site_id=site_id, machine_id=machine_id,
                   request_id=request_id, work_type_id=work_type_id, description=description, status="draft", version=1, created_by=actor.id)
    db.add(wo)
    db.flush()
    wt = db.get(ServiceWorkType, work_type_id) if work_type_id else None
    for i, text in enumerate((wt.checklist if wt else None) or [], start=1):
        db.add(WorkOrderChecklistItem(work_order_id=wo.id, position=i, text=str(text)[:2000], required=True))
    db.add(WorkOrderEvent(id=str(uuid.uuid4()), work_order_id=wo.id, kind="create", base_version=0, new_version=1, payload={},
                          actor_id=actor.id, device_id="web", occurred_at=_now(), result="applied"))
    audit(db, actor.id, None, "work_order", wo.id, "create", after={"number": wo.number, "client_id": client_id})
    return wo


def _required_open(db: Session, wo: WorkOrder) -> int:
    return sum(1 for i in db.scalars(select(WorkOrderChecklistItem).where(WorkOrderChecklistItem.work_order_id == wo.id)).all()
               if i.required and not i.done)


def _later(db: Session, wo: WorkOrder, base: int) -> list[WorkOrderEvent]:
    """Принятые события после версии base (то, чего отправитель не видел)."""
    return db.scalars(select(WorkOrderEvent).where(WorkOrderEvent.work_order_id == wo.id, WorkOrderEvent.new_version.is_not(None),
                                                   WorkOrderEvent.new_version > base)).all()


def _stale_conflict(db: Session, wo: WorkOrder, kind: str, payload: dict, base: int) -> str:
    """Причина конфликта для события по устаревшей версии или пустая строка, если его можно объединить."""
    later = _later(db, wo, base)
    if kind == "note":
        return ""
    if kind == "checklist":
        item = str(payload.get("item_id") or "")
        for e in later:
            if e.kind == "checklist" and str(e.payload.get("item_id")) == item and bool(e.payload.get("done")) != bool(payload.get("done")):
                return "Этот пункт чек-листа уже отметили иначе"
        return ""
    if kind in ("assign", "plan"):
        if any(e.kind in ("assign", "plan") for e in later):
            return "Назначение или сроки уже изменили"
        return ""
    if any(e.kind in STATUS_KINDS or (e.kind == "resolve" and e.payload.get("decision") == "accept") for e in later):
        return f"Статус уже изменили: сейчас «{STATUSES[wo.status]}»"
    return ""


def _check_rights(wo: WorkOrder, actor: Actor, kind: str) -> None:
    if kind in HEAD_ONLY:
        if not actor.is_head:
            raise Forbidden("Это действие — руководителя сервиса")
        return
    if not (actor.is_head or (wo.engineer_id and wo.engineer_id == actor.id)):
        raise Forbidden("Наряд назначен другому инженеру")


def _apply(db: Session, wo: WorkOrder, actor: Actor, kind: str, payload: dict, offline: bool) -> tuple[str, str]:
    """Проверить и выполнить действие над нарядом. Возвращает (result, reason); applied/merged меняют наряд."""
    if kind == "note":
        if not str(payload.get("text") or "").strip():
            return "rejected", "Пустая заметка"
        return "applied", ""
    if kind == "checklist":
        item = db.get(WorkOrderChecklistItem, str(payload.get("item_id") or ""))
        if item is None or item.work_order_id != wo.id:
            return "rejected", "Нет такого пункта чек-листа"
        if wo.status not in ("assigned", "in_progress", "on_hold"):
            return "rejected", f"Чек-лист нельзя менять в статусе «{STATUSES[wo.status]}»"
        item.done = bool(payload.get("done"))
        item.done_by, item.done_at = (actor.id, _now()) if item.done else (None, None)
        item.note = str(payload.get("note") or item.note or "")[:2000]
        return "applied", ""
    if wo.status in FINAL:
        return "rejected", f"Наряд уже {STATUSES[wo.status].lower()}"
    if kind == "assign":
        eng = str(payload.get("engineer_id") or "")
        if not eng:
            return "rejected", "Не выбран инженер"
        u = db.get(User, eng)
        if u is None or not u.is_active or not {"engineer", "service_head"} & set(u.roles or []):
            return "rejected", "Назначить можно только действующего инженера или руководителя сервиса"
        wo.engineer_id, wo.assigned_by = eng, actor.id
        if wo.status == "draft":
            wo.status = "assigned"
        return "applied", ""
    if kind == "plan":
        for k in ("planned_start", "planned_end"):
            if k in payload:
                v = payload.get(k)
                setattr(wo, k, datetime.fromisoformat(v) if v else None)
        if wo.planned_start and wo.planned_end and wo.planned_end < wo.planned_start:
            return "rejected", "Окончание раньше начала"
        return "applied", ""
    if kind == "close" and offline:
        # решение 5: без связи наряд не закрывается — «готов к закрытию», подпись сохраняется
        if wo.status not in ("in_progress", "ready_to_close"):
            return "rejected", f"Из статуса «{STATUSES[wo.status]}» закрыть нельзя"
        if _required_open(db, wo):
            return "rejected", "Не выполнены обязательные пункты чек-листа"
        wo.signed_by_name = str(payload.get("signed_by_name") or wo.signed_by_name)[:255]
        wo.signed_at = wo.signed_at or _now()
        wo.status = "ready_to_close"
        return "applied", "Без связи закрытие не проводится: наряд готов к закрытию"
    src, dst = TRANSITIONS[kind]
    if wo.status not in src:
        return "rejected", f"Из статуса «{STATUSES[wo.status]}» действие «{KIND_TITLES[kind]}» недоступно"
    if kind == "start" and not wo.engineer_id:
        return "rejected", "Инженер не назначен"
    if kind in ("ready", "close") and _required_open(db, wo):
        return "rejected", "Не выполнены обязательные пункты чек-листа"
    if kind == "close":
        name = str(payload.get("signed_by_name") or wo.signed_by_name or "").strip()
        if not name:
            return "rejected", "Нужна подпись представителя клиента"
        wo.signed_by_name, wo.signed_at, wo.closed_at = name[:255], wo.signed_at or _now(), _now()
    if kind == "cancel" and not str(payload.get("reason") or "").strip():
        return "rejected", "Укажите причину отмены"
    wo.status = dst
    return "applied", ""


def submit(db: Session, wo: WorkOrder, actor: Actor, *, event_id: Optional[str], kind: str, base_version: int,
           payload: Optional[dict] = None, device_id: str = "web", offline: bool = False,
           occurred_at: Optional[datetime] = None) -> WorkOrderEvent:
    """Принять событие наряда. Повтор того же id возвращает сохранённый результат и ничего не меняет."""
    payload = dict(payload or {})
    event_id = event_id or str(uuid.uuid4())
    prev = db.get(WorkOrderEvent, event_id)
    if prev is not None:
        if prev.work_order_id != wo.id or prev.actor_id != actor.id or prev.kind != kind:
            raise ValueError("Событие с таким идентификатором уже есть у другого наряда или автора")
        return prev
    if kind not in KINDS:
        raise ValueError(f"Неизвестное событие «{kind}»")
    _check_rights(wo, actor, kind)
    ev = WorkOrderEvent(id=event_id, work_order_id=wo.id, kind=kind, base_version=int(base_version), payload=payload,
                        actor_id=actor.id, device_id=(device_id or "web")[:64], offline=bool(offline),
                        occurred_at=occurred_at or _now())
    before = {"status": wo.status, "engineer_id": wo.engineer_id, "version": wo.version}

    if kind == "resolve":
        result, reason = _resolve(db, wo, actor, payload)
    elif ev.base_version > wo.version:
        result, reason = "rejected", "Версия наряда у отправителя новее серверной"
    elif ev.base_version < wo.version and (why := _stale_conflict(db, wo, kind, payload, ev.base_version)):
        result, reason = "conflict", why
    else:
        result, reason = _apply(db, wo, actor, kind, payload, offline)
        if result == "applied" and ev.base_version < wo.version:
            result = "merged"
    ev.result, ev.reason = result, reason
    if result in ("applied", "merged"):
        wo.version += 1
        ev.new_version = wo.version
    db.add(ev)
    db.flush()
    if result in ("applied", "merged"):
        audit(db, actor.id, None, "work_order", wo.id, kind, before=before,
              after={"status": wo.status, "engineer_id": wo.engineer_id, "version": wo.version}, comment=reason)
        _sync_request(db, wo)
    elif result == "conflict":
        record(db, "work_order", wo.id, "conflict", actor_id=actor.id)
    return ev


def _resolve(db: Session, wo: WorkOrder, actor: Actor, payload: dict) -> tuple[str, str]:
    """Решение руководителя по конфликту: accept — выполнить исходное действие над текущим нарядом, dismiss — отклонить."""
    src = db.get(WorkOrderEvent, str(payload.get("event_id") or ""))
    if src is None or src.work_order_id != wo.id or src.result != "conflict":
        return "rejected", "Нет такого конфликта"
    if open_conflicts(db, wo.id, only_ids=True).count(src.id) == 0:
        return "rejected", "Конфликт уже решён"
    decision = payload.get("decision")
    if decision == "dismiss":
        return "applied", "Конфликт отклонён"
    if decision != "accept":
        return "rejected", "Решение — accept или dismiss"
    # действие выполняется с правами руководителя, но проверки статусов и чек-листа — те же
    result, reason = _apply(db, wo, actor, src.kind, src.payload or {}, offline=False)
    return result, reason or "Принято руководителем"


def open_conflicts(db: Session, order_id: str, only_ids: bool = False) -> list:
    evs = db.scalars(select(WorkOrderEvent).where(WorkOrderEvent.work_order_id == order_id).order_by(WorkOrderEvent.received_at)).all()
    done = {str(e.payload.get("event_id")) for e in evs if e.kind == "resolve" and e.result == "applied"}
    out = [e for e in evs if e.result == "conflict" and e.id not in done]
    return [e.id for e in out] if only_ids else out


def _sync_request(db: Session, wo: WorkOrder) -> None:
    """Обращение «выполнено», когда закрыт его наряд (отменённый наряд обращение не закрывает)."""
    if wo.request_id and wo.status == "closed":
        req = db.get(ServiceRequest, wo.request_id)
        if req is not None and req.status == "accepted":
            req.status = "done"


def event_payload(e: WorkOrderEvent, names: Optional[dict[str, str]] = None) -> dict[str, Any]:
    return {"id": e.id, "kind": e.kind, "kind_title": KIND_TITLES.get(e.kind, e.kind), "base_version": e.base_version,
            "new_version": e.new_version, "payload": e.payload or {}, "actor_id": e.actor_id,
            "actor_name": (names or {}).get(e.actor_id or "", ""), "device_id": e.device_id, "offline": e.offline,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "received_at": e.received_at.isoformat() if e.received_at else None, "result": e.result, "reason": e.reason}
