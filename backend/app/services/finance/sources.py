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
