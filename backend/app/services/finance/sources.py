"""Источники проекции обязательств помимо договоров (docs/finance/03, разделы 9–10).

- Заказ поставщику (`PurchaseOrder.payments`, формат `{due, amount, comment, paid_on}`, валюта — `PurchaseOrder.currency`):
  каждая строка — обязательство «мы платим» с юридическим сроком `due`. `paid_on` — старая отметка → запись «требует сверки».
  Проецируются только внешние заказы после подтверждения поставщиком (D-121); внутригрупповые — нет (D-122).
- Логистическая партия (`Batch.payments`): формат в CRM не задан и поле пока нигде не заполняется (сверка 10.11.2026).
  Адаптер принимает `{due|on, amount, currency, payee|counterparty, comment|title, paid_on}`; валюту не выдумываем —
  строка без валюты уходит в конфликт «валюта не определена» (D-123).
- Запчасти: ожидаемые поступления по заказанным, но не отгруженным строкам заявок клиентов — не обязательства с датой,
  а отдельный блок «ожидается, срок не согласован» (только чтение, D-124).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Batch, Deal, Factory, FinanceObligation, PurchaseOrder
from .money import dec, s
from .obligations import LineSpec, SyncStats, project_lines

PO_STATUSES = ("confirmed", "in_progress", "closed", "cancelled")


def _rev(sig: dict) -> str:
    return hashlib.sha256(json.dumps(sig, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _date(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _key(row: dict, i: int) -> str:
    return f"payment:{row.get('line_uid') or i + 1}"


# ------------------------------------------------------------------ заказ поставщику

def po_eligible(po: PurchaseOrder) -> bool:
    return po.kind == "external" and po.status in PO_STATUSES


def sync_purchase_order(db: Session, po: PurchaseOrder, user_id: Optional[str] = None) -> SyncStats:
    """Проекция графика оплат заказа поставщику. Вызывается в той же транзакции, что и изменение заказа."""
    if not po_eligible(po):
        return SyncStats()
    supplier = db.get(Factory, po.supplier_id) if po.supplier_id else None
    currency = (po.currency or "").upper() or None
    specs = []
    for i, row in enumerate(po.payments or []):
        amount = dec(row.get("amount"))
        due = _date(row.get("due"))
        fields = {"deal_id": None, "contract_id": None, "legal_entity_id": po.buyer_entity_id, "counterparty_kind": "factory", "counterparty_id": po.supplier_id,
                  "counterparty_name_snapshot": (supplier.name if supplier else "")[:255], "direction": "out", "nature": "contractual",
                  "title": f"{po.number}: оплата {i + 1}" + (f" — {row['comment']}" if row.get("comment") else ""), "event_code": "po_due", "source_line_no": i + 1,
                  "legal_due_on": due, "legal_due_text": "" if due else "срок оплаты в заказе не указан", "event_on": None, "event_is_fact": False, "forecast_on": due}
        fields["title"] = fields["title"][:255]
        active, reason = (False, "заказ отменён") if po.status == "cancelled" else (True, "")
        basis = {"po_number": po.number, "po_status": po.status, "supplier_ref": po.supplier_ref, "line": {k: row.get(k) for k in ("due", "amount", "comment", "line_uid")}}
        specs.append(LineSpec(key=_key(row, i), revision=_rev({"due": row.get("due"), "amount": s(amount), "currency": currency}), amount=amount, currency=currency,
                              fields=fields, basis=basis, active=active, reason=reason))
    st = project_lines(db, "purchase_order", po.id, specs, user_id, None, "строка удалена из графика оплат заказа")
    st.contracts = 1
    from . import legacy
    for i, row in enumerate(po.payments or []):   # «оплачено» в заказе — старая отметка, не банковский факт (D-117)
        legacy.record(db, user_id, origin_type="purchase_order_legacy", origin_id=f"{po.id}:pay:{i + 1}", deal_id=None, direction="out",
                      amount=row.get("amount") if row.get("paid_on") else None, currency=currency or "XXX", on=_date(row.get("paid_on")),
                      note=row.get("comment") or "", source=("purchase_order", po.id, _key(row, i)), counterparty_name=supplier.name if supplier else "")
    return st


# ------------------------------------------------------------------ логистическая партия

def sync_batch(db: Session, b: Batch, user_id: Optional[str] = None, deleted: bool = False) -> SyncStats:
    """Проекция графика платежей партии перевозчику/брокеру. Партию удалили — строки выводятся из итогов, история остаётся."""
    deal = db.get(Deal, b.deal_id)
    specs = []
    for i, row in enumerate([] if deleted else (b.payments or [])):
        amount = dec(row.get("amount"))
        currency = (str(row.get("currency") or "").upper() or None)
        due = _date(row.get("due") or row.get("on") or row.get("date"))
        payee = str(row.get("payee") or row.get("counterparty") or "")
        what = str(row.get("comment") or row.get("title") or "")
        fields = {"deal_id": b.deal_id, "contract_id": None, "legal_entity_id": None, "counterparty_kind": "other", "counterparty_id": None,
                  "counterparty_name_snapshot": payee[:255], "direction": "out", "nature": "operational",
                  "title": (f"{deal.number if deal else ''} · {b.name}: платёж {i + 1}" + (f" — {what}" if what else ""))[:255], "event_code": "batch_due",
                  "source_line_no": i + 1, "legal_due_on": due, "legal_due_text": "" if due else "срок платежа в партии не указан",
                  "event_on": None, "event_is_fact": False, "forecast_on": due}
        basis = {"batch": b.name, "line": {k: row.get(k) for k in ("due", "on", "amount", "currency", "payee", "counterparty", "comment", "title", "line_uid")}}
        specs.append(LineSpec(key=_key(row, i), revision=_rev({"due": str(due), "amount": s(amount), "currency": currency, "payee": payee}),
                              amount=amount, currency=currency, fields=fields, basis=basis))
    st = project_lines(db, "batch", b.id, specs, user_id, b.deal_id, "партия удалена" if deleted else "строка удалена из графика платежей партии")
    st.contracts = 1
    from . import legacy
    for i, row in enumerate(b.payments or []):
        legacy.record(db, user_id, origin_type="batch_legacy", origin_id=f"{b.id}:pay:{i + 1}", deal_id=b.deal_id, direction="out",
                      amount=row.get("amount") if (row.get("paid_on") and not deleted) else None,
                      currency=(str(row.get("currency") or "").upper() or "XXX"), on=None if deleted else _date(row.get("paid_on")),
                      note=str(row.get("comment") or ""), source=("batch", b.id, _key(row, i)), counterparty_name=str(row.get("payee") or ""))
    return st


def rebuild_sources(db: Session, user_id: Optional[str]) -> SyncStats:
    total = SyncStats()
    for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.kind == "external", PurchaseOrder.status.in_(PO_STATUSES))).all():
        total.add(sync_purchase_order(db, po, user_id))
    for b in db.scalars(select(Batch)).all():
        if b.payments or db.scalar(select(FinanceObligation.id).where(FinanceObligation.source_type == "batch", FinanceObligation.source_id == b.id)):
            total.add(sync_batch(db, b, user_id))
    for d in db.scalars(select(Deal).where(Deal.approved_version_id.is_not(None))).all():
        total.add(sync_calculation(db, d, user_id))
    return total


# ------------------------------------------------------------------ запчасти без согласованной даты

def parts_undated(db: Session, user) -> dict:
    """Заказано клиентами и не отгружено — ожидаемые поступления без даты. В датированный календарь и кривую не входят (D-018)."""
    from ..parts_deals import rows
    items = [{"id": p["id"], "number": p["number"], "client": p["client_name"], "manager": p["manager_name"], "amount": s(dec(p["ordered_rub"])), "currency": "RUB",
              "lines": p["lines"], "urgent": p["urgent"], "url": p["url"]}
             for p in rows(db, user, include_closed=False) if p.get("block") == "won" and p.get("ordered_rub")]
    total = sum((dec(x["amount"]) for x in items), dec(0))
    return {"items": sorted(items, key=lambda x: -float(x["amount"])), "total": s(total), "currency": "RUB",
            "note": "Ожидаемые поступления по заказанным и не отгруженным запчастям. Срок оплаты не согласован — в календарь по датам не входят. "
                    "Связь с договором «С клиентом: запасные части» в CRM пока не ведётся: после подписания такого договора его строки появятся в календаре, "
                    "а эта сумма — пока строки не отгружены — продолжит показываться здесь (не складывайте их)."}


# ------------------------------------------------------------------ утверждённый расчёт: прогноз до договора и замещение договором

def _deal_won(deal) -> bool:
    from ...core.workflow import POST_WIN_STATUSES, DealStatus
    if deal.status in (DealStatus.LOST, DealStatus.CANCELLED) or getattr(deal, "deleted_at", None):
        return False
    return deal.status in POST_WIN_STATUSES or bool(deal.won_variant_id)


def _deal_contracts(db: Session, deal_id: str) -> list:
    """Подписанные договоры сделки (основная сделка или рамочный по нескольким сделкам)."""
    from ...models import Contract
    out = []
    for c in db.scalars(select(Contract).where(Contract.status.in_(("active", "closed")), Contract.doc_type != "amendment")).all():
        if c.deal_id == deal_id or deal_id in (c.deal_ids or []):
            out.append(c)
    return out


def _contract_line(db: Session, contracts: list, line_no: int) -> Optional[FinanceObligation]:
    if len(contracts) != 1:
        return None
    return db.scalar(select(FinanceObligation).where(FinanceObligation.source_type == "contract", FinanceObligation.source_id == contracts[0].id,
                                                     FinanceObligation.source_line_no == line_no, FinanceObligation.is_active.is_(True)))


def sync_calculation(db: Session, deal, user_id: Optional[str] = None) -> SyncStats:
    """Прогноз платежей по утверждённой версии расчёта (базовый сценарий, выигравший вариант): график клиента и графики заводу
    по позициям. Ключи от неизменяемой версии; новая утверждённая версия выводит прежнюю проекцию из итогов (история остаётся).
    Подписанный договор замещает прогноз своей стороны: при однозначной строке — superseded_by, иначе — исключение с причиной (D-126, D-127)."""
    from ...core.workflow import DealStatus
    from ...models import Client, DealVersion, Position, Variant
    from ..deal_service import audit
    from .obligations import _active_alloc_total
    st = SyncStats()
    db.flush()   # сессия без autoflush: выборки ниже должны видеть изменения договора/сделки этой транзакции
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    won = _deal_won(deal)
    specs: list[LineSpec] = []
    if ver is not None and (won or db.scalar(select(FinanceObligation.id).where(FinanceObligation.source_type == "calculation", FinanceObligation.source_id == ver.id))):
        variants = (ver.calc_result or {}).get("variants") or {}
        vid = deal.won_variant_id if deal.won_variant_id in variants else next(
            (v.id for v in db.scalars(select(Variant).where(Variant.deal_id == deal.id, Variant.is_default.is_(True))).all() if v.id in variants), next(iter(variants), None))
        base = (variants.get(vid) or {}).get("base") or {}
        client = db.get(Client, deal.client_id) if deal.client_id else None
        contracts = _deal_contracts(db, deal.id)
        client_contracts = [c for c in contracts if c.direction == "income"]
        closed = deal.status == DealStatus.CLOSED
        inactive_reason = "" if won and not closed else ("сделка закрыта" if closed else "сделка не выиграна или отменена")

        def spec(key: str, no: int, on, amount, currency: str, direction: str, cp_kind: str, cp_id, cp_name: str, title: str, event: str,
                 basis: dict, side_contracts: list) -> LineSpec:
            amt = dec(amount)
            fields = {"deal_id": deal.id, "contract_id": None, "legal_entity_id": None, "counterparty_kind": cp_kind, "counterparty_id": cp_id,
                      "counterparty_name_snapshot": (cp_name or "")[:255], "direction": direction, "nature": "forecast", "title": title[:255], "event_code": event,
                      "source_line_no": no, "legal_due_on": None, "legal_due_text": f"прогноз по утверждённому расчёту (версия {ver.version_no}); юридический срок — после подписания договора",
                      "event_on": None, "event_is_fact": False, "forecast_on": _date(on), "superseded_by_id": None}
            active, reason = (won and not closed), inactive_reason
            if active and side_contracts:
                target = _contract_line(db, side_contracts, no)
                nums = ", ".join(c.number for c in side_contracts)
                fields["superseded_by_id"] = target.id if target else None
                active, reason = False, (f"заменено строкой договора {nums}" if target else f"заменено договором {nums}: построчное соответствие не установлено")
            status = "ok"
            if not active:   # прогноз вне итогов, а по нему уже разнесены деньги — их надо перенести на строку договора (сверка)
                prev = db.scalar(select(FinanceObligation).where(FinanceObligation.source_type == "calculation", FinanceObligation.source_id == ver.id,
                                                                 FinanceObligation.source_line_key == key))
                if prev is not None and _active_alloc_total(db, prev.id) > 0:
                    status, reason = "conflict", f"{reason}; есть разнесения — перенесите их на строку договора"
            return LineSpec(key=key, revision=_rev({"on": str(on), "amount": s(amt), "currency": currency, "event": event, "version": ver.id}),
                            amount=amt, currency=currency, fields=fields, basis={"version_no": ver.version_no, "variant_id": vid, "scenario": "base", **basis},
                            active=active, reason=reason, status=status)

        for row in base.get("payment_dates") or []:
            no = int(row.get("no") or 0)
            specs.append(spec(f"client:{vid}:{row.get('line_uid') or no}", no, row.get("on"), row.get("amount_rub"), "RUB", "in", "client", deal.client_id,
                              client.name if client else "", f"{deal.number}: прогноз оплаты клиента — строка {no}", str(row.get("event") or ""),
                              {"line": {k: row.get(k) for k in ("no", "event", "on", "amount_rub", "via")}}, client_contracts))
        for pc in base.get("positions") or []:
            pos = db.get(Position, pc.get("position_id"))
            fac = db.get(Factory, pos.factory_id) if pos and pos.factory_id else None
            fac_contracts = [c for c in contracts if c.direction == "expense" and fac is not None and c.cp_id == fac.id]
            for row in pc.get("factory_payment_dates") or []:
                no = int(row.get("no") or 0)
                cur = (str(row.get("currency") or "").upper() or None)
                specs.append(spec(f"factory:{pc.get('position_id')}:{row.get('line_uid') or no}", no, row.get("on"), row.get("amount"), cur, "out", "factory",
                                  fac.id if fac else None, fac.name if fac else "", f"{deal.number}: прогноз оплаты заводу ({pos.model if pos else 'позиция'}) — строка {no}",
                                  str(row.get("event") or ""), {"position_id": pc.get("position_id"), "line": {k: row.get(k) for k in ("no", "event", "on", "amount", "currency")}},
                                  fac_contracts))
        st = project_lines(db, "calculation", ver.id, specs, user_id, deal.id, "строка удалена из утверждённого расчёта")
        st.contracts = 1
    db.flush()
    # прежние утверждённые версии — история: из итогов выводятся, не удаляются
    for o in db.scalars(select(FinanceObligation).where(FinanceObligation.source_type == "calculation", FinanceObligation.deal_id == deal.id,
                                                        FinanceObligation.is_active.is_(True))).all():
        if ver is None or o.source_id != ver.id:
            o.is_active, o.excluded_reason = False, f"заменено утверждённой версией {ver.version_no}" if ver else "утверждённая версия снята"
            audit(db, user_id, deal.id, "finance_obligation", o.id, "superseded_version", after={"version": ver.id if ver else None})
            st.updated += 1
    db.flush()
    # прогнозная строка вне итогов, а по ней есть разнесения — деньги надо перенести на строку договора (сверка)
    for o in db.scalars(select(FinanceObligation).where(FinanceObligation.source_type == "calculation", FinanceObligation.deal_id == deal.id,
                                                        FinanceObligation.is_active.is_(False), FinanceObligation.sync_status == "ok")).all():
        if (ver is None or o.source_id != ver.id) and _active_alloc_total(db, o.id) > 0:   # прежние версии; текущая — через LineSpec.status
            o.sync_status = "conflict"
            o.excluded_reason = (o.excluded_reason + "; есть разнесения — перенесите их на строку договора").strip("; ")
            audit(db, user_id, deal.id, "finance_obligation", o.id, "conflict", comment="прогноз замещён, а разнесения остались на прогнозной строке")
            st.conflicts += 1
    return st
