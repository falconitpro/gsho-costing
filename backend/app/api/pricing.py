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
from ..calc.types import EVENT_TITLES, PaymentEvent
from ..core.auth import CurrentUser, get_current_user, require_roles
from ..core.roles import Role, filter_payload
from ..core.workflow import (
    ApprovalStep, DealState, DealStatus, cd_checks, cd_submit_price, price_changed_after_visa, rework as wf_rework, visa_decision,
)
from ..db import get_db
from ..models import Approval, Attachment, ClientPriceHistory, Deal, DealVersion, Factory, FactoryPriceHistory, FeedEntry, Position, Variant
from ..services import settings_service
from ..services.deal_calc import approval_flags, build_calc_input, run_calc
from ..services.deal_service import apply_transition, audit, complete_tasks, create_version, ensure_folders, sla
from ..services.kp_generator import KpData, KpVariant, build_kp
from ..services.offers_service import kp_brand_check, kp_model_name
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
    # история цен клиентам (задачи 7, 8): по отправленным версиям КП; видят только КД/ГД (серверная фильтрация ключа)
    client_kp_history = []
    for h in db.scalars(select(ClientPriceHistory).where(ClientPriceHistory.client_id == deal.client_id, ClientPriceHistory.deal_id != deal.id)
                        .order_by(ClientPriceHistory.on.desc()).limit(30)).all():
        client_kp_history.append({"deal": h.deal_number, "variant": h.variant_name, "version_no": h.version_no, "price_rub": h.total_rub,
                                  "unit_price_rub": h.unit_price_rub, "status": h.outcome or "kp_sent", "models": [h.model], "on": h.on.isoformat()})
    warn_lower = []
    for v in deal.variants:
        models = {p.model for p in deal.positions if p.id in v.position_ids}
        for h in client_kp_history:
            if h["price_rub"] and v.price_rub and set(h["models"]) & models and v.price_rub < h["price_rub"]:
                warn_lower.append(f"Предложение «{v.name}»: цена ниже предыдущего КП этому клиенту ({h['deal']}, {h['price_rub']:,.0f} RUB)")
    base_any = next(iter(res.variants.values()), {}).get("base") if res.variants else None
    if base_any and base_any.defaults_used.get("client_payments"):
        warn_lower.append("Условия оплаты клиента не заданы — расчёт по типовой схеме (30 % после подписания, 70 % аккредитивом после CMR).")
    if base_any and base_any.defaults_used.get("factory_payments"):
        warn_lower.append("График оплаты заводу не задан КД — расчёт по типовой схеме (30 % в течение 14 раб. дней после контракта, 70 % после готовности).")
    factories = {f.id: f.name for f in db.scalars(select(Factory)).all()}
    warn_lower += kp_brand_check(deal, factories)
    comments = []
    for p in deal.positions:
        for k, c in ((p.logistics or {}).get("checks") or {}).items():
            if c.get("comment"):
                comments.append({"from": "логист", "item": k, "ref": p.model, "comment": c["comment"]})
        if (p.price_reference or {}).get("comment"):
            comments.append({"from": "менеджер", "item": "цена завода", "ref": p.model, "comment": p.price_reference["comment"]})
    checks = cd_checks(_cd_deal_dict(deal))
    terms = {}
    for vid, scen in res.variants.items():
        terms[vid] = [{"position_id": pc.position_id, "model": next((p.model for p in deal.positions if p.id == pc.position_id), ""),
                       "delivery_term": pc.delivery_term.model_dump(mode="json") if pc.delivery_term else None,
                       "events": {k: v.isoformat() for k, v in pc.events.items()}} for pc in scen["base"].positions]
    data = {
        "input": inp.model_dump(mode="json"), "result": res.model_dump(mode="json"),
        "approval_flags": approval_flags(res), "threshold_pct": inp.thresholds.margin_threshold_pct,
        "factory_price_history": [serialize(h) for h in price_hist], "client_kp_history": client_kp_history,
        "warnings": res.warnings + warn_lower, "comments": comments, "cd_checks": checks, "terms": terms,
        "status": deal.status.value, "scenario_override": deal.scenario_override, "kp_brand": deal.kp_brand, "kp_images_mode": deal.kp_images_mode,
        "payment_terms": {"client_payments": deal.client_payments, "client_payment_wishes": deal.client_payment_wishes, "bank_guarantee": deal.bank_guarantee,
                          "default_client": settings_service.get(db, "default_client_payment_scheme"),
                          "default_factory": settings_service.get(db, "default_factory_payment_scheme"),
                          "advance_guarantee_default": settings_service.get(db, "advance_guarantee_cost"),
                          "positions": [{"id": p.id, "model": p.model, "factory_payments": p.factory_payments, "factory_payments_offer": p.factory_payments_offer,
                                         "delivery_term": p.delivery_term, "production_term": p.production_term} for p in deal.positions]},
        "events": {e.value: t for e, t in EVENT_TITLES.items()},
    }
    return filter_payload(data, user)


def _cd_deal_dict(deal: Deal) -> dict:
    return {"payment_terms_set": bool(deal.client_payments) or (bool(deal.variants) and all(v.client_payments for v in deal.variants if not v.is_default)),
            "positions": [{"model": p.model, "factory_payments": p.factory_payments,
                           "local_options": [{"name": o.name, "cost": o.cost, "supplier_lead_days": o.supplier_lead_days} for o in (p.local_option_rows or [])]}
                          for p in deal.positions]}


class PaymentTermsIn(BaseModel):
    """Задача 1: условия оплаты задаёт КД (ГД — те же права). Пустые списки = вернуться к типовой схеме."""
    client_payments: Optional[list[dict]] = None                 # уровень сделки (без альтернатив)
    variant_payments: Optional[dict[str, list[dict]]] = None     # по предложениям (с альтернативами)
    factory_payments: Optional[dict[str, list[dict]]] = None     # position_id → график заводу
    bank_guarantee: Optional[dict] = None                        # {enabled, mode: one_time_pct|annual_pct|fixed, value}
    delivery_terms: Optional[dict[str, dict]] = None             # position_id → {unit, start_event} (задача 3, правка КД)
    accept_wishes: bool = False                                  # отметка «пожелания клиента приняты»


@router.post("/payment-terms")
def payment_terms(deal_id: str, body: PaymentTermsIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    if not user.can("payment_terms", 2):
        raise HTTPException(403, "Условия оплаты задаёт коммерческий директор")
    before = {"client_payments": deal.client_payments, "bank_guarantee": deal.bank_guarantee}
    if body.client_payments is not None:
        deal.client_payments = body.client_payments
    if body.variant_payments:
        for v in deal.variants:
            if v.id in body.variant_payments:
                v.client_payments = body.variant_payments[v.id]
    if body.factory_payments:
        for p in deal.positions:
            if p.id in body.factory_payments:
                p.factory_payments = body.factory_payments[p.id]
    if body.bank_guarantee is not None:
        deal.bank_guarantee = body.bank_guarantee
    changed_terms = False
    if body.delivery_terms:
        for p in deal.positions:
            if p.id in body.delivery_terms:
                if (p.delivery_term or {}) != body.delivery_terms[p.id]:
                    changed_terms = True
                p.delivery_term = body.delivery_terms[p.id]
    if body.accept_wishes:
        db.add(FeedEntry(deal_id=deal.id, kind="system", subject="Пожелания клиента по оплате приняты КД", body=deal.client_payment_wishes or "", user_id=user.id))
    # изменение условий после визы сбрасывает визы (как цена)
    reset = price_changed_after_visa(DealState(status=deal.status, service_flag=deal.service_flag))
    if reset and (body.client_payments is not None or body.variant_payments or body.factory_payments or changed_terms):
        for a in db.scalars(select(Approval).where(Approval.deal_id == deal.id, Approval.reset_at.is_(None))).all():
            a.reset_at = datetime.now(timezone.utc)
        apply_transition(db, deal, reset, user.id)
    audit(db, user.id, deal.id, "deal", deal.id, "payment_terms", before, body.model_dump(exclude_none=True))
    db.commit()
    inp, res = run_calc(db, deal)
    return filter_payload({"ok": True, "result": res.model_dump(mode="json"), "cd_checks": cd_checks(_cd_deal_dict(deal)), "status": deal.status.value}, user)


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
        raise HTTPException(422, "Задайте цену по каждому предложению")
    errors = cd_checks(_cd_deal_dict(deal))
    if errors:
        raise HTTPException(422, {"message": "Перед утверждением задайте условия оплаты и оцените опции РФ", "errors": errors})
    inp, res = run_calc(db, deal)
    flags = approval_flags(res)
    st = DealState(status=deal.status, service_flag=deal.service_flag, in_execution=deal.in_execution)
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
    elif tr.status == DealStatus.CONTRACT:
        _finalize_contract_change(db, deal, user.id)
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
    st = DealState(status=deal.status, service_flag=deal.service_flag, in_execution=deal.in_execution)
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
    elif tr.status == DealStatus.CONTRACT:
        _finalize_contract_change(db, deal, user.id)
    elif tr.new_version:
        create_version(db, deal, "rework", user.id)
    db.commit()
    return {"status": deal.status.value}


def _finalize_contract_change(db: Session, deal: Deal, user_id: str) -> None:
    """Задача 2: изменённые условия контракта утверждены — новая версия расчёта, этапы исполнения пересобираются."""
    ver = create_version(db, deal, "contract_change", user_id)
    deal.approved_version_id = ver.id
    from ..services.execution_service import rebuild_stages
    rebuild_stages(db, deal)


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
    factories = {f.id: f for f in db.scalars(select(Factory)).all()}
    company = deal.kp_brand == "company"
    single = not deal.has_alternatives
    variants = []
    for v in deal.variants:
        if not v.in_kp or v.price_rub is None:
            continue
        poss = [p for p in deal.positions if p.id in v.position_ids]
        qty = sum(p.qty for p in poss)
        base = res["variants"][v.id]["base"]
        pay = v.client_payments or deal.client_payments or s.get("default_client_payment_scheme") or []
        terms = []
        for pc in base.get("positions", []):
            dt = pc.get("delivery_term") or {}
            p = next((x for x in poss if x.id == pc["position_id"]), None)
            if p and dt.get("text"):
                terms.append(f"{_kp_model(p, company, db, factories)}: {dt['text']}")
        local_opts = [o.name for p in poss for o in (p.local_option_rows or [])]
        variants.append(KpVariant(
            name="Предложение" if single else v.name,
            model_factory="; ".join(_kp_model(p, company, db, factories) + ("" if company else f" / {factories[p.factory_id].name if p.factory_id in factories else ''}") for p in poss),
            qty=qty, configuration=("; ".join(o.get("name", "") for p in poss for o in (p.options or [])) or "базовая") + (f"; доукомплектование в РФ: {', '.join(local_opts)}" if local_opts else ""),
            basis=poss[0].basis if poss else "", delivery_days=base["delivery_days"], delivery_term_text="; ".join(terms) if terms else f"{base['delivery_days']} дн.",
            unit_price_rub=v.price_rub / max(qty, 1), total_rub=v.price_rub, vat_pct=20.0,
            payment_terms="; ".join(_pay_text(l) for l in pay),
            warranty="; ".join(f"{p.warranty_months or '—'} мес. / {p.warranty_hours or '—'} м·ч" for p in poss),
            service_line=("включено в цену" if v.service_in_kp else None) if deal.service_flag else None,
        ))
    tech: dict[str, list[tuple[str, str, str]]] = {}
    images: dict[str, list[tuple[str, str]]] = {}
    attach_files: list[str] = []
    st = adapters.storage()
    for p in deal.positions:
        title = _kp_model(p, company, db, factories) + (f" ({p.machine_type})" if p.machine_type else "")
        rows = [(r.name_ru, r.value_ru or "", r.unit or "") for r in sorted(p.tech_param_rows or [], key=lambda r: r.no) if (r.value_ru or "").strip()]
        if not rows:   # совместимость: опросник RFQ по кодам
            rows = [(f.get("name", ""), f.get("value", ""), f.get("unit", "")) for code, f in sorted((p.tech_params or {}).items())
                    if not code.startswith("_") and 1 <= int(code.split(".")[0]) <= 11 and f.get("value") not in (None, "")]
        tech[title] = rows
        # задача 10: изображения, отмеченные КД/ГД «В КП»; способ вложения — документ / письмо / оба
        for img in sorted((i for i in (p.image_rows or []) if i.in_kp), key=lambda i: i.kp_order):
            att = db.get(Attachment, img.attachment_id)
            if not att:
                continue
            try:
                local = st.download(att.storage_path, os.path.join(tempfile.mkdtemp(), att.file_name))
            except Exception:
                continue
            if deal.kp_images_mode in ("document", "both"):
                images.setdefault(title, []).append((local, img.caption))
            if deal.kp_images_mode in ("attachment", "both"):
                attach_files.append(local)
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
    forbidden = [factories[p.factory_id].name for p in deal.positions if p.factory_id in factories] + \
                [factories[p.factory_id].name_zh for p in deal.positions if p.factory_id in factories and factories[p.factory_id].name_zh] if company else []
    data = KpData(
        kp_no=f"КП-{deal.number[2:]}", kp_date=today.strftime("%d.%m.%Y"), valid_until=(today + timedelta(days=s["kp_validity_days"])).strftime("%d.%m.%Y"),
        client=deal.client.name, site=deal.site, manager=deal.manager_id, manager_contact="", intro_text=s.get("kp_intro_text") or
        "Уважаемые коллеги! Благодарим за запрос и направляем коммерческое предложение на поставку горношахтного оборудования.",
        variants=variants, tech_specs=tech, service_block=service_block, delivery_terms=delivery_terms,
        payment_and_warranty="Условия оплаты и гарантии — согласно таблице предложения.", fx_clause_pct=s["kp_fx_clause_pct"],
        signatures="Коммерческий директор ________________", requisites=s.get("company_requisites", ""),
        single_offer=single, images=images, brand_company=company, forbidden_words=[w for w in forbidden if w],
    )
    ver.kp_meta = {"kp_brand": deal.kp_brand, "kp_images_mode": deal.kp_images_mode, "images_in_document": sum(len(v) for v in images.values()),
                   "attachments": [os.path.basename(f) for f in attach_files]}
    tmpdir = tempfile.mkdtemp()
    return build_kp(data, os.path.join(tmpdir, f"{deal.number}_КП_v{ver.version_no}.docx"))


def _kp_model(p: Position, company: bool, db: Session, factories: dict) -> str:
    if company:
        return kp_model_name(p, db) or p.model
    return p.model


def _pay_text(l: dict) -> str:
    ev = l.get("event")
    try:
        title = EVENT_TITLES[PaymentEvent(ev)]
    except Exception:
        title = str(ev)
    share = f"{l.get('share_pct')} %" if l.get("share_pct") is not None else f"{l.get('amount')}"
    off = l.get("offset_days") or 0
    kind = "раб. дн." if l.get("offset_kind") == "working" else "дн."
    via = " (аккредитив)" if l.get("via") == "lc" else ""
    return f"{share} — {title}" + (f" + {off} {kind}" if off else "") + via
