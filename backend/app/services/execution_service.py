"""Стадия «Заключение и исполнение контракта» (задача 2) и задачи по опциям РФ на этой стадии (задача 6).

Этапы исполнения строятся из плановых дат событий расчёта (базовый сценарий, выбранное клиентом предложение):
подписание контракта с клиентом и заводом, аванс клиента, открытие аккредитива, предоплата заводу, производство,
готовность, окончательный расчёт с заводом, отгрузка, пересечение границы (CMR), выпуск ДТ, доставка, окончательный
расчёт клиента, ПНР, закрытие; параллельно — закупка и установка опций РФ. Этапы по позициям/партиям ведутся отдельно.

Фактическая дата этапа записывается в actual_events позиции/партии (или в поля сделки) и пересчитывает плановые
даты последующих этапов и платежей через расчётное ядро. Фактические платежи вносит ФД — они попадают и в план/факт.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.roles import Role
from ..core.workflow import add_business_days
from ..models import Batch, Deal, ExecutionStage, LocalOption, PlanFactEntry, Position, Task
from . import settings_service
from .deal_calc import run_calc
from .deal_service import audit, notify

# code → (название, уровень: deal|position|batch|option, событие расчёта, ответственные роли, вид: date|payment_in|payment_out, порядок)
STAGE_DEFS: dict[str, dict[str, Any]] = {
    "contract_signed":   {"title": "Подписание контракта с клиентом", "level": "deal", "event": "contract", "roles": ["manager"], "kind": "date", "sort": 10},
    "factory_contract":  {"title": "Подписание контракта с заводом", "level": "deal", "event": "factory_contract", "roles": ["manager"], "kind": "date", "sort": 20},
    "client_advance":    {"title": "Аванс от клиента", "level": "deal", "event": "client_prepayment", "roles": ["fd"], "kind": "payment_in", "sort": 30},
    "lc_opened":         {"title": "Открытие аккредитива клиентом", "level": "deal", "event": "lc_opened", "roles": ["fd"], "kind": "date", "sort": 40},
    "factory_advance":   {"title": "Оплата заводу (аванс)", "level": "position", "event": "factory_prepayment", "roles": ["fd"], "kind": "payment_out", "sort": 50},
    "production":        {"title": "Производство", "level": "position", "event": "production_start", "roles": ["manager", "logistician"], "kind": "date", "sort": 60},
    "readiness":         {"title": "Готовность на заводе", "level": "position", "event": "readiness", "roles": ["logistician", "manager"], "kind": "date", "sort": 70},
    "factory_final":     {"title": "Окончательный расчёт с заводом", "level": "position", "event": "", "roles": ["fd"], "kind": "payment_out", "sort": 80},
    "shipment":          {"title": "Отгрузка", "level": "batch", "event": "shipment", "roles": ["logistician"], "kind": "date", "sort": 90},
    "border":            {"title": "Пересечение границы (CMR с отметкой таможни)", "level": "batch", "event": "border", "roles": ["logistician"], "kind": "date", "sort": 100},
    "declaration":       {"title": "Выпуск ДТ", "level": "batch", "event": "declaration", "roles": ["logistician"], "kind": "date", "sort": 110},
    "delivery":          {"title": "Доставка клиенту", "level": "batch", "event": "delivery", "roles": ["logistician"], "kind": "date", "sort": 120},
    "client_final":      {"title": "Окончательный расчёт клиента", "level": "deal", "event": "", "roles": ["fd"], "kind": "payment_in", "sort": 130},
    "local_option_order":   {"title": "Заказать опцию РФ", "level": "option", "event": "", "roles": ["manager", "cd", "purchasing"], "kind": "date", "sort": 55},
    "local_option_install": {"title": "Установить опцию РФ", "level": "option", "event": "", "roles": ["service_head"], "kind": "date", "sort": 135},
    "commissioning":     {"title": "ПНР", "level": "position", "event": "commissioning", "roles": ["service_head"], "kind": "date", "sort": 140},
    "closing":           {"title": "Закрытие", "level": "deal", "event": "", "roles": ["cd"], "kind": "date", "sort": 150},
}
ADVANCE_EVENTS = {"contract", "client_prepayment", "lc_opened"}
TITLES = {k: v["title"] for k, v in STAGE_DEFS.items()}


def won_variant(deal: Deal):
    if deal.won_variant_id:
        v = next((v for v in deal.variants if v.id == deal.won_variant_id), None)
        if v:
            return v
    return next((v for v in deal.variants if v.in_kp and v.price_rub is not None), deal.variants[0] if deal.variants else None)


def _d(v) -> Optional[date]:
    if not v:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def rebuild_stages(db: Session, deal: Deal) -> list[ExecutionStage]:
    """Пересобирает плановые даты этапов из расчёта, сохраняя уже введённые фактические даты и суммы."""
    inp, res = run_calc(db, deal)
    v = won_variant(deal)
    if v is None:
        return []
    base = res.variants[v.id]["base"]
    existing = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == deal.id)).all()
    by_key = {(s.code, s.position_id, s.batch_id, s.local_option_id): s for s in existing}
    keep: set[str] = set()
    positions = {p.id: p for p in deal.positions if p.id in v.position_ids}
    batches = {b.id: b for b in deal.batches}
    lead = int(settings_service.get(db, "local_options_order_reserve_days") or 14)

    def upsert(code: str, planned: Optional[date], position_id=None, batch_id=None, option_id=None, amount=None, currency="RUB", title=None):
        key = (code, position_id, batch_id, option_id)
        st = by_key.get(key)
        if st is None:
            st = ExecutionStage(deal_id=deal.id, code=code, position_id=position_id, batch_id=batch_id, local_option_id=option_id)
            db.add(st)
            by_key[key] = st
        st.title = title or TITLES[code]
        st.sort = STAGE_DEFS[code]["sort"]
        st.planned_on = planned
        st.responsible_roles = list(STAGE_DEFS[code]["roles"])
        st.event = STAGE_DEFS[code]["event"]
        if amount is not None:
            st.amount, st.currency = amount, currency
        keep.add(id(st))
        return st

    # уровень сделки
    ev_deal: dict[str, date] = {}
    for pc in base.positions:
        for k, dv in pc.events.items():
            ev_deal[k] = max(ev_deal.get(k, dv), dv)
    upsert("contract_signed", deal.contract_signed_at or ev_deal.get("contract"))
    upsert("factory_contract", deal.factory_contract_signed_at or ev_deal.get("factory_contract"))
    client_lines = base.payment_dates
    adv = [l for l in client_lines if l["event"] in ADVANCE_EVENTS]
    if adv:
        upsert("client_advance", min(_d(l["on"]) for l in adv), amount=round(sum(l["amount_rub"] for l in adv), 2))
    if any(l.get("via") == "lc" for l in client_lines):
        upsert("lc_opened", ev_deal.get("lc_opened"))
    final = [l for l in client_lines if l["event"] not in ADVANCE_EVENTS]
    if final:
        upsert("client_final", max(_d(l["on"]) for l in final), amount=round(sum(l["amount_rub"] for l in final), 2))
    # уровень позиции
    for pc in base.positions:
        p = positions.get(pc.position_id)
        if p is None:
            continue
        pay = pc.factory_payment_dates
        if pay:
            first, rest = pay[0], pay[1:]
            upsert("factory_advance", _d(first["on"]), position_id=p.id, amount=first["amount"], currency=first["currency"],
                   title=f"Оплата заводу (аванс) — {p.model}")
            if rest:
                upsert("factory_final", max(_d(l["on"]) for l in rest), position_id=p.id, amount=round(sum(l["amount"] for l in rest), 2),
                       currency=rest[0]["currency"], title=f"Окончательный расчёт с заводом — {p.model}")
        start_ev = "factory_prepayment" if (p.production_term or {}).get("start_event") == "factory_prepayment" else "factory_contract"
        upsert("production", pc.events.get(start_ev), position_id=p.id, title=f"Производство — {p.model}")
        upsert("readiness", pc.events.get("readiness"), position_id=p.id, title=f"Готовность на заводе — {p.model}")
        upsert("commissioning", pc.events.get("commissioning"), position_id=p.id, title=f"ПНР — {p.model}")
        if p.batch_id is None:
            for code in ("shipment", "border", "declaration", "delivery"):
                upsert(code, pc.events.get(code), position_id=p.id, title=f"{TITLES[code]} — {p.model}")
        # опции РФ (задача 6): заказать к дате − срок поставщика − запас; установить до передачи или в ПНР
        for o in p.local_option_rows or []:
            needed = pc.events.get("declaration") if o.install_stage == "before_handover" else pc.events.get("commissioning")
            if needed:
                order_on = max(needed - timedelta(days=(o.supplier_lead_days or 0) + lead), pc.events.get("contract") or needed)
                upsert("local_option_order", order_on, position_id=p.id, option_id=o.id, amount=round((o.cost or 0) * (o.qty or 1), 2),
                       currency=o.currency or "RUB", title=f"Заказать опцию РФ «{o.name}» ({p.model})")
                if o.needs_install:
                    upsert("local_option_install", needed, position_id=p.id, option_id=o.id, title=f"Установить опцию РФ «{o.name}» ({p.model})")
    # уровень партии: даты по самой поздней позиции партии
    for b in batches.values():
        members = [pc for pc in base.positions if positions.get(pc.position_id) and positions[pc.position_id].batch_id == b.id]
        if not members:
            continue
        for code in ("shipment", "border", "declaration", "delivery"):
            planned = max((pc.events.get(code) for pc in members if pc.events.get(code)), default=None)
            upsert(code, planned, batch_id=b.id, title=f"{TITLES[code]} — {b.name}")
    upsert("closing", ev_deal.get("commissioning"))
    # устаревшие этапы (позиция удалена и т. п.) без факта — убираем
    for st in existing:
        if id(st) not in keep and st.actual_on is None:
            db.delete(st)
    db.flush()
    return sorted((s for s in by_key.values() if id(s) in keep), key=lambda s: ((s.actual_on or s.planned_on or date.max), s.sort))


def stage_rows(db: Session, deal: Deal) -> list[dict]:
    rows = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == deal.id)).all()
    pos = {p.id: p for p in deal.positions}
    bat = {b.id: b for b in deal.batches}
    out = []
    for s in sorted(rows, key=lambda s: ((s.actual_on or s.planned_on or date.max), s.sort)):
        out.append({"id": s.id, "code": s.code, "title": s.title, "planned_on": s.planned_on, "actual_on": s.actual_on, "amount": s.amount,
                    "currency": s.currency, "actual_amount": s.actual_amount, "responsible_roles": s.responsible_roles, "kind": STAGE_DEFS[s.code]["kind"],
                    "position_id": s.position_id, "position": pos[s.position_id].model if s.position_id in pos else None,
                    "batch_id": s.batch_id, "batch": bat[s.batch_id].name if s.batch_id in bat else None, "local_option_id": s.local_option_id,
                    "note": s.note, "done": s.actual_on is not None, "overdue": s.actual_on is None and s.planned_on is not None and s.planned_on < date.today()})
    return out


def can_set(user, stage: ExecutionStage) -> bool:
    if user.has(Role.GD):
        return True
    kind = STAGE_DEFS[stage.code]["kind"]
    if kind in ("payment_in", "payment_out") or stage.code == "lc_opened":
        return user.can("execution_payments", 2)
    if stage.code == "closing":
        return user.can("execution_close", 2)
    return user.can("execution", 2) and any(Role(r) in user.roles for r in stage.responsible_roles)


def set_actual(db: Session, deal: Deal, stage: ExecutionStage, actual_on: Optional[date], actual_amount: Optional[float], note: str, user) -> None:
    """Фактическая дата/сумма этапа: пишется в источник (позиция/партия/сделка), платежи — в план/факт; пересчёт плана."""
    stage.actual_on, stage.actual_amount, stage.note, stage.done_by = actual_on, actual_amount, note, user.id
    code, ev = stage.code, STAGE_DEFS[stage.code]["event"]
    if code == "contract_signed":
        deal.contract_signed_at = actual_on
    elif code == "factory_contract":
        deal.factory_contract_signed_at = actual_on
    elif stage.position_id and ev and ev not in ("production_start",):
        p = db.get(Position, stage.position_id)
        if p:
            events = dict(p.actual_events or {})
            if actual_on:
                events[ev] = actual_on.isoformat()
            else:
                events.pop(ev, None)
            p.actual_events = events
    elif stage.batch_id and ev:
        b = db.get(Batch, stage.batch_id)
        if b:
            events = dict(b.actual_events or {})
            if actual_on:
                events[ev] = actual_on.isoformat()
            else:
                events.pop(ev, None)
            b.actual_events = events
            if ev == "border":
                b.border_actual = actual_on
    elif stage.local_option_id:
        o = db.get(LocalOption, stage.local_option_id)
        if o:
            if code == "local_option_order":
                o.ordered_at, o.status = actual_on, ("ordered" if actual_on else o.status)
            else:
                o.installed_at, o.status = actual_on, ("installed" if actual_on else o.status)
    kind = STAGE_DEFS[code]["kind"]
    if kind in ("payment_in", "payment_out") and actual_on and actual_amount:
        db.add(PlanFactEntry(deal_id=deal.id, position_id=stage.position_id, kind=kind, ref=f"stage:{stage.id}", amount=actual_amount,
                             currency=stage.currency or "RUB", on=actual_on, comment=note or stage.title, entered_by=user.id))
    # задачи по этапу закрываем
    for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.done_at.is_(None), Task.block == f"stage:{stage.id}")).all():
        from datetime import datetime, timezone
        t.done_at = datetime.now(timezone.utc)
    audit(db, user.id, deal.id, "execution_stage", stage.id, "actual", after={"actual_on": str(actual_on), "amount": actual_amount, "note": note})
    db.flush()
    rebuild_stages(db, deal)


def create_stage_tasks(db: Session, deal: Deal, user_id: Optional[str]) -> int:
    """Задачи на этапы исполнения с ответственными: опции РФ (заказать/установить), ввод дат по ролям. Без дублей."""
    n = 0
    existing = {t.block for t in db.scalars(select(Task).where(Task.deal_id == deal.id, Task.done_at.is_(None))).all()}
    from ..services.deal_service import _assignee
    from ..core.workflow import TaskSpec
    for s in db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == deal.id, ExecutionStage.actual_on.is_(None))).all():
        block = f"stage:{s.id}"
        if block in existing or not s.planned_on:
            continue
        roles = [Role(r) for r in s.responsible_roles if r in Role._value2member_map_]
        if not roles:
            continue
        role = roles[0]
        due = s.planned_on if s.code.startswith("local_option") else add_business_days(s.planned_on, 1)
        spec = TaskSpec(role, s.title, due, block)
        assignee = _assignee(db, deal, spec)
        db.add(Task(deal_id=deal.id, role=role.value, assignee_id=assignee, title=s.title, block=block, due=due))
        if s.code.startswith("local_option"):
            notify(db, deal, assignee, "task_new", ["app", "outlook"], f"{deal.number}: {s.title} — срок {due:%d.%m.%Y}")
        n += 1
    return n
