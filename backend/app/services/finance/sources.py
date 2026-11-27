"""Источники проекции обязательств помимо договоров (docs/finance/03, разделы 9–10).

- Заказ поставщику (`PurchaseOrder.payments`, формат `{due, amount, comment, paid_on}`, валюта — `PurchaseOrder.currency`):
  каждая строка — обязательство «мы платим» с юридическим сроком `due`. `paid_on` — старая отметка → запись «требует сверки».
  Проецируются только внешние заказы после подтверждения поставщиком (D-121); внутригрупповые — нет (D-122).
- Логистическая партия (`Batch.payments`): формат в CRM не задан и поле пока нигде не заполняется (сверка 10.11.2026).
  Адаптер принимает `{due|on, amount, currency, payee|counterparty, comment|title, paid_on}`; валюту не выдумываем —
  строка без валюты уходит в конфликт «валюта не определена» (D-123).
- Гарантийная претензия заводу (`WarrantyClaim.compensations`, вид «деньгами»): сумма, которую завод признал и должен вернуть, —
  обязательство «нам платят», срок не согласован; отметка «получено» — старая отметка → запись «требует сверки» (D-149).
- Запчасти: ожидаемые поступления по заказанным, но не отгруженным строкам заявок клиентов — не обязательства с датой,
  а отдельный блок «ожидается, срок не согласован» (только чтение, D-124).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Batch, Deal, Factory, FinanceObligation, PurchaseOrder, WarrantyClaim
from .money import dec, s
from .obligations import LineSpec, SyncStats, project_lines
from ...core.clock import today as msk_today

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


# ------------------------------------------------------------------ претензия по договору: неустойки, вписанные вручную

def sync_contract_claim(db: Session, cl, user_id: Optional[str] = None) -> SyncStats:
    """Строки-неустойки претензии без привязки к условию договора (вписаны вручную, чаще во входящей) — в «Финансы» (D-150).
    Неустойки по условиям договора идут через договор (`penalty`), долг — это строки графика, их не дублируем.
    Предъявлена (исходящая отправлена / входящая получена) — сумма строк; ответ «удовлетворена частично» — пропорционально;
    «отклонена», отменена, закрыта без признания — строка выключена."""
    from ...models import Contract
    c = db.get(Contract, cl.contract_id)
    manual = [x for x in (cl.lines or []) if x.get("kind") == "penalty" and not x.get("penalty_key") and x.get("amount")]
    specs = []
    if c is not None and manual:
        total_all = sum(float(x.get("amount") or 0) for x in cl.lines or [] if x.get("kind") in ("debt", "penalty"))
        share = 1.0
        if cl.result == "partial" and total_all:
            share = min(1.0, float(cl.result_amount or 0) / total_all)
        claimed = cl.status in ("sent", "received", "answered", "court", "closed") and cl.result not in ("rejected",)
        if cl.status == "closed" and cl.result not in ("satisfied", "partial"):
            claimed = False
        recognized = cl.result in ("satisfied", "partial")
        from ..legal_contracts import cp_display
        cp = cp_display(db, c) or ""
        direction = "in" if cl.direction == "outgoing" else "out"
        due = cl.answer_due if not recognized else ((cl.answer_on or msk_today()) + timedelta(days=10))
        for i, x in enumerate(manual):
            amount = dec(round(float(x["amount"]) * share, 2))
            fields = {"deal_id": cl.deal_id or c.deal_id, "contract_id": c.id, "legal_entity_id": c.own_entity_id, "counterparty_kind": c.cp_kind or "other",
                      "counterparty_id": c.cp_id, "counterparty_name_snapshot": cp[:255], "direction": direction, "nature": "penalty",
                      "title": f"{cl.number}: {x.get('title') or 'неустойка'} ({'признана' if recognized else 'предъявлена'})"[:255], "event_code": "recognized" if recognized else "claimed",
                      "source_line_no": i + 1, "legal_due_on": None, "legal_due_text": "ожидаемая дата: срок ответа на претензию" if not recognized else "ожидаемая дата: 10 дней после признания",
                      "event_on": None, "event_is_fact": False, "forecast_on": due}
            active, reason = (True, "") if claimed else (False, "претензия отклонена, отменена или закрыта без признания")
            specs.append(LineSpec(key=f"line:{i + 1}", revision=_rev({"amount": s(amount), "rec": recognized, "active": active, "due": str(due)}), amount=amount,
                                  currency=cl.currency, fields=fields, basis={"claim_number": cl.number, "claim_status": cl.status, "result": cl.result, "line": x},
                                  active=active, reason=reason))
    return project_lines(db, "claim", cl.id, specs, user_id, cl.deal_id, "строка удалена из претензии")


# ------------------------------------------------------------------ гарантийная претензия: компенсация деньгами

def sync_warranty_claim(db: Session, c: WarrantyClaim, user_id: Optional[str] = None) -> SyncStats:
    """Компенсация деньгами по претензии — ожидаемое поступление от завода (D-149). Деталью — уже есть в заказе заводу (цена 0);
    скидкой — не деньги, в «Финансы» не идёт."""
    supplier = db.get(Factory, c.supplier_id) if c.supplier_id else None
    comps = [x for x in (c.compensations or []) if x.get("kind") == "money"]
    specs = []
    for i, x in enumerate(comps):
        amount, currency = dec(x.get("amount")), (x.get("currency") or "").upper() or None
        fields = {"deal_id": c.deal_id, "contract_id": None, "legal_entity_id": None, "counterparty_kind": "factory", "counterparty_id": c.supplier_id,
                  "counterparty_name_snapshot": (supplier.name if supplier else "")[:255], "direction": "in", "nature": "operational",
                  "title": f"{c.number}: гарантийная компенсация" + (f" — {x['note']}" if x.get("note") else ""), "event_code": "warranty_comp", "source_line_no": i + 1,
                  "legal_due_on": None, "legal_due_text": "срок возврата заводом не согласован", "event_on": None, "event_is_fact": False, "forecast_on": None}
        fields["title"] = fields["title"][:255]
        active, reason = (False, "претензия закрыта без получения") if c.status == "closed" and not x.get("received_on") else (True, "")
        basis = {"claim_number": c.number, "claim_status": c.status, "compensation": {k: x.get(k) for k in ("id", "amount", "currency", "note")}}
        specs.append(LineSpec(key=f"comp:{x['id']}", revision=_rev({"amount": s(amount), "currency": currency, "active": active}), amount=amount, currency=currency,
                              fields=fields, basis=basis, active=active, reason=reason))
    st = project_lines(db, "warranty_claim", c.id, specs, user_id, c.deal_id, "компенсация удалена из претензии")
    from . import legacy
    for x in comps:   # «получено» в претензии — отметка менеджера, не банковский факт
        legacy.record(db, user_id, origin_type="warranty_claim_legacy", origin_id=f"{c.id}:{x['id'][:20]}", deal_id=c.deal_id, direction="in",
                      amount=x.get("amount") if x.get("received_on") else None, currency=(x.get("currency") or "XXX").upper(), on=_date(x.get("received_on")),
                      note=f"{c.number}: гарантийная компенсация", source=("warranty_claim", c.id, f"comp:{x['id']}"), counterparty_name=supplier.name if supplier else "")
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
    for c in db.scalars(select(WarrantyClaim)).all():
        if any(x.get("kind") == "money" for x in c.compensations or []):
            total.add(sync_warranty_claim(db, c, user_id))
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
