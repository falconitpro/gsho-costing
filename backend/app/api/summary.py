"""Сводные экраны по всем сделкам: «Отгрузки» (логист), «Деньги» (ФД), «Сервис» (сервис), «Закупки» (закупки).

Каждый экран собирает то, что иначе приходится искать внутри каждой сделки: этапы партий, платежи по датам,
приёмки и ТО машин, что заказать к какой дате.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.auth import CurrentUser, get_current_user
from ..core.roles import Role
from ..core.workflow import DealStatus
from ..db import get_db
from ..models import (AcceptanceUnit, Client, ClientMachine, Deal, ExecutionStage, Factory, LocalOption, MachineReminder, PartRequest, PoLine,
                      Position, PurchaseOrder, User, WarrantyClaim)
from .common import can_see_deal

router = APIRouter(prefix="/api/summary", tags=["summary"])
ACTIVE = (DealStatus.WON, DealStatus.CONTRACT, DealStatus.EXECUTION)


def _deals(db: Session, user: CurrentUser, statuses=ACTIVE) -> list[Deal]:
    rows = db.scalars(select(Deal).where(Deal.status.in_(statuses), Deal.archived_at.is_(None), Deal.deleted_at.is_(None)).order_by(Deal.number)).all()
    return [d for d in rows if can_see_deal(user, d)]


def _names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name for u in db.scalars(select(User)).all()}


def _stage_row(s: ExecutionStage, today: date) -> dict[str, Any]:
    on = s.actual_on or s.refined_on or s.planned_on
    return {"id": s.id, "code": s.code, "title": s.title, "planned_on": s.planned_on, "refined_on": s.refined_on, "actual_on": s.actual_on,
            "on": on, "done": s.actual_on is not None, "overdue_days": (today - on).days if on and not s.actual_on and on < today else 0,
            "amount": s.amount, "currency": s.currency, "actual_amount": s.actual_amount}


# ------------------------------------------------------------------ Отгрузки

@router.get("/shipments")
def shipments(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Все партии в пути и к отгрузке: этапы отгрузка → граница → ДТ → доставка, просрочки, готовность завода по позициям."""
    if not user.has(Role.LOGISTICIAN, Role.CD, Role.GD, Role.FD, Role.MANAGER, Role.PURCHASING):
        raise HTTPException(403)
    today = date.today()
    names = _names(db)
    out = []
    for d in _deals(db, user):
        sts = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == d.id)).all()
        by_batch: dict[str, list] = {}
        for s in sts:
            if s.batch_id:
                by_batch.setdefault(s.batch_id, []).append(s)
        ready = [_stage_row(s, today) for s in sts if s.code == "readiness"]
        for b in d.batches:
            stages = sorted(by_batch.get(b.id, []), key=lambda s: s.sort)
            rows = [_stage_row(s, today) for s in stages]
            nxt = next((r for r in rows if not r["done"]), None)
            out.append({"deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "", "site": d.site, "manager": names.get(d.manager_id, ""),
                        "batch_id": b.id, "batch": b.name, "route": b.route, "units": b.transport_units, "weight_kg": b.weight_kg, "volume_m3": b.volume_m3,
                        "stages": rows, "next": nxt, "overdue": max((r["overdue_days"] for r in rows), default=0),
                        "phase": ("доставлено" if rows and all(r["done"] for r in rows) else nxt["title"] if nxt else "—"),
                        "readiness": ready})
    out.sort(key=lambda r: (-(r["overdue"] > 0), r["next"]["on"].isoformat() if r["next"] and r["next"]["on"] else "9999"))
    return {"rows": out, "totals": {"batches": len(out), "overdue": sum(1 for r in out if r["overdue"] > 0),
                                    "in_transit": sum(1 for r in out if any(x["code"] == "shipment" and x["done"] for x in r["stages"]) and not all(x["done"] for x in r["stages"])),
                                    "to_ship_30d": sum(1 for r in out if r["next"] and r["next"]["code"] == "shipment" and r["next"]["on"] and r["next"]["on"] <= today + timedelta(days=30))}}


# ------------------------------------------------------------------ Деньги

@router.get("/money")
def money(days: int = 90, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Поступления и платежи по всем сделкам на горизонте N дней, просроченная дебиторка, аккредитивы и гарантии, кассовая кривая по неделям."""
    if not user.can("finance_analytics"):
        raise HTTPException(403, "Финансовая аналитика доступна ФД, КД и ЗГД")
    today = date.today()
    horizon = today + timedelta(days=days)
    names = _names(db)
    rows, receivables, guarantees = [], [], []
    for d in _deals(db, user):
        sts = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == d.id)).all()
        for s in sts:
            kind = {"client_advance": "in", "client_final": "in", "factory_advance": "out", "factory_final": "out"}.get(s.code)
            if not kind or s.actual_on:
                continue
            on = s.refined_on or s.planned_on
            if on is None:
                continue
            row = {"deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "", "manager": names.get(d.manager_id, ""),
                   "title": s.title, "direction": kind, "on": on, "amount": s.amount, "currency": s.currency,
                   "overdue_days": (today - on).days if on < today else 0}
            if on < today and kind == "in":
                receivables.append(row)
            if on <= horizon:
                rows.append(row)
        bg = d.bank_guarantee or {}
        if bg.get("required") or bg.get("amount"):
            guarantees.append({"deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "", "kind": "банковская гарантия", **{k: bg.get(k) for k in ("amount", "currency", "until", "status", "bank")}})
        lc = next((s for s in sts if s.code == "lc_opened"), None)
        if lc is not None:
            guarantees.append({"deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "", "kind": "аккредитив",
                               "amount": None, "currency": "", "until": None, "status": "открыт" if lc.actual_on else f"план {(lc.refined_on or lc.planned_on or today):%d.%m}", "bank": ""})
    if user.has(Role.FD, Role.GD, Role.CD, Role.LAWYER, Role.ADMIN):
        from ..services.legal_contracts import money_rows
        seen = {d.id for d in _deals(db, user)}
        for r in money_rows(db, today, horizon):
            if r["deal_id"] is None or r["deal_id"] in seen:
                rows.append(r)
                if r["overdue_days"] and r["direction"] == "in":
                    receivables.append(r)
    rows.sort(key=lambda r: r["on"])
    # кривая по неделям (в рублях — суммы в RUB; валютные суммы показываются отдельно)
    weeks: dict[str, dict[str, float]] = {}
    for r in rows:
        if r["currency"] != "RUB" or not r["amount"]:
            continue
        wk = (r["on"] - timedelta(days=r["on"].weekday())).isoformat()
        w = weeks.setdefault(wk, {"in": 0.0, "out": 0.0})
        w[r["direction"]] += float(r["amount"])
    curve, cum = [], 0.0
    for wk in sorted(weeks):
        cum += weeks[wk]["in"] - weeks[wk]["out"]
        curve.append({"week": wk, "in": round(weeks[wk]["in"], 2), "out": round(weeks[wk]["out"], 2), "cum": round(cum, 2)})
    return {"rows": rows, "receivables": receivables, "guarantees": guarantees, "curve": curve,
            "totals": {"in_rub": round(sum(float(r["amount"] or 0) for r in rows if r["direction"] == "in" and r["currency"] == "RUB"), 2),
                       "out_rub": round(sum(float(r["amount"] or 0) for r in rows if r["direction"] == "out" and r["currency"] == "RUB"), 2),
                       "receivable_count": len(receivables), "min_cum": min((c["cum"] for c in curve), default=0.0)}}


# ------------------------------------------------------------------ Сервис

@router.get("/service")
def service(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Приёмки (онлайн и на заводе) по плану, ПНР, напоминания ТО, открытые претензии, парк машин."""
    if not user.has(Role.SERVICE_HEAD, Role.CD, Role.GD, Role.MANAGER, Role.FD):
        raise HTTPException(403)
    today = date.today()
    names = _names(db)
    acceptances, pnr = [], []
    for d in _deals(db, user):
        sts = db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == d.id, ExecutionStage.code.in_(["factory_preacceptance", "factory_acceptance", "commissioning"]))).all()
        units = db.scalars(select(AcceptanceUnit).where(AcceptanceUnit.deal_id == d.id)).all()
        for s in sts:
            pos = db.get(Position, s.position_id) if s.position_id else None
            pu = [u for u in units if u.position_id == s.position_id]
            row = {**_stage_row(s, today), "deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "", "site": d.site,
                   "manager": names.get(d.manager_id, ""), "position": pos.designation if pos else "", "qty": int(pos.qty or 0) if pos else 0,
                   "accepted": sum(1 for u in pu if u.decision in ("accepted", "accepted_remarks")), "open_remarks": sum(len([r for r in (u.remarks or []) if r.get("text") and not r.get("fixed_on")]) for u in pu)}
            (pnr if s.code == "commissioning" else acceptances).append(row)
    acceptances.sort(key=lambda r: (r["done"], r["on"].isoformat() if r["on"] else "9999"))
    pnr.sort(key=lambda r: (r["done"], r["on"].isoformat() if r["on"] else "9999"))
    rem = db.scalars(select(MachineReminder).where(MachineReminder.dismissed.is_(False)).order_by(MachineReminder.due_on)).all()
    clients = {c.id: c.name for c in db.scalars(select(Client)).all()}
    factories = {f.id: f.name for f in db.scalars(select(Factory)).all()}
    reminders = []
    for r in rem:
        m = db.get(ClientMachine, r.machine_id)
        if m is None:
            continue
        reminders.append({"machine_id": m.id, "machine": f"{m.model or ''} {m.serial or ''}".strip(), "client": clients.get(m.client_id, ""),
                          "to_hours": r.to_hours, "due_on": r.due_on, "overdue_days": (today - r.due_on).days if r.due_on and r.due_on < today else 0, "request_id": r.request_id})
    claims = db.scalars(select(WarrantyClaim).where(WarrantyClaim.status.notin_(["closed", "rejected"])).order_by(WarrantyClaim.created_at.desc())).all()
    claim_rows = [{"id": c.id, "number": c.number, "status": c.status, "factory": factories.get(c.supplier_id, ""), "created_at": c.created_at} for c in claims]
    machines = db.scalar(select(func.count()).select_from(ClientMachine)) or 0
    return {"acceptances": acceptances, "pnr": pnr, "reminders": reminders, "claims": claim_rows,
            "totals": {"machines": int(machines), "acceptances_30d": sum(1 for a in acceptances if not a["done"] and a["on"] and a["on"] <= today + timedelta(days=30)),
                       "overdue": sum(1 for a in acceptances + pnr if a["overdue_days"] > 0), "reminders_due": sum(1 for r in reminders if r["overdue_days"] > 0 or (r["due_on"] and r["due_on"] <= today + timedelta(days=30))),
                       "claims_open": len(claim_rows)}}


# ------------------------------------------------------------------ Закупки

@router.get("/purchasing")
def purchasing(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Что закупить к какой дате: опции РФ по сделкам (срок поставки против готовности завода), заявки на запчасти без заказа, заказы поставщикам с ETA."""
    if not user.has(Role.PURCHASING, Role.CD, Role.GD, Role.SERVICE_HEAD, Role.FD):
        raise HTTPException(403)
    today = date.today()
    names = _names(db)
    options = []
    for d in _deals(db, user):
        ready = {s.position_id: (s.actual_on or s.refined_on or s.planned_on) for s in db.scalars(select(ExecutionStage).where(ExecutionStage.deal_id == d.id, ExecutionStage.code == "readiness")).all()}
        for o in db.scalars(select(LocalOption).where(LocalOption.deal_id == d.id)).all():
            if o.status in ("installed",):
                continue
            need_by = ready.get(o.position_id)
            order_by = (need_by - timedelta(days=int(o.supplier_lead_days or 0))) if need_by else None
            options.append({"deal_id": d.id, "deal_number": d.number, "client": d.client.name if d.client else "", "manager": names.get(d.manager_id, ""),
                            "option": o.name, "qty": o.qty, "supplier": o.supplier, "lead_days": o.supplier_lead_days, "status": o.status,
                            "need_by": need_by, "order_by": order_by, "ordered_at": o.ordered_at,
                            "late": bool(order_by and not o.ordered_at and order_by < today), "cost": o.cost if user.can("factory_price_history") else None, "currency": o.currency})
    options.sort(key=lambda r: (not r["late"], r["order_by"].isoformat() if r["order_by"] else "9999"))
    clients = {c.id: c.name for c in db.scalars(select(Client)).all()}
    factories = {f.id: f.name for f in db.scalars(select(Factory)).all()}
    reqs = db.scalars(select(PartRequest).where(PartRequest.closed == "").order_by(PartRequest.created_at)).all()
    req_rows = [{"id": r.id, "number": r.number, "urgent": r.urgent, "client": clients.get(r.client_id, ""), "created_at": r.created_at,
                 "lines": len(r.lines or []), "unordered": sum(1 for l in (r.lines or []) if getattr(l, "status", "") in ("new", "quoted", "in_kp", "ordered", "replaced"))} for r in reqs]
    req_rows = [r for r in req_rows if r["unordered"] > 0]
    pos = db.scalars(select(PurchaseOrder).where(PurchaseOrder.status.notin_(["closed", "cancelled"])).order_by(PurchaseOrder.created_at.desc())).all()
    po_rows = []
    for p in pos:
        lines = db.scalars(select(PoLine).where(PoLine.po_id == p.id)).all()
        etas = [l.eta for l in lines if l.eta]
        po_rows.append({"id": p.id, "number": p.number, "status": p.status, "supplier": factories.get(p.supplier_id, ""),
                        "lines": len(lines), "received": sum(1 for l in lines if (l.received_qty or 0) >= (l.qty or 0) and l.qty), "eta": min(etas) if etas else None,
                        "overdue": sum(1 for l in lines if l.eta and l.eta < today and (l.received_qty or 0) < (l.qty or 0))})
    return {"options": options, "requests": req_rows, "orders": po_rows,
            "totals": {"options_late": sum(1 for o in options if o["late"]), "options_to_order": sum(1 for o in options if not o["ordered_at"]),
                       "requests_open": len(req_rows), "orders_open": len(po_rows), "orders_overdue": sum(1 for p in po_rows if p["overdue"])}}
