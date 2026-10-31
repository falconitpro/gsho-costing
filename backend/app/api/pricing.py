"""Экран КД: расчёт в трёх сценариях, назначение цены с живым пересчётом, визация, возврат, утверждение, КП."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..calc import calc_deal, solve_price
from ..core.auth import CurrentUser, require_roles
from ..core.roles import Role
from ..core.workflow import (
    ApprovalStep, DealState, DealStatus, cd_submit_price, price_changed_after_visa, rework as wf_rework, visa_decision,
)
from ..db import get_db
from ..models import Approval, Attachment, Deal, DealVersion, FactoryPriceHistory, Variant
from ..services import settings_service
from ..services.deal_calc import approval_flags, build_calc_input, run_calc
from ..services.deal_service import apply_transition, audit, complete_tasks, create_version, ensure_folders, sla
from ..services.kp_generator import KpData, KpVariant, build_kp
from .common import get_deal_or_404, serialize

router = APIRouter(prefix="/api/deals/{deal_id}", tags=["pricing"])
CD = require_roles(Role.CD, Role.GD)
MGMT = require_roles(Role.CD, Role.FD, Role.GD)


def _ladder(db: Session) -> list[ApprovalStep]:
    rows = settings_service.get(db, "approval_ladder")
    status_by_role = {"cd": DealStatus.CD_APPROVAL, "fd": DealStatus.FD_VISA, "gd": DealStatus.GD_VISA}
    return [ApprovalStep(r["name"], Role(r["role"]), r["condition"], r.get("sla_days", 1), status_by_role[r["role"]]) for r in rows]


@router.get("/calc")
def get_calc(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(MGMT)):
    """Таблица 14 статей × 3 сценария по каждому варианту, денежный поток, курсы, чувствительность, история цен и КП."""
    deal = get_deal_or_404(db, deal_id, user)
    if deal.status in (DealStatus.DRAFT, DealStatus.LOGISTICS_CALC, DealStatus.SERVICE_CALC) and not deal.logistics_done:
        raise HTTPException(409, "Расчёт ещё не сформирован: блок логистики не завершён")
    inp, res = run_calc(db, deal)
    price_hist = db.scalars(select(FactoryPriceHistory).where(FactoryPriceHistory.deal_id != deal.id,
                                                              FactoryPriceHistory.model.in_([p.model for p in deal.positions]))
                            .order_by(FactoryPriceHistory.on.desc()).limit(20)).all()
    prev_kp = db.scalars(select(Deal).where(Deal.client_id == deal.client_id, Deal.id != deal.id,
                                            Deal.status.in_([DealStatus.KP_SENT, DealStatus.WON, DealStatus.LOST])).limit(10)).all()
    client_kp_history = []
    for d in prev_kp:
        for v in d.variants:
            client_kp_history.append({"deal": d.number, "variant": v.name, "price_rub": v.price_rub, "status": d.status.value,
                                      "models": [p.model for p in d.positions if p.id in v.position_ids]})
    warn_lower = []
    for v in deal.variants:
        models = {p.model for p in deal.positions if p.id in v.position_ids}
        for h in client_kp_history:
            if h["price_rub"] and v.price_rub and set(h["models"]) & models and v.price_rub < h["price_rub"]:
                warn_lower.append(f"Вариант «{v.name}»: цена ниже предыдущего КП этому клиенту ({h['deal']}, {h['price_rub']:,.0f} RUB)")
    comments = []
    for p in deal.positions:
        for k, c in ((p.logistics or {}).get("checks") or {}).items():
            if c.get("comment"):
                comments.append({"from": "логист", "item": k, "ref": p.model, "comment": c["comment"]})
        if (p.price_reference or {}).get("comment"):
            comments.append({"from": "менеджер", "item": "цена завода", "ref": p.model, "comment": p.price_reference["comment"]})
    return {
        "input": inp.model_dump(mode="json"), "result": res.model_dump(mode="json"),
        "approval_flags": approval_flags(res), "threshold_pct": inp.thresholds.margin_threshold_pct,
        "factory_price_history": [serialize(h) for h in price_hist], "client_kp_history": client_kp_history,
        "warnings": res.warnings + warn_lower, "comments": comments,
        "status": deal.status.value, "scenario_override": deal.scenario_override,
    }


class PriceIn(BaseModel):
    variant_id: str
    mode: str = "amount"            # amount | markup | margin
    value: float
    service_in_kp: Optional[bool] = None
    client_payments: Optional[list[dict]] = None
    overhead_pct_override: Optional[float] = None
    overhead_override_comment: str = ""


@router.post("/price")
def set_price(deal_id: str, body: PriceIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD)):
    """Цена суммой, наценкой или целевой маржой — остальные два пересчитываются; живой пересчёт маржи/разрыва."""
    deal = get_deal_or_404(db, deal_id, user)
    v = db.get(Variant, body.variant_id)
    if v is None or v.deal_id != deal.id:
        raise HTTPException(404)
    if body.overhead_pct_override is not None and not body.overhead_override_comment:
        raise HTTPException(422, "Переопределение накладных требует комментария (и визы ГД)")
    before = serialize(v)
    if body.service_in_kp is not None:
        v.service_in_kp = body.service_in_kp
    if body.client_payments is not None:
        v.client_payments = body.client_payments
    v.overhead_pct_override, v.overhead_override_comment = body.overhead_pct_override, body.overhead_override_comment
    db.flush()
    inp = build_calc_input(db, deal)
    if body.mode == "amount":
        price = body.value
    elif body.mode == "markup":
        price = solve_price(inp, v.id, target_markup_pct=body.value)
    elif body.mode == "margin":
        price = solve_price(inp, v.id, target_margin_pct=body.value)
    else:
        raise HTTPException(422, "mode: amount | markup | margin")
    v.price_rub, v.price_mode = round(price, 2), body.mode
    reset = price_changed_after_visa(DealState(status=deal.status, service_flag=deal.service_flag))
    if reset and before.get("price_rub") != v.price_rub:
        for a in db.scalars(select(Approval).where(Approval.deal_id == deal.id, Approval.reset_at.is_(None))).all():
            a.reset_at = datetime.now(timezone.utc)
        apply_transition(db, deal, reset, user.id)
    audit(db, user.id, deal.id, "variant", v.id, "price", {"price_rub": before.get("price_rub")}, {"price_rub": v.price_rub, "mode": body.mode})
    db.commit()
    inp, res = run_calc(db, deal)
    return {"variant": serialize(v), "result": res.variants[v.id], "approval_flags": approval_flags(res), "warnings": res.warnings}


class ComparePaymentsIn(BaseModel):
    variant_id: str
    alternative: list[dict]


@router.post("/compare-payments")
def compare_payments(deal_id: str, body: ComparePaymentsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(MGMT)):
    """Сравнение двух наборов условий оплаты для одного варианта (например, с гарантией и без)."""
    deal = get_deal_or_404(db, deal_id, user)
    inp = build_calc_input(db, deal)
    base = calc_deal(inp).variants[body.variant_id]
    for v in inp.variants:
        if v.id == body.variant_id:
            from ..calc.types import PaymentLine
            v.client_payments = [PaymentLine(**l) for l in body.alternative]
    alt = calc_deal(inp).variants[body.variant_id]
    return {"current": base, "alternative": alt}


class ScenarioOverrideIn(BaseModel):
    dk: dict[str, float] = {}
    dt: dict[str, int] = {}
    use_historical_volatility: Optional[bool] = None
    comment: str


@router.post("/scenario-override")
def scenario_override(deal_id: str, body: ScenarioOverrideIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD)):
    deal = get_deal_or_404(db, deal_id, user)
    if not body.comment:
        raise HTTPException(422, "Нужен комментарий")
    before = deal.scenario_override
    deal.scenario_override = body.model_dump(exclude_none=True)
    audit(db, user.id, deal.id, "deal", deal.id, "scenario_override", before, deal.scenario_override, body.comment)
    db.commit()
    return deal.scenario_override


@router.post("/submit")
def submit(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(CD)):
    """КД задал цену по каждому варианту → коммерческая виза; далее ФД/ГД по условиям или утверждение."""
    deal = get_deal_or_404(db, deal_id, user)
    if any(v.price_rub is None for v in deal.variants if v.in_kp):
        raise HTTPException(422, "Задайте цену по каждому варианту")
    inp, res = run_calc(db, deal)
    flags = approval_flags(res)
    st = DealState(status=deal.status, service_flag=deal.service_flag)
    try:
        tr = cd_submit_price(st, flags, date.today(), sla(db), _ladder(db))
    except Exception as e:
        raise HTTPException(409, str(e))
    db.add(Approval(deal_id=deal.id, step="commercial", role=user.roles and sorted(r.value for r in user.roles)[0], user_id=user.id,
                    approved=True, prices_snapshot={v.id: v.price_rub for v in deal.variants}))
    complete_tasks(db, deal, block="pricing")
    apply_transition(db, deal, tr, user.id)
    if tr.status == DealStatus.APPROVED:
        _finalize_approval(db, deal, user.id)
    db.commit()
    return {"status": deal.status.value, "approval_flags": flags, "reasons": res.approval.reasons}


class VisaIn(BaseModel):
    approved: bool
    comment: str = ""
    cd_insists: bool = False


@router.post("/visa")
def visa(deal_id: str, body: VisaIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.FD, Role.GD))):
    deal = get_deal_or_404(db, deal_id, user)
    by = Role.GD if deal.status == DealStatus.GD_VISA else Role.FD
    if by not in user.roles:
        raise HTTPException(403, f"Виза этой ступени — роль {by.value}")
    if not body.approved and not body.comment:
        raise HTTPException(422, "Отказ требует комментария")
    inp, res = run_calc(db, deal)
    flags = approval_flags(res)
    st = DealState(status=deal.status, service_flag=deal.service_flag)
    try:
        tr = visa_decision(st, by, body.approved, flags, date.today(), sla(db), _ladder(db), cd_insists=body.cd_insists)
    except Exception as e:
        raise HTTPException(409, str(e))
    db.add(Approval(deal_id=deal.id, step="financial" if by == Role.FD else "general", role=by.value, user_id=user.id,
                    approved=body.approved, comment=body.comment, prices_snapshot={v.id: v.price_rub for v in deal.variants}))
    complete_tasks(db, deal, block="visa")
    apply_transition(db, deal, tr, user.id)
    if tr.status == DealStatus.APPROVED:
        _finalize_approval(db, deal, user.id)
    elif tr.new_version:
        create_version(db, deal, "rework", user.id)
    db.commit()
    return {"status": deal.status.value}


class ReworkIn(BaseModel):
    block: str
    reason: str


@router.post("/rework")
def rework(deal_id: str, body: ReworkIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD)):
    deal = get_deal_or_404(db, deal_id, user)
    try:
        tr = wf_rework(DealState(status=deal.status, service_flag=deal.service_flag), body.block, body.reason, date.today(), sla(db))
    except Exception as e:
        raise HTTPException(409, str(e))
    if body.block == "logistics":
        deal.logistics_done = False
    if body.block == "service":
        deal.service_done = False
    for a in db.scalars(select(Approval).where(Approval.deal_id == deal.id, Approval.reset_at.is_(None))).all():
        a.reset_at = datetime.now(timezone.utc)
    create_version(db, deal, "rework", user.id)
    apply_transition(db, deal, tr, user.id)
    db.commit()
    return {"status": deal.status.value, "version_no": deal.current_version_no}


def _finalize_approval(db: Session, deal: Deal, user_id: str) -> DealVersion:
    """Утверждение: версия расчёта + Excel + КП в OneDrive, черновик письма менеджеру."""
    ver = create_version(db, deal, "approval", user_id)
    deal.approved_version_id = ver.id
    try:
        kp_path = _build_kp_file(db, deal, ver)
        ensure_folders(db, deal)
        stored = adapters.storage().upload(kp_path, deal.storage_folder, "05_kp", os.path.basename(kp_path))
        ver.kp_path = stored.path
        db.add(Attachment(deal_id=deal.id, folder="05_kp", entity="version", entity_id=ver.id, file_name=os.path.basename(kp_path),
                          storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user_id))
        from ..adapters.mail import OutgoingMail
        adapters.mail().create_draft(OutgoingMail(to=[c.get("email", "") for c in (deal.client.contacts or [])], subject=f"Коммерческое предложение {deal.number}",
                                                  body_html="<p>Добрый день! Направляем коммерческое предложение.</p>", attachments=[kp_path]))
    except Exception as e:
        audit(db, user_id, deal.id, "kp", ver.id, "kp_failed", comment=str(e)[:500])
    return ver


def _build_kp_file(db: Session, deal: Deal, ver: DealVersion) -> str:
    s = settings_service.get_all(db)
    res = ver.calc_result
    variants = []
    for v in deal.variants:
        if not v.in_kp or v.price_rub is None:
            continue
        poss = [p for p in deal.positions if p.id in v.position_ids]
        qty = sum(p.qty for p in poss)
        base = res["variants"][v.id]["base"]
        pay = v.client_payments or deal.client_payments or []
        variants.append(KpVariant(
            name=v.name, model_factory="; ".join(f"{p.model} / {p.factory_id}" for p in poss), qty=qty,
            configuration="; ".join(o.get("name", "") for p in poss for o in (p.options or [])) or "базовая",
            basis=poss[0].basis if poss else "", delivery_days=base["delivery_days"],
            unit_price_rub=v.price_rub / max(qty, 1), total_rub=v.price_rub, vat_pct=20.0,
            payment_terms="; ".join(f"{l.get('share_pct')} % — {l.get('event')}" for l in pay),
            warranty="; ".join(f"{p.warranty_months or '—'} мес. / {p.warranty_hours or '—'} м·ч" for p in poss),
            service_line=("включено в цену" if v.service_in_kp else None) if deal.service_flag else None,
        ))
    tech = {}
    for p in deal.positions:
        rows = [(f.get("name", ""), f.get("value", ""), f.get("unit", "")) for code, f in sorted((p.tech_params or {}).items())
                if not code.startswith("_") and 1 <= int(code.split(".")[0]) <= 11 and f.get("value") not in (None, "")]
        tech[f"{p.model} ({p.machine_type})"] = rows
    delivery_terms = [(f.get("name", ""), str(f.get("value", ""))) for p in deal.positions for code, f in (p.logistics_params or {}).items()
                      if code.startswith("14.") and f.get("value") not in (None, "")]
    service_block = None
    if deal.service_flag and any(v.service_in_kp for v in deal.variants):
        sc = next((p.service_calc for p in deal.positions if (p.service_calc or {}).get("result")), None)
        if sc:
            service_block = {"regulations": list(sc["result"]["executions"]), "spare_parts": f"{len(sc.get('spare_parts', []))} позиций по листу ЗИП",
                             "visits": f"{sum(r['visits'] for r in sc['result']['rows'])} выездов", "price": "включено в цену"}
    today = date.today()
    from datetime import timedelta
    data = KpData(
        kp_no=f"КП-{deal.number[2:]}", kp_date=today.strftime("%d.%m.%Y"), valid_until=(today + timedelta(days=s["kp_validity_days"])).strftime("%d.%m.%Y"),
        client=deal.client.name, site=deal.site, manager=deal.manager_id, manager_contact="", intro_text=s.get("kp_intro_text") or
        "Уважаемые коллеги! Благодарим за запрос и направляем коммерческое предложение на поставку горношахтного оборудования.",
        variants=variants, tech_specs=tech, service_block=service_block, delivery_terms=delivery_terms,
        payment_and_warranty="Условия оплаты и гарантии — согласно таблице вариантов.", fx_clause_pct=s["kp_fx_clause_pct"],
        signatures="Коммерческий директор ________________", requisites=s.get("company_requisites", ""),
    )
    tmpdir = tempfile.mkdtemp()
    return build_kp(data, os.path.join(tmpdir, f"{deal.number}_КП_v{ver.version_no}.docx"))
