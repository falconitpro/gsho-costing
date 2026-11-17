"""Срок производства позиции — одно поле для всех расчётов (D-065).

Хранится в `Position.production_term`: значение, единица (календарные / рабочие дни, недели, месяцы или конкретная дата),
точка отсчёта (контракт с заводом или предоплата заводу) и служебные отметки: откуда значение, кто и когда задал.
`Position.production_days` — только производное значение в календарных днях (справки, старые выгрузки).
К дате готовности (точка отсчёта + срок производства) расчёт прибавляет все остальные сроки: отгрузка, транзит,
таможня, доставка по РФ, установка опций, ПНР.

Правка КД или ЗГД фиксирует срок: автоматические источники (ответ завода, каталог, копирование) его больше не
перезаписывают, менеджер изменить не может. Каждое изменение — запись в журнале действий (история на экране).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

UNITS = ("calendar_days", "working_days", "weeks", "months", "date")
UNIT_TITLES = {"calendar_days": "календарных дней", "working_days": "рабочих дней", "weeks": "недель", "months": "месяцев", "date": "конкретная дата"}
START_EVENTS = ("factory_contract", "factory_prepayment")
SOURCE_TITLES = {"manager": "менеджер", "cd": "КД", "gd": "ЗГД", "offer": "ответ завода", "catalog": "каталог (последняя цена завода)",
                 "rfq": "RFQ", "copy": "копия сделки", "mirror": "ответ поставщика компании группы"}
FIXED_BY = ("cd", "gd")
DEFAULT_DAYS = 90
META = ("source", "set_by", "set_at", "comment", "fixed")


def normalize(raw: Optional[dict]) -> dict:
    """Только значащие поля срока; пусто — {}."""
    t = raw or {}
    unit = t.get("unit") if t.get("unit") in UNITS else "calendar_days"
    start = t.get("start_event") if t.get("start_event") in START_EVENTS else "factory_contract"
    if unit == "date":
        fd = t.get("fixed_date")
        fd = fd.isoformat() if isinstance(fd, date) else (str(fd)[:10] if fd else None)
        return {"unit": "date", "fixed_date": fd, "start_event": start} if fd else {}
    try:
        value = float(t.get("value"))
    except (TypeError, ValueError):
        return {}
    if value <= 0:
        return {}
    return {"value": int(value) if value == int(value) else value, "unit": unit, "start_event": start}


def calendar_days(term: Optional[dict], fallback: int = DEFAULT_DAYS) -> int:
    """Срок в календарных днях (приблизительно для рабочих дней и месяцев) — для справок и сравнения."""
    t = normalize(term)
    if not t or t["unit"] == "date":
        return fallback
    v = float(t["value"])
    return int(round(v * {"weeks": 7, "working_days": 7 / 5, "months": 30.44}.get(t["unit"], 1)))


def text(term: Optional[dict]) -> str:
    t = normalize(term)
    if not t:
        return ""
    if t["unit"] == "date":
        return f"до {t['fixed_date']}"
    start = "с подписания контракта с заводом" if t["start_event"] == "factory_contract" else "с предоплаты заводу"
    return f"{t['value']:g} {UNIT_TITLES[t['unit']]} {start}"


def is_fixed(pos: Any) -> bool:
    return bool((pos.production_term or {}).get("fixed"))


def info(pos: Any, users: Optional[dict[str, str]] = None) -> dict:
    """Что использует расчёт: значение, откуда, кем и когда задано; пусто — расчёт по умолчанию."""
    raw = pos.production_term or {}
    t = normalize(raw)
    return {"term": t, "text": text(t) or f"не задан — расчёт по {DEFAULT_DAYS} календарных дней", "missing": not t,
            "source": raw.get("source") or "", "source_title": SOURCE_TITLES.get(raw.get("source") or "", ""),
            "set_by": raw.get("set_by"), "set_by_name": (users or {}).get(raw.get("set_by") or "", ""), "set_at": raw.get("set_at"),
            "comment": raw.get("comment") or "", "fixed": bool(raw.get("fixed")), "calendar_days": calendar_days(t)}


class FixedTermError(Exception):
    """Срок зафиксирован КД / ЗГД — менеджер изменить не может."""


def set_term(db, pos: Any, raw: Optional[dict], user_id: Optional[str], source: str, comment: str = "") -> bool:
    """Записать срок производства. Возвращает True, если значение изменилось.

    Автоматические источники не трогают срок, зафиксированный КД / ЗГД; менеджер при попытке изменить — FixedTermError.
    """
    from .deal_service import audit
    new = normalize(raw)
    old_raw = dict(pos.production_term or {})
    old = normalize(old_raw)
    fixing = source in FIXED_BY
    if not fixing and old_raw.get("fixed"):
        if new and new != old:
            if source == "manager":
                raise FixedTermError(f"Срок производства зафиксирован ({SOURCE_TITLES.get(old_raw.get('source'), 'КД / ЗГД')}): {text(old)}. Изменить его могут КД или ЗГД.")
            if pos.id:
                audit(db, user_id, pos.deal_id, "position", pos.id, "production_term_kept", before=old_raw, after=new,
                      comment=f"{SOURCE_TITLES.get(source, source)} предлагает {text(new)} — оставлен зафиксированный срок")
        return False
    if not new and not fixing:
        return False                     # пустое значение из автоматического источника не стирает введённое
    if new == old and not (fixing and not old_raw.get("fixed")) and not (fixing and comment and comment != old_raw.get("comment")):
        return False
    stored = {**new, "source": source, "set_by": user_id, "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "comment": comment, "fixed": fixing or False}
    pos.production_term = stored
    pos.production_days = calendar_days(new, pos.production_days or DEFAULT_DAYS)
    if pos.id:
        audit(db, user_id, pos.deal_id, "position", pos.id, "production_term", before=old_raw or None, after=stored,
              comment=f"{SOURCE_TITLES.get(source, source)}" + (f": {comment}" if comment else ""))
    return True


def history(db, pos: Any) -> list[dict]:
    from sqlalchemy import select
    from ..models import AuditLog, User
    rows = db.scalars(select(AuditLog).where(AuditLog.entity == "position", AuditLog.entity_id == pos.id,
                                             AuditLog.action.in_(["production_term", "production_term_kept"])).order_by(AuditLog.at.desc())).all()
    names = {u.id: u.display_name or u.email for u in db.scalars(select(User).where(User.id.in_({r.user_id for r in rows if r.user_id}))).all()} if rows else {}
    return [{"at": r.at.isoformat(), "user": names.get(r.user_id or "", ""), "action": r.action,
             "before": text(r.before), "after": text(r.after), "comment": r.comment} for r in rows]
