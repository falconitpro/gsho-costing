"""Реестр обязательств: проекция строк источников и вычисление оплаты/остатка/статуса.

Источник этого этапа — подписанный договор (Contract.payment_terms через legal_contracts.schedule()).
Каждая строка графика — отдельное обязательство с ключом payment:{line_uid|no}. Проекция не редактирует договор,
а договор не редактируется через финансовый API (D-011, D-017).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Contract, Deal, FinanceAllocation, FinanceObligation
from ..deal_service import audit
from .. import legal_contracts as legal
from .money import ZERO, dec, s

PROJECTED_STATUSES = ("active", "closed", "terminated")   # подписанные договоры (legal.LOCKED)
DIRECTION = {"income": "in", "expense": "out"}
SOURCE_TITLES = {"contract": "Договор", "calculation": "Расчёт", "purchase_order": "Заказ поставщику", "batch": "Партия",
                 "part_request": "Заявка на запчасти", "penalty": "Неустойка"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _revision(line: dict, c: Contract, amount: Optional[Decimal]) -> str:
    """Hash значимых юридических полей строки: сумма, валюта, событие, отсрочка. Прогнозные даты не входят — их обновление не конфликт."""
    sig = {"amount": s(amount), "currency": c.currency, "share_pct": line.get("share_pct"), "event": line.get("event") or "contract",
           "offset_days": int(line.get("offset_days") or 0), "offset_kind": line.get("offset_kind") or "calendar",
           "event_on": (line.get("event_on") or "")[:10] if (line.get("event") == "fixed_date") else "", "via": line.get("via") or "transfer"}
    return hashlib.sha256(json.dumps(sig, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _line_keys(terms: list[dict]) -> list[str]:
    """Стабильный ключ строки: line_uid (новые графики) или номер строки (подписанные договоры — JSON не переписываем)."""
    nos = [str(l.get("line_uid") or l.get("no") or i + 1) for i, l in enumerate(terms)]
    if len(set(nos)) != len(nos):   # номера в договоре повторяются — ключ по позиции, с пометкой
        return [f"payment:idx{i + 1}" for i in range(len(terms))]
    return [f"payment:{n}" for n in nos]


def _active_alloc_total(db: Session, obligation_id: str) -> Decimal:
    v = db.scalar(select(func.coalesce(func.sum(FinanceAllocation.amount_obligation), 0)).where(
        FinanceAllocation.obligation_id == obligation_id, FinanceAllocation.status.in_(("provisional", "confirmed"))))
    return dec(v) or ZERO


@dataclass
class SyncStats:
    contracts: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0
    orphaned: int = 0
    skipped: list[dict] = field(default_factory=list)

    def add(self, other: "SyncStats") -> None:
        for k in ("contracts", "created", "updated", "unchanged", "conflicts", "orphaned"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.skipped += other.skipped

    def as_dict(self) -> dict:
        return {"contracts": self.contracts, "created": self.created, "updated": self.updated, "unchanged": self.unchanged,
                "conflicts": self.conflicts, "orphaned": self.orphaned, "skipped": self.skipped}


def eligible(c: Contract) -> bool:
    return c.doc_type != "amendment" and c.direction in DIRECTION and c.status in PROJECTED_STATUSES


@dataclass
class LineSpec:
    """Строка источника для проекции: ключ, ревизия значимых полей, сумма/валюта и производные поля обязательства."""
    key: str
    revision: str
    amount: Optional[Decimal]
    currency: Optional[str]
    fields: dict
    basis: dict
    active: bool = True
    reason: str = ""
    status: str = "ok"          # источник сам может пометить строку на сверку (напр., замещённый прогноз с разнесениями)


def project_lines(db: Session, source_type: str, source_id: str, specs: list[LineSpec], user_id: Optional[str], deal_id: Optional[str],
                  orphan_reason: str = "строка удалена из источника") -> SyncStats:
    """Единая пересборка строк одного источника (договор, заказ поставщику, партия). Правила одинаковы для всех источников:
    повтор с той же ревизией ничего не меняет; изменение суммы/валюты при разнесениях → conflict без перезаписи;
    строка без валюты — conflict «валюта не определена» (не подставляем RUB); исчезнувшая строка → orphaned, не удаляется."""
    st = SyncStats()
    existing = {o.source_line_key: o for o in db.scalars(select(FinanceObligation).where(
        FinanceObligation.source_type == source_type, FinanceObligation.source_id == source_id)).all()}
    now = _now()
    for sp in specs:
        amount, currency, fields, rev = sp.amount, sp.currency, sp.fields, sp.revision
        active, reason, sync_status = sp.active, sp.reason, sp.status
        if not currency:
            currency, active, reason, sync_status = "XXX", False, "валюта не определена в источнике", "conflict"   # XXX — ISO 4217 «без валюты»
        if amount is None and active:
            active, reason = False, "сумма строки не определена"
        o = existing.pop(sp.key, None)
        if o is None:
            o = FinanceObligation(source_type=source_type, source_id=source_id, source_line_key=sp.key, source_revision=rev, amount=amount or ZERO,
                                  currency=currency, basis_snapshot=sp.basis, is_active=active, excluded_reason=reason, sync_status=sync_status, synced_at=now, **fields)
            db.add(o)
            db.flush()
            audit(db, user_id, deal_id, "finance_obligation", o.id, "create", after={"source": f"{source_type}:{source_id}:{sp.key}", "amount": s(o.amount), "currency": o.currency, "revision": rev})
            st.created += 1
            continue
        before = {"amount": s(o.amount), "currency": o.currency, "legal_due_on": o.legal_due_on, "forecast_on": o.forecast_on, "is_active": o.is_active, "sync_status": o.sync_status}
        money_changed = (amount or ZERO) != o.amount or currency != o.currency
        if money_changed and o.sync_status == "conflict" and (o.basis_snapshot or {}).get("pending", {}).get("revision") == rev:
            st.conflicts += 1   # тот же неразрешённый конфликт — без повторной записи и аудита
            continue
        if money_changed and _active_alloc_total(db, o.id) > 0:
            # сумма/валюта изменилась, а по строке уже есть разнесения: не перезаписываем, ставим на сверку
            o.sync_status = "conflict"
            o.basis_snapshot = {**sp.basis, "pending": {"amount": s(amount), "currency": currency, "revision": rev}}
            o.synced_at = now
            for k, v in fields.items():
                setattr(o, k, v)
            audit(db, user_id, deal_id, "finance_obligation", o.id, "conflict", before=before,
                  after={"pending_amount": s(amount), "pending_currency": currency, "revision": rev}, comment="изменение источника при наличии разнесений")
            st.conflicts += 1
            continue
        changed = (money_changed or rev != o.source_revision or o.is_active != active or o.sync_status != sync_status
                   or any(getattr(o, k) != v for k, v in fields.items()))
        if not changed:
            st.unchanged += 1 if sync_status == "ok" else 0
            st.conflicts += 0 if sync_status == "ok" else 1
            continue
        for k, v in fields.items():
            setattr(o, k, v)
        o.amount, o.currency, o.source_revision, o.basis_snapshot = amount or ZERO, currency, rev, sp.basis
        o.is_active, o.excluded_reason, o.sync_status, o.synced_at = active, reason, sync_status, now
        audit(db, user_id, deal_id, "finance_obligation", o.id, "sync", before=before,
              after={"amount": s(o.amount), "currency": o.currency, "legal_due_on": o.legal_due_on, "forecast_on": o.forecast_on, "is_active": o.is_active, "revision": rev})
        st.updated += 1
        if sync_status != "ok":
            st.conflicts += 1
    for key, o in existing.items():   # строки больше нет в источнике — не удаляем (история и разнесения), а выводим из итогов
        if o.sync_status == "orphaned" and not o.is_active:
            st.unchanged += 1
            continue
        audit(db, user_id, deal_id, "finance_obligation", o.id, "orphaned", before={"is_active": o.is_active, "sync_status": o.sync_status}, after={"is_active": False, "sync_status": "orphaned"})
        o.is_active, o.sync_status, o.excluded_reason, o.synced_at = False, "orphaned", orphan_reason, now
        st.orphaned += 1
    return st


def sync_contract(db: Session, c: Contract, user_id: Optional[str] = None, today: Optional[date] = None) -> SyncStats:
    """Пересобрать проекцию строк графика одного договора. Вызывается в той же транзакции, что и изменение договора."""
    if not eligible(c):
        return SyncStats()
    today = today or date.today()
    terms = list(c.payment_terms or [])
    rows = legal.schedule(db, c, today)
    keys = _line_keys(terms)
    cp_name = legal.cp_display(db, c)
    specs = []
    for i, (line, row, key) in enumerate(zip(terms, rows, keys)):
        amount = dec(row["amount"])
        due = date.fromisoformat(row["due_on"]) if row.get("due_on") else None
        legal_due = due if row.get("due_legal") else None
        fields: dict[str, Any] = {
            "deal_id": c.deal_id, "contract_id": c.id, "legal_entity_id": c.own_entity_id, "counterparty_kind": c.cp_kind or "other",
            "counterparty_id": c.cp_id, "counterparty_name_snapshot": cp_name[:255], "direction": DIRECTION[c.direction], "nature": "contractual",
            "title": f"{c.number}: {row['event_title']} — строка {row['no']}"[:255], "event_code": row["event"], "source_line_no": int(row["no"]) if str(row["no"]).isdigit() else i + 1,
            "legal_due_on": legal_due, "legal_due_text": "" if legal_due else row["text"],
            "event_on": date.fromisoformat(row["event_on"]) if row.get("event_on") else None, "event_is_fact": bool(row.get("event_fact")),
            "forecast_on": due,   # срок по наступившему событию либо оперативный прогноз по плановой дате этапа; без даты — NULL
        }
        active, reason = True, ""
        if amount is None:
            active, reason = False, "сумма строки не определена (доля без цены договора)"
        elif c.status == "terminated":
            active, reason = False, "договор расторгнут"
        basis = {"contract_number": c.number, "contract_status": c.status, "line": {k: line.get(k) for k in ("no", "line_uid", "share_pct", "amount", "event", "offset_days", "offset_kind", "via", "event_on", "day_of_month")},
                 "event_source": row.get("event_source", ""), "terms_text": row["text"]}
        specs.append(LineSpec(key=key, revision=_revision(line, c, amount), amount=amount, currency=c.currency, fields=fields, basis=basis, active=active, reason=reason))
    st = project_lines(db, "contract", c.id, specs, user_id, c.deal_id, "строка удалена из графика договора")
    st.contracts = 1
    from .sources import sync_calculation   # подписанный договор замещает прогноз утверждённого расчёта своих сделок (D-127)
    for did in {c.deal_id, *(c.deal_ids or [])} - {None, ""}:
        d = db.get(Deal, did)
        if d is not None and d.approved_version_id:
            sub = sync_calculation(db, d, user_id)
            sub.contracts = 0
            st.add(sub)
    for i, line in enumerate(terms):
        if dec(rows[i]["amount"]) is None:
            st.skipped.append({"contract": c.number, "line": rows[i]["no"], "reason": "сумма не определена"})
    return st


def rebuild(db: Session, user_id: Optional[str], dry_run: bool = True, today: Optional[date] = None) -> dict:
    """Полная пересборка проекции договоров. dry_run — всё в точке сохранения, которая откатывается: отчёт без изменений."""
    total = SyncStats()
    sp = db.begin_nested()
    try:
        for c in db.scalars(select(Contract).where(Contract.status.in_(PROJECTED_STATUSES)).order_by(Contract.created_at)).all():
            total.add(sync_contract(db, c, user_id, today))
        from .sources import rebuild_sources
        total.add(rebuild_sources(db, user_id))   # заказы поставщикам и партии
        db.flush()
        counts = {f"{t}:{c}": n for t, c, n in db.execute(select(FinanceObligation.source_type, FinanceObligation.currency, func.count()).where(
            FinanceObligation.is_active.is_(True)).group_by(FinanceObligation.source_type, FinanceObligation.currency)).all()}
        sums = {f"{t}:{c}": s(dec(v)) for t, c, v in db.execute(select(FinanceObligation.source_type, FinanceObligation.currency, func.sum(FinanceObligation.amount)).where(
            FinanceObligation.is_active.is_(True)).group_by(FinanceObligation.source_type, FinanceObligation.currency)).all()}
    finally:
        if dry_run:
            sp.rollback()
        else:
            sp.commit()
    return {"dry_run": dry_run, **total.as_dict(), "active_counts": counts, "active_sums": sums}


# ------------------------------------------------------------------ оплата, остаток, статус

def paid_map(db: Session, ids: Iterable[str]) -> dict[str, dict[str, Decimal]]:
    ids = list(ids)
    out: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"confirmed": ZERO, "provisional": ZERO})
    if not ids:
        return out
    for oid, status, total in db.execute(select(FinanceAllocation.obligation_id, FinanceAllocation.status, func.sum(FinanceAllocation.amount_obligation))
                                         .where(FinanceAllocation.obligation_id.in_(ids), FinanceAllocation.status.in_(("provisional", "confirmed")))
                                         .group_by(FinanceAllocation.obligation_id, FinanceAllocation.status)).all():
        out[oid][status] = dec(total) or ZERO
    return out


def effective_on(o: FinanceObligation) -> Optional[date]:
    return o.legal_due_on or o.forecast_on


def status_of(o: FinanceObligation, paid: dict[str, Decimal], as_of: date) -> dict:
    """Статус на дату (02, раздел 7). Просрочка — только от юридического срока; оперативный перенос её не стирает."""
    confirmed, provisional = paid["confirmed"], paid["provisional"]
    remaining = max(o.amount - confirmed, ZERO)
    flags = []
    if provisional > 0:
        flags.append("needs_reconciliation")
    if o.sync_status != "ok":
        flags.append(o.sync_status)
    days_overdue = (as_of - o.legal_due_on).days if (remaining > 0 and o.legal_due_on and o.legal_due_on < as_of) else 0
    if not o.is_active:
        st = "superseded" if o.superseded_by_id else "excluded"
    elif o.amount > 0 and remaining == 0:
        st = "paid"
    elif days_overdue > 0:
        st = "overdue"
    elif confirmed > 0:
        st = "partially_paid"
    elif not o.legal_due_on and not o.forecast_on:
        st = "no_date"
    elif (effective_on(o) or as_of) <= as_of + timedelta(days=7):
        st = "due_soon"
    else:
        st = "planned"
    return {"confirmed_paid": confirmed, "provisional_paid": provisional, "remaining": remaining, "status": st, "days_overdue": days_overdue, "flags": flags}


STATUS_TITLES = {"paid": "оплачено", "partially_paid": "частично оплачено", "overdue": "просрочено", "no_date": "нет даты", "due_soon": "скоро срок",
                 "planned": "ожидается", "superseded": "замещено", "excluded": "исключено из итогов"}


def source_number(o: FinanceObligation) -> str:
    bs = o.basis_snapshot or {}
    if bs.get("version_no"):
        return f"версия {bs['version_no']}"
    return str(bs.get("po_number") or bs.get("batch") or bs.get("contract_number") or "")


def source_url(o: FinanceObligation) -> Optional[str]:
    if o.source_type == "contract" and o.contract_id:
        return f"/legal?tab=contracts&open={o.contract_id}"
    if o.source_type == "purchase_order":
        return "/supply?tab=po"
    if o.source_type == "batch" and o.deal_id:
        return f"/deals/{o.deal_id}?tab=logistics"
    if o.source_type == "calculation" and o.deal_id:
        return f"/deals/{o.deal_id}?tab=versions"
    if o.deal_id:
        return f"/deals/{o.deal_id}"
    return None


def dto(o: FinanceObligation, st: dict, deals: dict[str, Deal], contracts: dict[str, Contract]) -> dict:
    """Allowlist DTO строки реестра — только перечисленные поля, без basis_snapshot и экономики сделки."""
    d = deals.get(o.deal_id or "")
    k = contracts.get(o.contract_id or "")
    return {
        "id": o.id, "direction": o.direction, "nature": o.nature, "title": o.title,
        "source": {"type": o.source_type, "type_title": SOURCE_TITLES.get(o.source_type, o.source_type), "id": o.source_id, "line_key": o.source_line_key,
                   "line_no": o.source_line_no, "number": k.number if k else source_number(o), "url": source_url(o)},
        "deal": {"id": d.id, "number": d.number, "url": f"/deals/{d.id}"} if d else None,
        "contract": {"id": k.id, "number": k.number, "status": k.status, "url": f"/legal?tab=contracts&open={k.id}"} if k else None,
        "legal_entity_id": o.legal_entity_id,
        "counterparty": {"kind": o.counterparty_kind, "id": o.counterparty_id, "name": o.counterparty_name_snapshot},
        "amount": s(o.amount), "currency": o.currency,
        "confirmed_paid": s(st["confirmed_paid"]), "provisional_paid": s(st["provisional_paid"]), "remaining": s(st["remaining"]),
        "legal_due_on": o.legal_due_on.isoformat() if o.legal_due_on else None, "legal_due_text": o.legal_due_text,
        "forecast_on": o.forecast_on.isoformat() if o.forecast_on else None,
        "event": {"code": o.event_code, "on": o.event_on.isoformat() if o.event_on else None, "is_fact": o.event_is_fact},
        "status": st["status"], "status_title": STATUS_TITLES.get(st["status"], st["status"]), "days_overdue": st["days_overdue"],
        "is_active": o.is_active, "excluded_reason": o.excluded_reason, "sync_status": o.sync_status, "flags": st["flags"],
    }


def _related(db: Session, rows: list[FinanceObligation]) -> tuple[dict[str, Deal], dict[str, Contract]]:
    deal_ids = {o.deal_id for o in rows if o.deal_id}
    contract_ids = {o.contract_id for o in rows if o.contract_id}
    deals = {d.id: d for d in db.scalars(select(Deal).where(Deal.id.in_(deal_ids))).all()} if deal_ids else {}
    contracts = {c.id: c for c in db.scalars(select(Contract).where(Contract.id.in_(contract_ids))).all()} if contract_ids else {}
    return deals, contracts


@dataclass
class ObligationFilter:
    as_of: date
    direction: Optional[str] = None
    currency: Optional[str] = None
    legal_entity_id: Optional[str] = None
    deal_id: Optional[str] = None
    contract_id: Optional[str] = None
    source_type: Optional[str] = None
    status: Optional[str] = None           # paid | partially_paid | overdue | no_date | due_soon | planned | open (не оплачено)
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    search: str = ""
    include_inactive: bool = False
    reconciliation: bool = False
    ids: Optional[list[str]] = None
    limit: int = 100
    offset: int = 0


def query(db: Session, vis, f: ObligationFilter) -> dict:
    stmt = select(FinanceObligation)
    if not f.include_inactive:
        stmt = stmt.where(FinanceObligation.is_active.is_(True))
    for col, val in ((FinanceObligation.direction, f.direction), (FinanceObligation.currency, f.currency), (FinanceObligation.legal_entity_id, f.legal_entity_id),
                     (FinanceObligation.deal_id, f.deal_id), (FinanceObligation.contract_id, f.contract_id), (FinanceObligation.source_type, f.source_type)):
        if val:
            stmt = stmt.where(col == val)
    if f.ids is not None:
        stmt = stmt.where(FinanceObligation.id.in_(f.ids or ["-"]))
    rows = [o for o in db.scalars(stmt).all() if vis.obligation(o)]
    paid = paid_map(db, [o.id for o in rows])
    deals, contracts = _related(db, rows)
    items = []
    for o in rows:
        st = status_of(o, paid[o.id], f.as_of)
        on = effective_on(o)
        if f.status == "open" and st["remaining"] <= 0:
            continue
        if f.status and f.status != "open" and st["status"] != f.status:
            continue
        if f.reconciliation and not st["flags"]:
            continue
        if f.date_from and (not on or on < f.date_from):
            continue
        if f.date_to and (not on or on > f.date_to):
            continue
        if f.search:
            q = f.search.lower()
            d_, k_ = deals.get(o.deal_id or ""), contracts.get(o.contract_id or "")
            hay = " ".join([o.title, o.counterparty_name_snapshot, d_.number if d_ else "", k_.number if k_ else "", source_number(o)]).lower()
            if q not in hay:
                continue
        items.append((on, o, st))
    items.sort(key=lambda x: (x[0] is None, x[0] or date.max, x[1].direction, x[1].title))
    totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"in": ZERO, "out": ZERO, "in_remaining": ZERO, "out_remaining": ZERO})
    for _, o, st in items:
        if o.is_active:
            t = totals[o.currency]
            t[o.direction] += o.amount
            t[f"{o.direction}_remaining"] += st["remaining"]
    page = items[f.offset:f.offset + f.limit]
    return {"total": len(items), "limit": f.limit, "offset": f.offset, "as_of": f.as_of.isoformat(),
            "items": [dto(o, st, deals, contracts) for _, o, st in page],
            "totals": {c: {k: s(v) for k, v in t.items()} for c, t in sorted(totals.items())}}
