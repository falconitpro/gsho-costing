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
    ApprovalStep, DealState, DealStatus, cd_checks, cd_insists_after_fd, cd_submit_price, price_changed_after_visa, rework as wf_rework, visa_decision,
)
from ..db import get_db
from ..models import Approval, Attachment, ClientPriceHistory, Deal, DealVersion, Factory, FactoryPriceHistory, FeedEntry, Position, User, Variant
from ..services import settings_service
from ..services.deal_calc import approval_flags, build_calc_input, run_calc
from ..services.deal_service import apply_transition, audit, complete_tasks, create_version, ensure_folders, sla
from ..services.kp_generator import KpData, KpVariant, build_kp
from ..services.offers_service import kp_brand_check, kp_model_name
from .common import get_deal_or_404, payment_terms_set, serialize

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
    # история цен клиентам (задачи 7, 8): по отправленным версиям КП; видят только КД/ЗГД (серверная фильтрация ключа)
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
    warn_lower += kp_brand_check(deal, factories, db)
    comments = []
    for p in deal.positions:
        for k, c in ((p.logistics or {}).get("checks") or {}).items():
            if c.get("comment"):
                comments.append({"from": "логист", "item": k, "ref": p.designation, "comment": c["comment"]})
        if (p.price_reference or {}).get("comment"):
            comments.append({"from": "менеджер", "item": "цена завода", "ref": p.designation, "comment": p.price_reference["comment"]})
    checks = cd_checks(_cd_deal_dict(deal))
    terms = {}
    for vid, scen in res.variants.items():
        terms[vid] = [{"position_id": pc.position_id, "model": next((p.designation for p in deal.positions if p.id == pc.position_id), ""),
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
                          "positions": [{"id": p.id, "model": p.designation, "factory_payments": p.factory_payments, "factory_payments_offer": p.factory_payments_offer,
                                         "delivery_term": p.delivery_term, "production_term": p.production_term} for p in deal.positions]},
        "events": {e.value: t for e, t in EVENT_TITLES.items()},
        "fd_declined": _fd_decline(db, deal),
    }
    return filter_payload(data, user)


def _cd_deal_dict(deal: Deal) -> dict:
    return {"payment_terms_set": payment_terms_set(deal),
            "positions": [{"model": p.designation, "factory_payments": p.factory_payments,
                           "local_options": [{"name": o.name, "cost": o.cost, "supplier_lead_days": o.supplier_lead_days} for o in (p.local_option_rows or [])]}
                          for p in deal.positions]}


class PaymentTermsIn(BaseModel):
    """Задача 1: условия оплаты задаёт КД (ЗГД — те же права). Пустые списки = вернуться к типовой схеме."""
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
    currency: Optional[str] = None  # валюта цены клиенту; по умолчанию — валюта предложения завода (или ранее выбранная)
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
        raise HTTPException(422, "Переопределение накладных требует комментария (и визы ЗГД)")
    before = serialize(v)
    if body.service_in_kp is not None:
        v.service_in_kp = body.service_in_kp
    if body.client_payments is not None:
        v.client_payments = body.client_payments
    v.overhead_pct_override, v.overhead_override_comment = body.overhead_pct_override, body.overhead_override_comment
    db.flush()
    inp = build_calc_input(db, deal)
    cur = (body.currency or v.price_currency or offer_currency(deal, v)).upper()
    rate = 1.0 if cur == "RUB" else (inp.fx.rates or {}).get(cur)
    if not rate:
        raise HTTPException(422, f"Нет курса ЦБ для валюты {cur} на дату расчёта")
    if body.mode == "amount":
        price = body.value * rate
    elif body.mode == "markup":
        price = solve_price(inp, v.id, target_markup_pct=body.value)
    elif body.mode == "margin":
        price = solve_price(inp, v.id, target_margin_pct=body.value)
    else:
        raise HTTPException(422, "Способ задания цены: сумма, наценка или маржа")
    v.price_rub, v.price_mode = round(price, 2), body.mode
    v.price_currency, v.price_fx_rate = cur, rate
    v.price_amount = round(body.value, 2) if body.mode == "amount" else round(price / rate, 2)
    reset = price_changed_after_visa(DealState(status=deal.status, service_flag=deal.service_flag))
    if reset and before.get("price_rub") != v.price_rub:
        for a in db.scalars(select(Approval).where(Approval.deal_id == deal.id, Approval.reset_at.is_(None))).all():
            a.reset_at = datetime.now(timezone.utc)
        apply_transition(db, deal, reset, user.id)
    audit(db, user.id, deal.id, "variant", v.id, "price", {"price_rub": before.get("price_rub")}, {"price_rub": v.price_rub, "mode": body.mode, "currency": cur, "amount": v.price_amount, "rate": rate})
    db.commit()
    inp, res = run_calc(db, deal)
    return {"variant": serialize(v), "result": res.variants[v.id], "approval_flags": approval_flags(res), "warnings": res.warnings}


def offer_currency(deal: Deal, v: Optional[Variant] = None) -> str:
    """Валюта предложения завода: самая частая валюта цен позиций предложения (иначе — базовая валюта сделки)."""
    poss = [p for p in deal.positions if v is None or not v.position_ids or p.id in v.position_ids]
    curs = [(p.currency or "").upper() for p in poss if p.currency]
    return max(set(curs), key=curs.count) if curs else (deal.base_currency or "CNY").upper()


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
    """КД задал цену по каждому варианту → коммерческая виза; далее ФД/ЗГД по условиям или утверждение."""
    deal = get_deal_or_404(db, deal_id, user)
    if any(v.price_rub is None for v in deal.variants if v.in_kp):
        raise HTTPException(422, "Задайте цену по каждому предложению")
    errors = cd_checks(_cd_deal_dict(deal))
    if errors:
        raise HTTPException(422, {"message": "Перед утверждением задайте условия оплаты и оцените опции РФ", "errors": errors})
    inp, res = run_calc(db, deal)
    flags = approval_flags(res)
    st = DealState(status=deal.status, service_flag=deal.service_flag, in_execution=deal.in_execution, execution_started=deal.execution_started)
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
    elif tr.status in (DealStatus.CONTRACT, DealStatus.EXECUTION):
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
        raise HTTPException(403, f"Виза этой ступени — {'ЗГД' if by == Role.GD else 'ФД'}")
    if not body.approved and not body.comment:
        raise HTTPException(422, "Отказ требует комментария")
    inp, res = run_calc(db, deal)
    flags = approval_flags(res)
    st = DealState(status=deal.status, service_flag=deal.service_flag, in_execution=deal.in_execution, execution_started=deal.execution_started)
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
    elif tr.status in (DealStatus.CONTRACT, DealStatus.EXECUTION):
        _finalize_contract_change(db, deal, user.id)
    elif tr.new_version:
        create_version(db, deal, "rework", user.id)
    db.commit()
    return {"status": deal.status.value}


def _fd_decline(db: Session, deal: Deal) -> Optional[dict]:
    """Последний отказ ФД по текущим ценам (сделка на доработке у КД): КД может настоять и передать на визу ЗГД."""
    if deal.status != DealStatus.REWORK:
        return None
    last = db.scalars(select(Approval).where(Approval.deal_id == deal.id, Approval.reset_at.is_(None)).order_by(Approval.at.desc())).first()
    if last is None or last.step != "financial" or last.approved:
        return None
    if any(abs((last.prices_snapshot or {}).get(v.id) or 0) != abs(v.price_rub or 0) for v in deal.variants if v.in_kp):
        return None   # цена изменена после отказа — нужна обычная отправка на визу
    u = db.get(User, last.user_id)
    return {"comment": last.comment, "at": last.at.isoformat() if last.at else None, "user": u.display_name if u else ""}


class InsistIn(BaseModel):
    comment: str


@router.post("/insist")
def insist(deal_id: str, body: InsistIn, db: Session = Depends(get_db), user: CurrentUser = Depends(CD)):
    """ФД отказал, КД настаивает: сделка уходит на визу ЗГД с той же ценой (ступень ЗГД, условие fd_declined)."""
    deal = get_deal_or_404(db, deal_id, user)
    if not body.comment.strip():
        raise HTTPException(422, "Укажите, почему КД настаивает на цене")
    if _fd_decline(db, deal) is None:
        raise HTTPException(409, "Настоять можно только после отказа ФД, пока цена не менялась")
    st = DealState(status=deal.status, service_flag=deal.service_flag, in_execution=deal.in_execution, execution_started=deal.execution_started)
    try:
        tr = cd_insists_after_fd(st, date.today(), sla(db), _ladder(db))
    except Exception as e:
        raise HTTPException(409, str(e))
    db.add(Approval(deal_id=deal.id, step="commercial", role="cd", user_id=user.id, approved=True, comment=f"КД настаивает: {body.comment}",
                    prices_snapshot={v.id: v.price_rub for v in deal.variants}))
    complete_tasks(db, deal, block="pricing")
    apply_transition(db, deal, tr, user.id)
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
def rework(deal_id: str, body: ReworkIn, db: Session = Depends(get_db), user: CurrentUser = Depends(require_roles(Role.CD, Role.GD, Role.FD))):
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


def revision_totals(deal: Deal, ver: DealVersion, changes: dict) -> dict:
    """Итоги редакции КП по предложениям: стоимость оборудования, услуги, скидки / надбавки, итог и маржа (ориентир)."""
    res = ver.calc_result or {}
    out = {}
    for v in deal.variants:
        if not v.in_kp or v.price_rub is None:
            continue
        ch = (changes or {}).get(v.id) or {}
        base = float(v.price_rub)
        extras = [(str(e.get("name") or "").strip(), (float(e["price_rub"]) if e.get("price_rub") not in (None, "") else None))
                  for e in ch.get("extras") or [] if str(e.get("name") or "").strip()]
        subtotal = base + sum(a for _, a in extras if a)
        adj = []
        for a in ch.get("adjustments") or []:
            try:
                val = float(a.get("value") or 0)
            except (TypeError, ValueError):
                continue
            if not val:
                continue
            amount = subtotal * val / 100.0 if a.get("mode") == "pct" else val
            sign = -1 if a.get("kind") == "discount" else 1
            label = a.get("label") or (("Скидка" if sign < 0 else "Надбавка") + (f" {val:g} %" if a.get("mode") == "pct" else ""))
            adj.append((label, round(sign * amount, 2)))
        total = round(subtotal + sum(x for _, x in adj), 2)
        cost = ((res.get("variants") or {}).get(v.id) or {}).get("base", {}).get("cost_total_rub")
        out[v.id] = {"name": v.name, "base": base, "extras": extras, "adjustments": adj, "total": total,
                     "cost": cost, "margin_pct": round((total - cost) / total * 100, 2) if cost and total else None,
                     "configuration": (ch.get("configuration") or "").strip(), "service_in_kp": ch.get("service_in_kp")}
    return out


def _kp_currency(deal: Deal) -> Optional[str]:
    """Валюта КП: если у всех предложений в КП цена назначена в одной валюте, не RUB."""
    curs = {(v.price_currency or "RUB") for v in deal.variants if v.in_kp and v.price_rub is not None}
    return next(iter(curs)) if len(curs) == 1 and next(iter(curs)) != "RUB" else None


def _build_kp_file(db: Session, deal: Deal, ver: DealVersion, changes: Optional[dict] = None, revision_no: int = 0) -> str:
    s = settings_service.get_all(db)
    rev = revision_totals(deal, ver, changes) if changes is not None else {}
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
            unit_price_rub=v.price_rub / max(qty, 1), total_rub=v.price_rub, vat_pct=22.0,
            payment_terms="; ".join(_pay_text(l) for l in pay),
            warranty="; ".join(f"{p.warranty_months or '—'} мес. / {p.warranty_hours or '—'} м·ч" for p in poss),
            service_line=("включено в цену" if v.service_in_kp else None) if deal.service_flag else None,
        ))
        r = rev.get(v.id)
        if r:   # редакция КП: услуги, скидки / надбавки, комплектация
            kv = variants[-1]
            kv.base_total_rub, kv.extra_lines, kv.adjustments, kv.total_rub = r["base"], r["extras"], r["adjustments"], r["total"]
            if r["configuration"]:
                kv.configuration = r["configuration"]
            if r["service_in_kp"] is not None and deal.service_flag:
                kv.service_line = "включено в цену" if r["service_in_kp"] else None
    kp_currency = _kp_currency(deal)
    if kp_currency:   # цена клиенту назначена в валюте — КП в этой валюте (по курсу на момент назначения цены)
        for v, kv in zip([x for x in deal.variants if x.in_kp and x.price_rub is not None], variants):
            k = float(v.price_fx_rate or 1.0)
            kv.unit_price_rub, kv.total_rub = kv.unit_price_rub / k, kv.total_rub / k
            if kv.base_total_rub is not None:
                kv.base_total_rub = kv.base_total_rub / k
            kv.extra_lines = [(n, a / k if a else a) for n, a in kv.extra_lines]
            kv.adjustments = [(n, a / k) for n, a in kv.adjustments]
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
        # задача 10: изображения, отмеченные КД/ЗГД «В КП»; способ вложения — документ / письмо / оба
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
        kp_no=f"КП-{deal.number[2:]}" + (f"/{revision_no}" if revision_no else ""), kp_date=today.strftime("%d.%m.%Y"), valid_until=(today + timedelta(days=s["kp_validity_days"])).strftime("%d.%m.%Y"),
        client=deal.client.name, site=deal.site, manager=deal.manager_id, manager_contact="", intro_text=s.get("kp_intro_text") or
        "Уважаемые коллеги! Благодарим за запрос и направляем коммерческое предложение на поставку горношахтного оборудования.",
        variants=variants, tech_specs=tech, service_block=service_block, delivery_terms=delivery_terms,
        payment_and_warranty="Условия оплаты и гарантии — согласно таблице предложения.", fx_clause_pct=s["kp_fx_clause_pct"],
        signatures="Коммерческий директор ________________", requisites=s.get("company_requisites", ""),
        single_offer=single, images=images, brand_company=company, forbidden_words=[w for w in forbidden if w], currency=kp_currency or "RUB",
    )
    if not revision_no:
        ver.kp_meta = {"kp_brand": deal.kp_brand, "kp_images_mode": deal.kp_images_mode, "images_in_document": sum(len(v) for v in images.values()),
                       "attachments": [os.path.basename(f) for f in attach_files]}
    tmpdir = tempfile.mkdtemp()
    return build_kp(data, os.path.join(tmpdir, f"{deal.number}_КП_v{ver.version_no}" + (f"_ред{revision_no}" if revision_no else "") + ".docx"))


def _kp_model(p: Position, company: bool, db: Session, factories: dict) -> str:
    if company:
        return kp_model_name(p, db) or p.model
    return p.designation


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


# ------------------------------------------------------------------ КП: пересобрать, выгрузить Word / PDF, отправить клиенту

def _kp_access(deal: Deal, user: CurrentUser) -> None:
    if not (user.has(Role.CD, Role.GD, Role.ADMIN) or (user.has(Role.MANAGER) and deal.manager_id == user.id)):
        raise HTTPException(403, "КП формируют и отправляют менеджер сделки, КД или ЗГД")


def _approved_version(db: Session, deal: Deal) -> DealVersion:
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    if ver is None or deal.status not in (DealStatus.APPROVED, DealStatus.KP_SENT, DealStatus.CONTRACT, DealStatus.EXECUTION):
        raise HTTPException(409, "КП формируется по утверждённой версии расчёта — сначала утвердите расчёт")
    return ver


def _kp_local(db: Session, deal: Deal, ver: DealVersion, rebuild: bool = False) -> str:
    """Локальный путь к файлу КП утверждённой версии; при отсутствии или rebuild=True — собирается заново и сохраняется в «05 КП»."""
    if ver.kp_path and not rebuild:
        local = os.path.join(tempfile.mkdtemp(), os.path.basename(ver.kp_path))
        try:
            return adapters.storage().download(ver.kp_path, local)
        except Exception:
            pass
    path = _build_kp_file(db, deal, ver)
    ensure_folders(db, deal)
    stored = adapters.storage().upload(path, deal.storage_folder, "05_kp", os.path.basename(path))
    ver.kp_path = stored.path
    ver.kp_meta = {k: v for k, v in (ver.kp_meta or {}).items() if k not in ("pdf_path", "pdf_for")}   # PDF пересоберётся
    db.add(Attachment(deal_id=deal.id, folder="05_kp", entity="version", entity_id=ver.id, file_name=os.path.basename(path),
                      storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=None))
    return path


def _to_pdf(docx_path: str) -> str:
    """Word → PDF через LibreOffice (установлен в образе бэкенда)."""
    import shutil
    import subprocess
    out = tempfile.mkdtemp()
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise HTTPException(503, "Конвертер в PDF недоступен на сервере (LibreOffice)")
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", out, docx_path], check=True, timeout=120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "HOME": out})
    pdf = os.path.join(out, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    if not os.path.isfile(pdf):
        raise HTTPException(500, "Не удалось преобразовать КП в PDF")
    return pdf


def _kp_pdf(db: Session, deal: Deal, ver: DealVersion, docx_path: str) -> str:
    """PDF версии КП: собирается один раз (LibreOffice ~30 с) и хранится в «05 КП»; после «Сформировать КП» — заново."""
    meta = dict(ver.kp_meta or {})
    if meta.get("pdf_path") and meta.get("pdf_for") == ver.kp_path:
        local = os.path.join(tempfile.mkdtemp(), os.path.basename(meta["pdf_path"]))
        try:
            return adapters.storage().download(meta["pdf_path"], local)
        except Exception:
            pass
    pdf = _to_pdf(docx_path)
    try:
        stored = adapters.storage().upload(pdf, deal.storage_folder, "05_kp", os.path.basename(pdf))
        ver.kp_meta = {**meta, "pdf_path": stored.path, "pdf_for": ver.kp_path}
        db.commit()
    except Exception:
        pass
    return pdf


@router.post("/kp/build")
def kp_build(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """«Сформировать КП»: пересобрать файл по утверждённой версии (после правки подачи КП, изображений, реквизитов)."""
    deal = get_deal_or_404(db, deal_id, user)
    _kp_access(deal, user)
    ver = _approved_version(db, deal)
    _kp_local(db, deal, ver, rebuild=True)
    audit(db, user.id, deal.id, "kp", ver.id, "kp_rebuilt")
    db.commit()
    return {"version_no": ver.version_no, "kp_path": ver.kp_path}


@router.get("/kp/file")
def kp_file(deal_id: str, format: str = "docx", original: bool = False, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """«Выгрузить КП»: Word (docx) или PDF — актуальная редакция (если КП изменяли) или КП утверждённой версии."""
    from fastapi.responses import FileResponse
    deal = get_deal_or_404(db, deal_id, user)
    ver = _approved_version(db, deal)
    last = None if original else _last_revision(db, deal, ver)
    if last is not None:
        return _revision_file(db, deal, last, format)
    path = _kp_local(db, deal, ver)
    db.commit()
    name = f"{deal.number}_КП_v{ver.version_no}"
    if format == "pdf":
        return FileResponse(_kp_pdf(db, deal, ver, path), filename=name + ".pdf", media_type="application/pdf")
    return FileResponse(path, filename=name + ".docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/kp/recipients")
def kp_recipients(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Получатели КП: контакты клиента с e-mail (отмечены те, что относятся к объекту сделки)."""
    deal = get_deal_or_404(db, deal_id, user)
    out = []
    for c in (deal.client.contacts if deal.client else []) or []:
        if c.get("email"):
            out.append({"name": c.get("name") or "", "position": c.get("position") or "", "email": c["email"], "phone": c.get("phone") or ""})
    return {"recipients": out, "mail_configured": _mail_provider() != "console", "subject": f"Коммерческое предложение {deal.number}",
            "body": "Добрый день!\n\nНаправляем коммерческое предложение во вложении. Будем рады ответить на вопросы.\n\nС уважением,"}


class KpSendIn(BaseModel):
    to: list[str]
    cc: list[str] = []
    subject: str = ""
    body: str = ""
    format: str = "pdf"            # pdf | docx | both


@router.post("/kp/send")
def kp_send(deal_id: str, body: KpSendIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """«Отправить клиенту»: письмо из системы с КП во вложении выбранным получателям и отметка «КП отправлено»."""
    import html
    import re
    from ..adapters.mail import OutgoingMail
    from .deals import mark_kp_sent
    deal = get_deal_or_404(db, deal_id, user)
    _kp_access(deal, user)
    ver = _approved_version(db, deal)
    if _mail_provider() == "console":
        raise HTTPException(409, "Почта не подключена — выгрузите КП и отправьте из своей почты, затем отметьте «КП отправлено вручную»")
    if deal.status != DealStatus.APPROVED:
        raise HTTPException(409, "КП уже отправлено — повторная отправка без смены статуса: выгрузите файл и отправьте вручную")
    to = [a.strip() for a in body.to + body.cc if a and a.strip()]
    bad = [a for a in to if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a)]
    if not body.to or bad:
        raise HTTPException(422, "Укажите получателей с корректными e-mail" + (f": {', '.join(bad)}" if bad else ""))
    last = _last_revision(db, deal, ver)
    docx = (adapters.storage().download(last.kp_path, os.path.join(tempfile.mkdtemp(), os.path.basename(last.kp_path))) if last
            else _kp_local(db, deal, ver))
    pdf = (lambda: _to_pdf(docx)) if last else (lambda: _kp_pdf(db, deal, ver, docx))
    files = [docx] if body.format == "docx" else [pdf()] if body.format == "pdf" else [docx, pdf()]
    subject = body.subject or f"Коммерческое предложение {deal.number}"
    html_body = "".join(f"<p>{html.escape(par).replace(chr(10), '<br>')}</p>" for par in (body.body or "").split("\n\n"))
    try:
        msg_id = adapters.mail().send(OutgoingMail(to=to, subject=subject, body_html=html_body, attachments=files, sender=user.email or ""))
    except Exception as e:
        audit(db, user.id, deal.id, "kp", ver.id, "kp_send_failed", comment=str(e)[:500])
        db.commit()
        raise HTTPException(502, f"Письмо не отправлено: {e}")
    mark_kp_sent(db, deal, user, to, "система", subject=subject, body_text=f"{body.body}\n\nВложение: {', '.join(os.path.basename(f) for f in files)}")
    audit(db, user.id, deal.id, "kp", ver.id, "kp_sent_from_system", after={"to": to, "format": body.format, "message_id": msg_id})
    db.commit()
    return {"status": deal.status.value, "sent_to": to, "message_id": msg_id, "provider": _mail_provider()}


def _mail_provider() -> str:
    from ..core.config import get_settings
    return get_settings().mail_provider


class ReportDateIn(BaseModel):
    report_date: Optional[date] = None   # пусто — сегодняшняя дата


@router.put("/report-date")
def set_report_date(deal_id: str, body: ReportDateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Дата отчёта (расчёта): курсы ЦБ, прогноз и сроки считаются на эту дату. Меняют КД или ЗГД."""
    deal = get_deal_or_404(db, deal_id, user)
    if not user.has(Role.CD, Role.GD):
        raise HTTPException(403, "Дату отчёта меняют КД или ЗГД")
    before = deal.report_date
    deal.report_date = body.report_date
    audit(db, user.id, deal.id, "deal", deal.id, "report_date", {"report_date": str(before) if before else None},
          {"report_date": str(body.report_date) if body.report_date else None})
    db.commit()
    return {"report_date": deal.report_date.isoformat() if deal.report_date else None}


# ------------------------------------------------------------------ редакции КП (КД / ЗГД): скидки, надбавки, услуги, комплектация

def _last_revision(db: Session, deal: Deal, ver: DealVersion):
    from ..models import KpRevision
    return db.scalar(select(KpRevision).where(KpRevision.deal_id == deal.id, KpRevision.version_id == ver.id).order_by(KpRevision.no.desc()).limit(1))


def _revision_file(db: Session, deal: Deal, rev, format: str):
    from fastapi.responses import FileResponse
    name = f"{deal.number}_КП_ред{rev.no}"
    local = adapters.storage().download(rev.kp_path, os.path.join(tempfile.mkdtemp(), os.path.basename(rev.kp_path)))
    if format != "pdf":
        return FileResponse(local, filename=name + ".docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    if rev.pdf_path:
        try:
            return FileResponse(adapters.storage().download(rev.pdf_path, os.path.join(tempfile.mkdtemp(), os.path.basename(rev.pdf_path))),
                                filename=name + ".pdf", media_type="application/pdf")
        except Exception:
            pass
    pdf = _to_pdf(local)
    try:
        rev.pdf_path = adapters.storage().upload(pdf, deal.storage_folder, "05_kp", os.path.basename(pdf)).path
        db.commit()
    except Exception:
        pass
    return FileResponse(pdf, filename=name + ".pdf", media_type="application/pdf")


def _cd_gd(user: CurrentUser) -> None:
    if not user.has(Role.CD, Role.GD):
        raise HTTPException(403, "Изменяют КП КД или ЗГД")


@router.get("/kp/revisions")
def kp_revisions(deal_id: str, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """История КП: исходное КП утверждённой версии и все редакции — дата, автор, цена, изменения, файлы."""
    from ..models import KpRevision, User
    deal = get_deal_or_404(db, deal_id, user)
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    if ver is None:
        return {"rows": [], "variants": [], "can_edit": False}
    users = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    base_total = sum(float(v.price_rub or 0) for v in deal.variants if v.in_kp and v.price_rub is not None)
    cost = None
    try:
        cost = sum(((ver.calc_result or {}).get("variants") or {}).get(v.id, {}).get("base", {}).get("cost_total_rub") or 0
                   for v in deal.variants if v.in_kp and v.price_rub is not None)
    except Exception:
        pass
    rows = [{"id": None, "no": 0, "at": ver.created_at.isoformat() if ver.created_at else None, "author": users.get(ver.created_by or "", ""),
             "description": f"КП по утверждённой версии расчёта {ver.version_no}", "price_rub": base_total, "price_before_rub": None,
             "margin_pct": round((base_total - cost) / base_total * 100, 2) if cost and base_total else None, "has_file": bool(ver.kp_path),
             "changes": {}, "sent_at": None}]
    for r in db.scalars(select(KpRevision).where(KpRevision.deal_id == deal.id, KpRevision.version_id == ver.id).order_by(KpRevision.no)).all():
        rows.append({"id": r.id, "no": r.no, "at": r.created_at.isoformat() if r.created_at else None, "author": users.get(r.created_by or "", ""),
                     "description": r.description, "price_rub": r.price_rub, "price_before_rub": r.price_before_rub, "margin_pct": r.margin_pct,
                     "has_file": bool(r.kp_path), "changes": r.changes, "sent_at": r.sent_at.isoformat() if r.sent_at else None})
    variants = [{"id": v.id, "name": v.name, "price_rub": v.price_rub, "service_flag": deal.service_flag, "service_in_kp": v.service_in_kp,
                 "configuration": "; ".join(o.get("name", "") for p in deal.positions if p.id in v.position_ids for o in (p.options or [])) or "базовая"}
                for v in deal.variants if v.in_kp and v.price_rub is not None]
    return {"rows": rows, "variants": variants, "can_edit": user.has(Role.CD, Role.GD) and deal.status in (DealStatus.APPROVED, DealStatus.KP_SENT),
            "threshold_pct": settings_service.get(db, "margin_threshold_pct")}


class KpRevisionIn(BaseModel):
    changes: dict = {}          # {variant_id: {adjustments: [{kind, mode, value, label}], extras: [{name, price_rub}], configuration, service_in_kp}}
    description: str = ""


@router.post("/kp/revisions/preview")
def kp_revision_preview(deal_id: str, body: KpRevisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    deal = get_deal_or_404(db, deal_id, user)
    _cd_gd(user)
    ver = _approved_version(db, deal)
    return {"variants": revision_totals(deal, ver, body.changes), "threshold_pct": settings_service.get(db, "margin_threshold_pct")}


@router.post("/kp/revisions", status_code=201)
def kp_revision_create(deal_id: str, body: KpRevisionIn, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    """Новая редакция КП: файл КП с изменениями (скидки, надбавки, услуги, комплектация), цена, маржа, описание изменений."""
    from ..models import KpRevision
    deal = get_deal_or_404(db, deal_id, user)
    _cd_gd(user)
    if deal.status not in (DealStatus.APPROVED, DealStatus.KP_SENT):
        raise HTTPException(409, "КП изменяют после утверждения расчёта (до заключения контракта)")
    if not body.description.strip():
        raise HTTPException(422, "Опишите, что изменено в КП")
    ver = _approved_version(db, deal)
    totals = revision_totals(deal, ver, body.changes)
    prev = _last_revision(db, deal, ver)
    before = prev.price_rub if prev else sum(float(v.price_rub or 0) for v in deal.variants if v.in_kp and v.price_rub is not None)
    no = (prev.no if prev else 0) + 1
    path = _build_kp_file(db, deal, ver, body.changes, revision_no=no)
    ensure_folders(db, deal)
    stored = adapters.storage().upload(path, deal.storage_folder, "05_kp", os.path.basename(path))
    db.add(Attachment(deal_id=deal.id, folder="05_kp", entity="version", entity_id=ver.id, file_name=os.path.basename(path),
                      storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user.id))
    total = round(sum(t["total"] for t in totals.values()), 2)
    cost = sum(t["cost"] or 0 for t in totals.values())
    rev = KpRevision(deal_id=deal.id, version_id=ver.id, no=no, created_by=user.id, description=body.description.strip(), changes=body.changes,
                     price_before_rub=before, price_rub=total, margin_pct=round((total - cost) / total * 100, 2) if cost and total else None,
                     prices={k: t["total"] for k, t in totals.items()}, kp_path=stored.path)
    db.add(rev)
    audit(db, user.id, deal.id, "kp", ver.id, "kp_revision", {"price_rub": before}, {"no": no, "price_rub": total, "description": rev.description})
    db.commit()
    return {"id": rev.id, "no": no, "price_rub": total, "margin_pct": rev.margin_pct}


@router.get("/kp/revisions/{rev_id}/file")
def kp_revision_file(deal_id: str, rev_id: str, format: str = "docx", db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    from ..models import KpRevision
    deal = get_deal_or_404(db, deal_id, user)
    rev = db.get(KpRevision, rev_id)
    if rev is None or rev.deal_id != deal.id:
        raise HTTPException(404)
    return _revision_file(db, deal, rev, format)
