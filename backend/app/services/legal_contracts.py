"""Реестр договоров (юридический блок, этап 1): справочники, нумерация, график платежей по событиям,
неустойки по умолчанию, проверки полноты, импорт договоров из сделок и юридических пакетов."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..calc.calendar import WorkCalendar
from ..calc.types import DEFAULT_CLIENT_PAYMENTS, DEFAULT_FACTORY_PAYMENTS, EVENT_TITLES
from ..models import Client, Contract, Deal, ExecutionStage, Factory, LegalEntity, LegalPackage, User

# ------------------------------------------------------------------ справочники
KINDS: dict[str, dict[str, str]] = {
    "client_supply":  {"title": "С клиентом: поставка ГШО", "direction": "income", "cp_kind": "client"},
    "client_parts":   {"title": "С клиентом: запасные части", "direction": "income", "cp_kind": "client"},
    "client_service": {"title": "С клиентом: сервис", "direction": "income", "cp_kind": "client"},
    "supplier":       {"title": "С поставщиком / заводом", "direction": "expense", "cp_kind": "factory"},
    "logistics":      {"title": "Перевозка / экспедирование", "direction": "expense", "cp_kind": "other"},
    "customs":        {"title": "Таможенный брокер", "direction": "expense", "cp_kind": "other"},
    "works":          {"title": "Подряд: монтаж, ПНР, доукомплектование", "direction": "expense", "cp_kind": "other"},
    "certification":  {"title": "Сертификация", "direction": "expense", "cp_kind": "other"},
    "agency":         {"title": "Агентский", "direction": "expense", "cp_kind": "other"},
    "lease":          {"title": "Аренда", "direction": "expense", "cp_kind": "other"},
    "services":       {"title": "Прочие услуги", "direction": "expense", "cp_kind": "other"},
    "nda":            {"title": "NDA / соглашение без оплаты", "direction": "none", "cp_kind": "other"},
    "other":          {"title": "Прочий", "direction": "none", "cp_kind": "other"},
}
DIRECTIONS = {"income": "доходный (платят нам)", "expense": "расходный (платим мы)", "none": "без денежного потока"}
DOC_TYPES = {"contract": "Договор", "amendment": "Дополнительное соглашение", "specification": "Спецификация"}
STATUSES = {"draft": "проект", "legal_review": "юридическая проверка", "approval": "согласование", "approved": "утверждён", "signing": "на подписании",
            "active": "действует", "closed": "исполнен", "terminated": "расторгнут", "cancelled": "аннулирован"}
LOCKED = {"active", "closed", "terminated"}          # подписанные: условия меняются только доп. соглашением
FILE_KINDS = {"draft": "Проект (версия)", "disagreements": "Протокол разногласий", "conclusion": "Заключение юриста", "for_signing": "Редакция на подпись",
              "signed_scan": "Подписанный скан", "edo": "Файл ЭДО с ЭП / квитанции", "correspondence": "Переписка", "reconciliation": "Акт сверки", "other": "Прочее"}
# события-основания: расчётные события сделки + события договора
EVENTS: dict[str, str] = {**{k.value: v for k, v in EVENT_TITLES.items()},
                          "invoice": "выставление счёта", "act_signed": "подписание УПД / акта", "batch_delivery": "поставка партии",
                          "fixed_date": "фиксированная дата", "monthly": "ежемесячно, N-го числа"}
EVENTS["contract"] = "подписание договора"
EVENTS["factory_contract"] = "подписание договора с заводом"
STAGE_BY_EVENT = {"client_prepayment": "client_advance", "lc_opened": "lc_opened", "factory_prepayment": "factory_advance", "readiness": "readiness",
                  "shipment": "shipment", "border": "border", "declaration": "declaration", "delivery": "delivery", "commissioning": "commissioning",
                  "batch_delivery": "delivery", "act_signed": "delivery"}
PERFORMANCE_CODES = {"delivery": "поставка", "readiness": "готовность на заводе", "shipment": "отгрузка", "works": "выполнение работ", "commissioning": "ПНР", "defects": "устранение недостатков", "other": "прочее"}
PENALTY_PAYERS = {"cp": "контрагент нам", "us": "мы контрагенту"}
PENALTY_REASONS = {"late_payment": "просрочка оплаты", "late_delivery": "просрочка поставки / готовности", "late_works": "просрочка работ (ПНР, монтаж)",
                   "late_defects": "просрочка устранения недостатков", "other": "прочее нарушение (штраф)"}
RATE_KINDS = {"pct_day": "% в день", "pct_week": "% в неделю", "fixed_day": "сумма в день", "one_time": "разовый штраф", "key_rate": "ключевая ставка ЦБ / N"}
PENALTY_BASES = {"overdue_amount": "просроченная сумма", "undelivered_value": "стоимость не исполненного в срок", "contract_price": "цена договора"}
CAP_KINDS = {"none": "без предела", "pct_base": "% базы", "pct_contract": "% цены договора", "amount": "сумма"}
NUM_PREFIX = {"income": "К", "expense": "П", "none": "С"}

DEFAULT_PENALTY_SETTINGS = {
    "late_delivery": {"rate_kind": "pct_day", "rate": 0.1, "base": "undelivered_value", "grace_days": 0, "cap_kind": "pct_base", "cap_value": 5},
    "late_payment": {"rate_kind": "pct_day", "rate": 0.1, "base": "overdue_amount", "grace_days": 0, "cap_kind": "none", "cap_value": None},
    "liability_cap_pct": 10,
}


def catalog() -> dict:
    return {"kinds": KINDS, "directions": DIRECTIONS, "doc_types": DOC_TYPES, "statuses": STATUSES, "file_kinds": FILE_KINDS, "events": EVENTS,
            "performance_codes": PERFORMANCE_CODES, "penalty_payers": PENALTY_PAYERS, "penalty_reasons": PENALTY_REASONS, "rate_kinds": RATE_KINDS,
            "penalty_bases": PENALTY_BASES, "cap_kinds": CAP_KINDS}


def penalty_defaults(db: Session) -> dict:
    from . import settings_service
    cur = settings_service.get(db, "contract_penalty_defaults") or {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_PENALTY_SETTINGS.items()}
    for k, v in cur.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        elif v is not None:
            out[k] = v
    return out


def default_penalties(db: Session, direction: str) -> tuple[list[dict], Optional[float]]:
    """Типовые неустойки (правила юридического блока; меняет ЗГД в Настройках): за просрочку поставки платит поставщик, за просрочку оплаты — покупатель."""
    if direction not in ("income", "expense"):
        return [], None
    d = penalty_defaults(db)
    supplier_side = "us" if direction == "income" else "cp"
    buyer_side = "cp" if direction == "income" else "us"
    rows = [{"payer": supplier_side, "reason": "late_delivery", "term": "delivery", **d["late_delivery"], "note": "типовое условие"},
            {"payer": buyer_side, "reason": "late_payment", "term": "", **d["late_payment"], "note": "типовое условие"}]
    return rows, d.get("liability_cap_pct")


def next_number(db: Session, entity: Optional[LegalEntity], direction: str, on: date) -> tuple[str, int]:
    """Внутренний номер: <К|П|С>-<код компании>-<год>-<порядковый в пределах компании и года>."""
    code = (entity.code if entity else "XX").upper()
    start, end = datetime(on.year, 1, 1, tzinfo=timezone.utc), datetime(on.year + 1, 1, 1, tzinfo=timezone.utc)
    q = select(func.max(Contract.seq)).where(Contract.created_at >= start, Contract.created_at < end)
    q = q.where(Contract.own_entity_id == entity.id) if entity else q.where(Contract.own_entity_id.is_(None))
    seq = (db.scalar(q) or 0) + 1
    return f"{NUM_PREFIX.get(direction, 'С')}-{code}-{on.year}-{seq:03d}", seq


def work_calendar(db: Session) -> WorkCalendar:
    from . import settings_service
    hol = []
    for x in settings_service.get(db, "rf_holidays") or []:
        try:
            hol.append(date.fromisoformat(str(x)[:10]))
        except ValueError:
            pass
    return WorkCalendar(holidays=hol)


def _plus(cal: WorkCalendar, on: date, days: int, kind: str) -> date:
    if kind == "working":
        return cal.add_working_days(on, days)
    return on + timedelta(days=days)


# ------------------------------------------------------------------ даты событий и график
def event_dates(db: Session, c: Contract) -> dict[str, dict]:
    """Дата каждого события: факт (юридически значимо) или план — из договора и этапов исполнения сделки."""
    out: dict[str, dict] = {}
    if c.signed_on:
        out["contract"] = out["factory_contract"] = {"on": c.signed_on, "fact": True, "source": "дата подписания договора"}
    if c.deal_id:
        stages = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == c.deal_id)).all()
        by_code: dict[str, list[ExecutionStage]] = {}
        for s in stages:
            by_code.setdefault(s.code, []).append(s)
        sign_code = "factory_contract" if c.direction == "expense" else "contract_signed"
        if "contract" not in out and by_code.get(sign_code):
            s = by_code[sign_code][0]
            on = s.actual_on or s.refined_on or s.planned_on
            if on:
                out["contract"] = out["factory_contract"] = {"on": on, "fact": bool(s.actual_on), "source": s.title}
        for ev, code in STAGE_BY_EVENT.items():
            ss = by_code.get(code) or []
            facts = [s.actual_on for s in ss if s.actual_on]
            plans = [s.actual_on or s.refined_on or s.planned_on for s in ss if (s.actual_on or s.refined_on or s.planned_on)]
            if ss and len(facts) == len(ss):
                out[ev] = {"on": max(facts), "fact": True, "source": ss[0].title}       # событие наступило полностью (по всем позициям/партиям)
            elif plans:
                out[ev] = {"on": max(plans), "fact": False, "source": ss[0].title}
    return out


def line_amount(c: Contract, line: dict) -> Optional[float]:
    if line.get("amount") not in (None, ""):
        return float(line["amount"])
    if line.get("share_pct") not in (None, "") and c.amount is not None:
        return round(float(c.amount) * float(line["share_pct"]) / 100, 2)
    return None


def schedule(db: Session, c: Contract, today: Optional[date] = None) -> list[dict]:
    """График платежей договора: сумма, дата события (факт/план), срок оплаты; просрочка — только от факта события."""
    today = today or date.today()
    cal = work_calendar(db)
    ev = event_dates(db, c)
    rows = []
    for i, l in enumerate(c.payment_terms or []):
        e = l.get("event") or "contract"
        base: Optional[dict] = None
        if e == "fixed_date" and l.get("event_on"):
            base = {"on": date.fromisoformat(l["event_on"][:10]), "fact": True, "source": "дата из договора"}
        elif e == "monthly":
            dom = int(l.get("day_of_month") or 1)
            nxt = today.replace(day=min(dom, 28)) if today.day <= dom else (today.replace(day=1) + timedelta(days=32)).replace(day=min(dom, 28))
            base = {"on": nxt, "fact": False, "source": f"ежемесячно {dom}-го"}
        elif l.get("event_on"):
            base = {"on": date.fromisoformat(l["event_on"][:10]), "fact": True, "source": "дата события внесена вручную"}
        else:
            base = ev.get(e)
        due = _plus(cal, base["on"], int(l.get("offset_days") or 0), l.get("offset_kind") or "calendar") if base else None
        overdue = (today - due).days if (due and base and base["fact"] and due < today) else 0
        rows.append({"no": l.get("no") or i + 1, "amount": line_amount(c, l), "currency": c.currency, "share_pct": l.get("share_pct"), "event": e, "event_title": EVENTS.get(e, e),
                     "offset_days": int(l.get("offset_days") or 0), "offset_kind": l.get("offset_kind") or "calendar", "via": l.get("via") or "transfer",
                     "event_on": base["on"].isoformat() if base else None, "event_fact": bool(base and base["fact"]), "event_source": base["source"] if base else "",
                     "due_on": due.isoformat() if due else None, "due_legal": bool(base and base["fact"]), "days_overdue": overdue, "text": term_text(l)})
    return rows


def term_text(l: dict) -> str:
    amt = f"{l['share_pct']:g} %" if l.get("share_pct") not in (None, "") else (f"{float(l['amount']):,.2f}".replace(",", " ") if l.get("amount") not in (None, "") else "—")
    e = l.get("event") or "contract"
    if e == "fixed_date":
        return f"{amt} — до {l.get('event_on') or '…'}"
    if e == "monthly":
        return f"{amt} — ежемесячно {l.get('day_of_month') or 1}-го числа"
    off = int(l.get("offset_days") or 0)
    tail = f" в течение {off} {'рабочих' if l.get('offset_kind') == 'working' else 'календарных'} дней после события «{EVENTS.get(e, e)}»" if off else f" в день события «{EVENTS.get(e, e)}»"
    return amt + tail + (" (аккредитив)" if l.get("via") == "lc" else "")


# ------------------------------------------------------------------ проверки полноты
def check(c: Contract) -> dict:
    errors, warnings = [], []
    if not c.own_entity_id:
        errors.append("Не выбрана компания группы")
    if not (c.cp_id or c.cp_name):
        errors.append("Не указан контрагент")
    if not (c.subject or c.title):
        warnings.append("Не описан предмет договора")
    if c.direction in ("income", "expense"):
        main = c.doc_type == "contract"          # доп. соглашение меняет только то, что в нём указано
        if main and c.amount is None and c.price_type == "fixed":
            errors.append("Не указана цена договора")
        lines = c.payment_terms or []
        if main and not lines:
            errors.append("Не заданы условия оплаты — для доходных и расходных договоров они обязательны")
        for i, l in enumerate(lines, 1):
            if not l.get("event"):
                errors.append(f"Платёж {i}: не указано событие-основание")
            if l.get("event") == "fixed_date" and not l.get("event_on"):
                errors.append(f"Платёж {i}: не указана дата")
            if l.get("share_pct") in (None, "") and l.get("amount") in (None, ""):
                errors.append(f"Платёж {i}: не указана доля или сумма")
        shares = [float(l["share_pct"]) for l in lines if l.get("share_pct") not in (None, "")]
        if shares and len(shares) == len(lines) and abs(sum(shares) - 100) > 0.01:
            errors.append(f"Доли платежей дают {sum(shares):g} %, должно быть 100 %")
        amounts = [float(l["amount"]) for l in lines if l.get("amount") not in (None, "") and l.get("share_pct") in (None, "")]
        if amounts and len(amounts) == len(lines) and c.amount is not None and abs(sum(amounts) - float(c.amount)) > 0.01:
            errors.append(f"Сумма платежей {sum(amounts):,.2f} не равна цене договора {float(c.amount):,.2f}".replace(",", " "))
        if main and not c.penalties:
            warnings.append("Неустойки не заданы — просрочки не попадут в расчёт и претензии")
        if main and not c.performance_terms and c.direction == "expense":
            warnings.append("Не указан срок исполнения (поставки / работ) — неустойку за просрочку не рассчитать")
        if main and not (c.law or {}).get("claim_days"):
            warnings.append("Не указан срок ответа на претензию")
    if not (c.signatory or {}).get("name"):
        warnings.append("Не указан подписант с нашей стороны")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


# ------------------------------------------------------------------ выдача
def _names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}


def cp_display(db: Session, c: Contract) -> str:
    if c.cp_name:
        return c.cp_name
    if c.cp_kind == "client" and c.cp_id:
        x = db.get(Client, c.cp_id)
        return x.name if x else ""
    if c.cp_kind == "factory" and c.cp_id:
        x = db.get(Factory, c.cp_id)
        return (x.short_name or x.name) if x else ""
    return ""


def payload(db: Session, c: Contract, names: Optional[dict] = None, ents: Optional[dict] = None, full: bool = True) -> dict:
    names = names if names is not None else _names(db)
    ents = ents if ents is not None else {e.id: e.name for e in db.scalars(select(LegalEntity)).all()}
    deal = db.get(Deal, c.deal_id) if c.deal_id else None
    today = date.today()
    out = {"id": c.id, "parent_id": c.parent_id, "doc_type": c.doc_type, "doc_type_title": DOC_TYPES.get(c.doc_type, c.doc_type), "kind": c.kind, "kind_title": KINDS.get(c.kind, {}).get("title", c.kind),
           "direction": c.direction, "direction_title": DIRECTIONS.get(c.direction, ""), "number": c.number, "cp_number": c.cp_number, "title": c.title, "subject": c.subject,
           "status": c.status, "status_title": STATUSES.get(c.status, c.status), "locked": c.status in LOCKED,
           "own_entity_id": c.own_entity_id, "own_entity_name": ents.get(c.own_entity_id or "", ""), "cp_kind": c.cp_kind, "cp_id": c.cp_id, "cp_name": cp_display(db, c), "cp_inn": c.cp_inn,
           "deal_id": c.deal_id, "deal_number": deal.number if deal else "", "amount": c.amount, "currency": c.currency, "vat_mode": c.vat_mode, "vat_rate": c.vat_rate, "price_type": c.price_type,
           "signed_on": c.signed_on.isoformat() if c.signed_on else None, "valid_to": c.valid_to.isoformat() if c.valid_to else None,
           "days_to_expiry": (c.valid_to - today).days if c.valid_to else None, "sign_method": c.sign_method,
           "has_scan": any(f.get("kind") == "signed_scan" for f in (c.files or [])), "curator_id": c.curator_id, "curator_name": names.get(c.curator_id or "", ""),
           "source": c.source, "updated_at": c.updated_at.isoformat() if c.updated_at else None,
           "terms_text": "; ".join(term_text(l) for l in (c.payment_terms or [])),
           "pending": [x["title"] for x in pending_steps(c)]}
    if not full:
        return out
    chk = check(c)
    out.update({"own_profile_id": c.own_profile_id, "own_bank_id": c.own_bank_id, "signatory": c.signatory or {}, "cp_profile_id": c.cp_profile_id, "deal_ids": c.deal_ids or [],
                "package_id": c.package_id, "fx_clause": c.fx_clause, "effective_from": c.effective_from.isoformat() if c.effective_from else None, "auto_renew": c.auto_renew,
                "payment_terms": c.payment_terms or [], "performance_terms": c.performance_terms or [], "penalties": c.penalties or [], "liability_cap_pct": c.liability_cap_pct,
                "security": c.security or {}, "warranty": c.warranty or {}, "law": c.law or {}, "edo": c.edo or {}, "original": c.original or {},
                "files": [{**f, "kind_title": FILE_KINDS.get(f.get("kind"), f.get("kind")), "uploaded_by_name": names.get(f.get("uploaded_by") or "", "")} for f in (c.files or [])],
                "history": [{**h, "by_name": names.get(h.get("by") or "", "")} for h in reversed(c.history or [])], "notes": c.notes, "lawyer_id": c.lawyer_id,
                "created_by_name": names.get(c.created_by or "", ""), "created_at": c.created_at.isoformat() if c.created_at else None,
                "schedule": schedule(db, c), "check": chk, "client_order": bool(c.client_order),
                "approval": approval_view(db, c, names), "route_preview": [STEP_TITLES[x] for x in route(db, c)], "sign_checklist": sign_checklist(c),
                "amends": amend_changes(db, c) if c.doc_type == "amendment" else [],
                "children": [payload(db, x, names, ents, full=False) for x in db.scalars(select(Contract).where(Contract.parent_id == c.id).order_by(Contract.created_at)).all()]})
    return out


def hist(c: Contract, user_id: Optional[str], action: str, comment: str = "", changes: Optional[dict] = None) -> None:
    row = {"at": datetime.now(timezone.utc).isoformat(), "by": user_id, "action": action, "comment": comment}
    if changes:
        row["changes"] = changes
    c.history = list(c.history or []) + [row]


# ------------------------------------------------------------------ из сделок и пакетов
def _won_variant(deal: Deal):
    from .execution_service import won_variant
    return won_variant(deal) if deal.variants else None


def deal_client_terms(deal: Deal) -> tuple[list[dict], Optional[float], str]:
    v = _won_variant(deal)
    lines = list((v.client_payments if v else None) or deal.client_payments or DEFAULT_CLIENT_PAYMENTS)
    amount = None
    cur = "RUB"
    if v is not None:
        if v.price_amount is not None and v.price_currency:
            amount, cur = float(v.price_amount), v.price_currency
        elif v.price_rub is not None:
            amount, cur = float(v.price_rub), "RUB"
    return [_norm_line(l, i) for i, l in enumerate(lines)], amount, cur


def deal_factory_terms(deal: Deal, factory_id: str) -> tuple[list[dict], Optional[float], str]:
    poss = [p for p in deal.positions if p.factory_id == factory_id] if factory_id else list(deal.positions)
    first = poss[0] if poss else None
    lines = list((first.factory_payments or first.factory_payments_offer) if first else []) or DEFAULT_FACTORY_PAYMENTS
    amount = sum((float(p.unit_price or 0) + float(p.options_price or 0)) * int(p.qty or 1) for p in poss) if poss else None
    cur = (first.currency if first else "CNY") or "CNY"
    return [_norm_line(l, i) for i, l in enumerate(lines)], (round(amount, 2) if amount else None), cur.upper()


def _norm_line(l: dict, i: int) -> dict:
    return {"no": i + 1, "share_pct": l.get("share_pct"), "amount": l.get("amount") if l.get("share_pct") in (None, "") else None, "event": l.get("event") or "contract",
            "offset_days": int(l.get("offset_days") or 0), "offset_kind": l.get("offset_kind") or "calendar", "via": l.get("via") or "transfer", "note": ""}


def _default_entity(db: Session, income: bool) -> Optional[LegalEntity]:
    ents = db.scalars(select(LegalEntity).where(LegalEntity.is_active.is_(True)).order_by(LegalEntity.code)).all()
    return next((e for e in ents if e.sells_to_clients), ents[0] if ents else None) if income else (ents[0] if ents else None)


def upsert_from_package(db: Session, pkg: LegalPackage, deal: Deal, user_id: Optional[str]) -> Contract:
    """Пакет сделки → проект договора в реестре (или обновление ещё не подписанного): стороны, цена, условия оплаты, типовые неустойки."""
    income = pkg.kind == "sale"
    c = db.scalars(select(Contract).where(Contract.deal_id == deal.id, Contract.cp_id == pkg.counterparty_id, Contract.doc_type == "contract",
                                          Contract.direction == ("income" if income else "expense"))).first()
    snap = pkg.snapshot or {}
    totals = snap.get("totals") or {}
    if income:
        lines, amount, cur = deal_client_terms(deal)
        if totals.get("amount") is not None:
            amount, cur = float(totals["amount"]), totals.get("currency") or cur
    else:
        lines, amount, cur = deal_factory_terms(deal, pkg.counterparty_id)
        if totals.get("amount") is not None:
            amount, cur = float(totals["amount"]), totals.get("currency") or cur
    fresh = c is None
    if fresh:
        ent = db.get(LegalEntity, pkg.own_entity_id) if pkg.own_entity_id else None
        direction = "income" if income else "expense"
        num, seq = next_number(db, ent, direction, date.today())
        pens, cap = default_penalties(db, direction)
        c = Contract(doc_type="contract", kind="client_supply" if income else "supplier", direction=direction, number=num, seq=seq, own_entity_id=pkg.own_entity_id,
                     cp_kind=pkg.counterparty_kind, cp_id=pkg.counterparty_id, deal_id=deal.id, deal_ids=[deal.id], subject=(snap.get("contract") or {}).get("subject") or "поставка горно-шахтного оборудования",
                     penalties=pens, liability_cap_pct=cap, curator_id=deal.manager_id, created_by=user_id, source="package", status="draft",
                     law={"claim_days": 30, "pre_trial": True}, cp_number=(snap.get("contract") or {}).get("number") or "")
        db.add(c)
    if c.status in LOCKED:
        return c
    c.package_id, c.own_profile_id, c.own_bank_id, c.cp_profile_id = pkg.id, pkg.own_profile_id, pkg.own_bank_id, pkg.cp_profile_id
    c.amount, c.currency, c.payment_terms = amount, (cur or "RUB").upper(), lines
    if not c.performance_terms:
        c.performance_terms = [{"code": "delivery" if income else "readiness", "title": "", "event": "contract", "offset_days": 0, "offset_kind": "calendar", "fixed_on": None,
                                "note": (snap.get("contract") or {}).get("delivery_term" if income else "production_term") or ""}]
    hist(c, user_id, "created" if fresh else "package", f"из юридического пакета v{pkg.version_no}")
    return c


def import_from_deals(db: Session) -> int:
    """Договоры, уже подписанные в сделках (номер и дата контракта), — в реестр. Идемпотентно."""
    n = 0
    known = {(x.deal_id, x.direction) for x in db.scalars(select(Contract).where(Contract.deal_id.is_not(None), Contract.doc_type == "contract")).all()}
    for deal in db.scalars(select(Deal).where(Deal.deleted_at.is_(None))).all():
        ent = _default_entity(db, True)
        if (deal.contract_number or deal.contract_signed_at) and (deal.id, "income") not in known:
            lines, amount, cur = deal_client_terms(deal)
            pens, cap = default_penalties(db, "income")
            db.add(Contract(doc_type="contract", kind="client_supply", direction="income", number=deal.contract_number or deal.number, cp_number=deal.contract_number or "",
                            own_entity_id=ent.id if ent else None, cp_kind="client", cp_id=deal.client_id, deal_id=deal.id, deal_ids=[deal.id], subject="поставка горно-шахтного оборудования",
                            amount=amount, currency=cur, payment_terms=lines, penalties=pens, liability_cap_pct=cap, signed_on=deal.contract_signed_at,
                            status="active" if deal.contract_signed_at else "draft", curator_id=deal.manager_id, source="deal_import", law={"claim_days": 30, "pre_trial": True},
                            history=[{"at": datetime.now(timezone.utc).isoformat(), "by": None, "action": "imported", "comment": f"из реквизитов контракта сделки {deal.number}"}]))
            n += 1
        if (deal.factory_contract_number or deal.factory_contract_signed_at) and (deal.id, "expense") not in known:
            fid = next((p.factory_id for p in deal.positions if p.factory_id), None)
            lines, amount, cur = deal_factory_terms(deal, fid or "")
            pens, cap = default_penalties(db, "expense")
            db.add(Contract(doc_type="contract", kind="supplier", direction="expense", number=deal.factory_contract_number or f"{deal.number}-З", cp_number=deal.factory_contract_number or "",
                            own_entity_id=ent.id if ent else None, cp_kind="factory", cp_id=fid, deal_id=deal.id, deal_ids=[deal.id], subject="поставка горно-шахтного оборудования",
                            amount=amount, currency=cur, payment_terms=lines, penalties=pens, liability_cap_pct=cap, signed_on=deal.factory_contract_signed_at,
                            status="active" if deal.factory_contract_signed_at else "draft", curator_id=deal.manager_id, source="deal_import", law={"claim_days": 30, "pre_trial": True},
                            history=[{"at": datetime.now(timezone.utc).isoformat(), "by": None, "action": "imported", "comment": f"из реквизитов контракта с заводом сделки {deal.number}"}]))
            n += 1
    return n


def apply_signed_to_deal(db: Session, c: Contract) -> None:
    """Подписанный договор сделки → номер и дата контракта в сделке (как раньше вносили вручную)."""
    if not c.deal_id or c.doc_type != "contract":
        return
    deal = db.get(Deal, c.deal_id)
    if not deal:
        return
    if c.direction == "income":
        deal.contract_number = deal.contract_number or c.cp_number or c.number
        deal.contract_signed_at = deal.contract_signed_at or c.signed_on
    elif c.direction == "expense":
        deal.factory_contract_number = deal.factory_contract_number or c.cp_number or c.number
        deal.factory_contract_signed_at = deal.factory_contract_signed_at or c.signed_on


def any_fields(d: dict[str, Any], keys: list[str]) -> dict:
    return {k: d[k] for k in keys if k in d}



# ------------------------------------------------------------------ этап 2: маршрут согласования, подписание, доп. соглашения, сроки
STEP_TITLES = {"lawyer": "Юрист", "fd": "ФД", "cd": "КД", "gd": "ЗГД"}
STEP_ROLE = {"lawyer": "lawyer", "fd": "fd", "cd": "cd", "gd": "gd"}
IN_ROUTE = {"legal_review", "approval", "approved", "signing"}       # условия заморожены: правит юрист / ЗГД, после виз — согласование заново
AMEND_FIELDS = ("amount", "payment_terms", "performance_terms", "penalties", "liability_cap_pct", "valid_to", "subject", "security", "warranty")


def gd_threshold(db: Session) -> float:
    from . import settings_service
    try:
        return float(settings_service.get(db, "contract_gd_threshold") or 100000)
    except (TypeError, ValueError):
        return 100000.0


def amount_rub(db: Session, c: Contract) -> Optional[float]:
    if c.amount is None:
        return None
    cur = (c.currency or "RUB").upper()
    if cur == "RUB":
        return float(c.amount)
    from ..models import FxRate
    r = db.scalars(select(FxRate).where(FxRate.currency == cur).order_by(FxRate.on.desc()).limit(1)).first()
    return float(c.amount) * r.rate if r else None


def route(db: Session, c: Contract) -> list[str]:
    """Маршрут: юрист и ФД — всегда; КД — закупки под заказы клиентов (ГШО или запчасти); ЗГД — цена выше порога (100 000 ₽) или цена не определена."""
    steps = ["lawyer", "fd"]
    if c.direction == "expense" and (c.kind == "supplier" or c.client_order) and (c.client_order or c.deal_id):
        steps.append("cd")
    rub = amount_rub(db, c)
    if (rub is None and c.direction != "none") or (rub is not None and rub > gd_threshold(db)):
        steps.append("gd")
    return steps


def start_route(db: Session, c: Contract, user_id: Optional[str]) -> None:
    rnd = int((c.approval or {}).get("round") or 0) + 1
    c.approval = {"round": rnd, "submitted_at": datetime.now(timezone.utc).isoformat(), "submitted_by": user_id,
                  "steps": [{"code": x, "title": STEP_TITLES[x], "decision": None, "by": None, "at": None, "comment": ""} for x in route(db, c)]}
    c.status = "legal_review"


def pending_steps(c: Contract) -> list[dict]:
    """Ожидающие визы: сначала юрист, затем параллельно ФД, КД, ЗГД."""
    if c.status not in ("legal_review", "approval"):
        return []
    steps = (c.approval or {}).get("steps") or []
    law = [x for x in steps if x["code"] == "lawyer" and x["decision"] != "approve"]
    if law:
        return law
    return [x for x in steps if x["decision"] != "approve"]


def user_steps(c: Contract, roles) -> list[dict]:
    roles = {str(getattr(r, "value", r)) for r in roles}
    return [x for x in pending_steps(c) if STEP_ROLE[x["code"]] in roles]


def approval_view(db: Session, c: Contract, names: dict) -> dict:
    a = dict(c.approval or {})
    pend = {x["code"] for x in pending_steps(c)}
    a["steps"] = [{**x, "by_name": names.get(x.get("by") or "", ""), "pending": x["code"] in pend} for x in a.get("steps") or []]
    a["submitted_by_name"] = names.get(a.get("submitted_by") or "", "")
    return a


def reset_visas(db: Session, c: Contract, user_id: Optional[str], why: str) -> bool:
    """Изменились условия на маршруте: маршрут пересобирается; если визы уже были — согласование заново (новый круг)."""
    a = dict(c.approval or {})
    had = any(x.get("decision") for x in a.get("steps") or [])
    a["steps"] = [{"code": x, "title": STEP_TITLES[x], "decision": None, "by": None, "at": None, "comment": ""} for x in route(db, c)] if had else \
        [next((y for y in a.get("steps") or [] if y["code"] == x), {"code": x, "title": STEP_TITLES[x], "decision": None, "by": None, "at": None, "comment": ""}) for x in route(db, c)]
    if had:
        a["round"] = int(a.get("round") or 1) + 1
        c.status = "legal_review"
    c.approval = a
    if had:
        hist(c, user_id, "route_reset", f"{why} — согласование заново (круг {a['round']})")
    return had


def sign_checklist(c: Contract) -> list[dict]:
    files = {f.get("kind") for f in (c.files or [])}
    out = [{"code": "approved", "title": "Договор согласован", "ok": c.status in ("approved", "signing") or c.status in LOCKED},
           {"code": "signatory", "title": "Подписант и основание (устав / доверенность)", "ok": bool((c.signatory or {}).get("name") and (c.signatory or {}).get("basis"))},
           {"code": "signed_on", "title": "Дата подписания", "ok": bool(c.signed_on)},
           {"code": "scan", "title": "Подписанный скан загружен", "ok": "signed_scan" in files}]
    if c.sign_method == "edo":
        out.append({"code": "edo", "title": "Файл ЭДО с ЭП / квитанции оператора", "ok": "edo" in files})
    else:
        out.append({"code": "original", "title": "Оригинал получен и передан на хранение", "ok": bool((c.original or {}).get("received_on")), "optional": True})
    return out


def amend_changes(db: Session, c: Contract) -> list[dict]:
    """Что меняет доп. соглашение в основном договоре: заполненные в нём поля."""
    parent = db.get(Contract, c.parent_id) if c.parent_id else None
    if parent is None:
        return []
    titles = {"amount": "цена", "payment_terms": "условия оплаты", "performance_terms": "сроки исполнения", "penalties": "неустойки", "liability_cap_pct": "предел ответственности",
              "valid_to": "срок действия", "subject": "предмет", "security": "обеспечение", "warranty": "гарантия"}
    out = []
    for k in AMEND_FIELDS:
        v = getattr(c, k)
        if v in (None, "", [], {}) or v == getattr(parent, k):
            continue
        out.append({"field": k, "title": titles[k]})
    return out


def apply_amendment(db: Session, c: Contract, user_id: Optional[str]) -> list[str]:
    """Доп. соглашение вступило в силу → основной договор принимает изменённые условия (с записью в истории)."""
    parent = db.get(Contract, c.parent_id) if c.parent_id else None
    if parent is None or c.doc_type != "amendment":
        return []
    changed = []
    for ch in amend_changes(db, c):
        k = ch["field"]
        setattr(parent, k, getattr(c, k))
        if k == "amount":
            parent.currency = c.currency
        changed.append(ch["title"])
    if changed:
        hist(parent, user_id, "amendment", f"{c.number} вступило в силу: " + ", ".join(changed))
    return changed


def role_users(db: Session, *roles: str) -> list[User]:
    return [u for u in db.scalars(select(User).where(User.is_active.is_(True))).all() if set(roles) & set(u.roles or [])]


def notify_users(db: Session, c: Contract, user_ids: list[str], text: str, event: str = "contract") -> None:
    from .deal_service import notify
    deal = db.get(Deal, c.deal_id) if c.deal_id else None
    for uid in dict.fromkeys(x for x in user_ids if x):
        notify(db, deal, uid, event, None, text)


def notify_pending(db: Session, c: Contract) -> None:
    roles = {STEP_ROLE[x["code"]] for x in pending_steps(c)}
    if not roles:
        return
    if roles == {"lawyer"} and c.lawyer_id:
        ids = [c.lawyer_id]                     # назначенный юрист
    else:
        ids = [u.id for u in role_users(db, *roles)]
    notify_users(db, c, ids, f"Договор {c.number} ({cp_display(db, c)}) ждёт вашей визы: {', '.join(x['title'] for x in pending_steps(c))}")


def check_expiring(db: Session, today: Optional[date] = None) -> int:
    """Действующие договоры: напоминания куратору и юристам за 30 и 7 дней до окончания; автопролонгация на год; уведомление об истечении."""
    today = today or date.today()
    n = 0
    lawyers = [u.id for u in role_users(db, "lawyer")]
    for c in db.scalars(select(Contract).where(Contract.status == "active", Contract.valid_to.is_not(None))).all():
        dl = (c.valid_to - today).days
        if dl < 0 and c.auto_renew:
            old = c.valid_to
            try:
                c.valid_to = old.replace(year=old.year + 1)
            except ValueError:
                c.valid_to = old + timedelta(days=365)
            c.notified = []
            hist(c, None, "renewed", f"автопролонгация: до {c.valid_to:%d.%m.%Y} (было {old:%d.%m.%Y})")
            notify_users(db, c, [c.curator_id or "", *lawyers], f"Договор {c.number} ({cp_display(db, c)}) продлён автоматически до {c.valid_to:%d.%m.%Y}")
            n += 1
            continue
        key = "expired" if dl < 0 else "expiring_7" if dl <= 7 else "expiring_30" if dl <= 30 else None
        if key is None or key in (c.notified or []):
            continue
        text = (f"Срок действия договора {c.number} ({cp_display(db, c)}) истёк {c.valid_to:%d.%m.%Y} — продлите доп. соглашением или отметьте исполненным" if key == "expired"
                else f"Договор {c.number} ({cp_display(db, c)}) действует до {c.valid_to:%d.%m.%Y} — осталось {dl} дн." + (" (автопролонгация)" if c.auto_renew else ""))
        if key == "expiring_30" and c.auto_renew:
            c.notified = list(c.notified or []) + [key]
            continue
        notify_users(db, c, [c.curator_id or "", *lawyers], text)
        c.notified = list(c.notified or []) + [key] + (["expiring_30"] if key != "expiring_30" and "expiring_30" not in (c.notified or []) else [])
        n += 1
    return n


def inbox(db: Session, user) -> dict:
    """Договоры к действию для пользователя: мои визы, мои проекты/возвраты, на подпись, без скана, истекающие."""
    today = date.today()
    roles = {str(getattr(r, "value", r)) for r in user.roles}
    sees_all = bool(roles & {"lawyer", "gd", "fd", "cd", "admin"})
    rows = db.scalars(select(Contract).where(Contract.status.not_in(("cancelled", "closed", "terminated")))).all()
    out: dict[str, list] = {"visas": [], "drafts": [], "signing": [], "no_scan": [], "expiring": []}
    names = None
    for c in rows:
        mine = c.curator_id == user.id or c.created_by == user.id
        base = {"id": c.id, "number": c.number, "cp_name": cp_display(db, c), "status": c.status, "status_title": STATUSES.get(c.status, c.status),
                "url": f"/legal?tab=contracts&open={c.id}", "updated_at": c.updated_at.isoformat() if c.updated_at else None, "amount": c.amount, "currency": c.currency}
        steps = user_steps(c, roles)
        if steps:
            sub = (c.approval or {}).get("submitted_at")
            out["visas"].append({**base, "steps": [x["title"] for x in steps], "waiting_since": sub})
        if mine and c.status == "draft" and any(h.get("action") == "visa" for h in (c.history or [])):
            last = next((h for h in reversed(c.history or []) if h.get("action") == "visa"), {})
            out["drafts"].append({**base, "comment": last.get("comment", "")})
        if c.status in ("approved", "signing") and (mine or sees_all):
            out["signing"].append(base)
        if c.status == "active" and (mine or sees_all):
            if not any(f.get("kind") == "signed_scan" for f in (c.files or [])):
                out["no_scan"].append(base)
            if c.valid_to and (c.valid_to - today).days <= 30:
                out["expiring"].append({**base, "valid_to": c.valid_to.isoformat(), "days": (c.valid_to - today).days, "auto_renew": c.auto_renew})
    out["expiring"].sort(key=lambda x: x["days"])
    return {**out, "counts": {k: len(v) for k, v in out.items()}}
