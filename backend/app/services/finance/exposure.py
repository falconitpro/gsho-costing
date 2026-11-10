"""«Обеспечение и неустойки» (docs/finance/01, раздел 10; 04 — GET /api/finance/legal-exposure; 05, раздел 12; D-138, D-139).

Только чтение юридического блока и исполнения — финансовый раздел условия и решения не редактирует:
- обеспечение: `Contract.security` (гарантия, аккредитив, удержание), `Deal.bank_guarantee`, этап исполнения «Открытие аккредитива»;
- неустойки: `legal_contracts.obligations()["penalties"]` — начисляется / начислена / предъявлена / признана / оплачена / не взыскиваем;
  в денежный прогноз (реестр обязательств) входят только предъявленные и признанные с суммой (D-132), начисляемые — нет.
Видимость — как у договора и сделки; неустойки и обеспечение по договорам с заводом — ещё и при праве «Условия оплаты» (как в реестре).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.roles import READ
from ...models import Contract, ContractClaim, Deal, ExecutionStage, FinanceObligation
from .. import legal_contracts as legal

SECURITY_KINDS = {"bank_guarantee": "банковская гарантия", "lc": "аккредитив", "retention": "гарантийное удержание", "advance_guarantee": "гарантия возврата аванса"}
PEN_GROUP = {"accruing": "accruing", "accrued": "accrued", "claimed": "claimed", "recognized": "recognized", "paid": "paid", "waived": "waived"}
CONTRACT_STATUSES = ("active", "closed", "terminated")


def _iso(v) -> Optional[str]:
    if v in (None, ""):
        return None
    return str(v)[:10]


def _sec_status(valid_to: Optional[str], today: date, given: str = "") -> tuple[str, str]:
    if given:
        return "custom", given
    if not valid_to:
        return "no_date", "срок не указан"
    try:
        d = date.fromisoformat(valid_to)
    except ValueError:
        return "no_date", "срок не указан"
    if d < today:
        return "expired", "срок истёк"
    if d <= today + timedelta(days=30):
        return "expires_soon", f"истекает через {(d - today).days} дн."
    return "active", "действует"


def _factory_hidden(user, c: Contract) -> bool:
    return c.cp_kind == "factory" and not user.can("payment_terms", READ)


def build(db: Session, user, vis, today: Optional[date] = None, deal_id: Optional[str] = None, contract_id: Optional[str] = None,
          direction: Optional[str] = None, status: Optional[str] = None, kind: Optional[str] = None) -> dict:
    today = today or date.today()
    q = select(Contract).where(Contract.status.in_(CONTRACT_STATUSES))
    if contract_id:
        q = q.where(Contract.id == contract_id)
    if deal_id:
        q = q.where(Contract.deal_id == deal_id)
    contracts = [c for c in db.scalars(q.order_by(Contract.number)).all() if vis.contract(c.id) and vis.deal(c.deal_id) and not _factory_hidden(user, c)]
    deal_ids = {c.deal_id for c in contracts if c.deal_id}
    deals = {d.id: d for d in db.scalars(select(Deal).where(Deal.id.in_(deal_ids))).all()} if deal_ids else {}

    # --- неустойки
    in_forecast = {(o.source_id, o.source_line_key): o for o in db.scalars(select(FinanceObligation).where(
        FinanceObligation.source_type == "penalty", FinanceObligation.is_active.is_(True), FinanceObligation.source_id.in_([c.id for c in contracts]))).all()} if contracts else {}
    claims_by_pen: dict[tuple[str, str], ContractClaim] = {}
    if contracts:
        for cl in db.scalars(select(ContractClaim).where(ContractClaim.contract_id.in_([c.id for c in contracts]), ContractClaim.status != "cancelled")).all():
            for ln in cl.lines or []:
                if ln.get("kind") == "penalty" and ln.get("penalty_key"):
                    claims_by_pen.setdefault((cl.contract_id, ln["penalty_key"]), cl)
    pens, secs = [], []
    for c in contracts:
        d = deals.get(c.deal_id or "")
        cp = legal.cp_display(db, c)
        base = {"contract": {"id": c.id, "number": c.number, "url": f"/legal?tab=contracts&open={c.id}", "status": c.status},
                "deal": {"id": d.id, "number": d.number, "url": f"/deals/{d.id}?tab=legal"} if d else None, "counterparty": cp}
        if not kind or kind == "penalty":
            for x in legal.obligations(db, c, today).get("penalties", []):
                dirn = "receivable" if x.get("payer") == "cp" else "payable"
                if direction and direction != dirn:
                    continue
                if status and status != x["status"]:
                    continue
                ob = in_forecast.get((c.id, f"penalty:{x['key']}"))
                cl = claims_by_pen.get((c.id, x["key"]))
                rub = legal.to_rub(db, x.get("amount"), x.get("currency") or c.currency)
                pens.append({**base, "key": x["key"], "direction": dirn, "reason_title": x["reason_title"], "obligation": x["obligation"], "days": x["days"],
                             "rate_text": x["rate_text"], "calc_amount": x["calc_amount"], "amount": x.get("amount"), "currency": x.get("currency") or c.currency,
                             "amount_rub": None if rub is None else round(rub, 2), "capped": x["capped"], "status": x["status"], "status_title": x["status_title"],
                             "in_forecast": ob is not None,
                             "forecast": {"obligation_id": ob.id, "on": (ob.legal_due_on or ob.forecast_on).isoformat() if (ob.legal_due_on or ob.forecast_on) else None,
                                          "url": f"/finance?tab=calendar&open={ob.id}"} if ob else None,
                             "claim": {"id": cl.id, "number": cl.number, "status": cl.status, "url": f"/legal?tab=claims&open={cl.id}"} if cl else None})
        sec = c.security or {}
        if (not kind or kind == "security") and sec.get("kind"):
            st, st_t = _sec_status(_iso(sec.get("valid_to")), today)
            if not status or status == st:
                amt = sec.get("amount")
                cur = (sec.get("currency") or c.currency or "RUB").upper()
                rub = legal.to_rub(db, float(amt), cur) if amt not in (None, "") else None
                secs.append({**base, "source": "contract", "kind": sec["kind"], "kind_title": SECURITY_KINDS.get(sec["kind"], sec["kind"]),
                             "direction": "provided_by_cp" if c.direction == "income" else "provided_by_us" if c.direction == "expense" else "other",
                             "amount": None if amt in (None, "") else float(amt), "currency": cur, "amount_rub": None if rub is None else round(rub, 2),
                             "valid_to": _iso(sec.get("valid_to")), "status": st, "status_title": st_t, "bank": sec.get("bank") or "", "note": sec.get("note") or ""})

    # --- обеспечение сделок: гарантия по сделке и аккредитив клиента (этап исполнения) — как в прежней сводке «Деньги»
    if not kind or kind == "security":
        dq = select(Deal).where(Deal.archived_at.is_(None), Deal.deleted_at.is_(None))
        if deal_id:
            dq = dq.where(Deal.id == deal_id)
        if contract_id:
            c0 = db.get(Contract, contract_id)
            dq = dq.where(Deal.id == (c0.deal_id if c0 and c0.deal_id else "-"))
        cand = [d for d in db.scalars(dq).all() if (d.bank_guarantee or {}).get("required") or (d.bank_guarantee or {}).get("amount")]
        lc_stages = {s.deal_id: s for s in db.scalars(select(ExecutionStage).where(ExecutionStage.code == "lc_opened")).all()}
        for d in cand + [db.get(Deal, did) for did in lc_stages if did not in {x.id for x in cand} and (not deal_id or did == deal_id)]:
            if d is None or d.archived_at is not None or d.deleted_at is not None or not vis.deal(d.id) or (contract_id and not any(c.deal_id == d.id for c in contracts)):
                continue
            dbase = {"contract": None, "deal": {"id": d.id, "number": d.number, "url": f"/deals/{d.id}?tab=execution"}, "counterparty": d.client.name if d.client else ""}
            bg = d.bank_guarantee or {}
            if bg.get("required") or bg.get("amount"):
                st, st_t = _sec_status(_iso(bg.get("until")), today)
                if not status or status == st:
                    cur = (bg.get("currency") or "RUB").upper()
                    amt = bg.get("amount")
                    rub = legal.to_rub(db, float(amt), cur) if amt not in (None, "") else None
                    secs.append({**dbase, "source": "deal", "kind": "bank_guarantee", "kind_title": "банковская гарантия по сделке", "direction": "provided_by_us",
                                 "amount": None if amt in (None, "") else float(amt), "currency": cur, "amount_rub": None if rub is None else round(rub, 2),
                                 "valid_to": _iso(bg.get("until")), "status": st, "status_title": (bg.get("status") or st_t), "bank": bg.get("bank") or "", "note": ""})
            lc = lc_stages.get(d.id)
            if lc is not None:
                opened = lc.actual_on is not None
                st, st_t = ("active", f"открыт {lc.actual_on:%d.%m.%Y}") if opened else ("planned", f"план {(lc.refined_on or lc.planned_on):%d.%m.%Y}" if (lc.refined_on or lc.planned_on) else "не открыт")
                if not status or status == st:
                    secs.append({**dbase, "source": "execution", "kind": "lc", "kind_title": "аккредитив клиента", "direction": "provided_by_cp",
                                 "amount": float(lc.amount) if lc.amount else None, "currency": (lc.currency or "RUB"), "amount_rub": legal.to_rub(db, float(lc.amount), lc.currency or "RUB") if lc.amount else None,
                                 "valid_to": None, "status": st, "status_title": st_t, "bank": "", "note": ""})

    tot: dict[str, dict[str, float]] = {"receivable": defaultdict(float), "payable": defaultdict(float)}
    for p in pens:
        if p["amount_rub"] is not None and p["status"] != "waived":
            tot[p["direction"]][p["status"]] += p["amount_rub"]
    no_rate = sum(1 for p in pens if p["amount"] and p["amount_rub"] is None)
    totals = {d: {k: round(v, 2) for k, v in t.items()} for d, t in tot.items()}
    for dname in ("receivable", "payable"):
        t = totals[dname]
        t["in_forecast"] = round(sum(p["amount_rub"] or 0 for p in pens if p["direction"] == dname and p["in_forecast"]), 2)
    sec_soon = sum(1 for x in secs if x["status"] in ("expires_soon", "expired"))
    return {"as_of": today.isoformat(), "security": secs, "penalties": pens, "totals": totals,
            "counts": {"security": len(secs), "security_attention": sec_soon, "penalties": len(pens), "no_rate": no_rate},
            "statuses": legal.PEN_STATUS, "note": "Условия и решения редактируются в юридическом блоке"}
