"""Цена при изменении условий контракта (D-104).

Редакции КП и шаги торгов привязаны к утверждённой версии. Когда условия контракта меняются, появляется новая
версия, а цена КД в предложении оставалась первой (до торгов), — и после новой версии карточка, списки, договор
и текущий расчёт брали первую цену. Теперь при фиксации изменения условий действующая цена (после торгов и редакций КП)
переносится в предложение и становится исходной для повторного утверждения: КД может её изменить, и именно она
попадает в новую версию. Прежние редакции и шаги остаются в истории прежней версии.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Deal, DealVersion, KpRevision, Variant

TOL_RUB = 1.0


def revisions_apply(deal: Deal) -> bool:
    """Редакции и шаги утверждённой версии действуют, пока цена не перенесена в предложение для изменения условий."""
    return (deal.negotiation_info or {}).get("rebased_version_id") != deal.approved_version_id


def _set_price(v: Variant, rub: float) -> None:
    rub = round(float(rub), 2)
    cur = (v.price_currency or "RUB").upper()
    rate = float(v.price_fx_rate or 0) if cur != "RUB" else 1.0
    v.price_rub, v.price_mode = rub, "amount"
    if rate:
        v.price_amount = round(rub / rate, 2)
    v.price_vat_included = False
    if v.price_input_units and v.price_amount is not None:     # D-085: введено за 1 шт. — пересчитываем на 1 шт. в валюте сделки
        v.price_input_amount, v.price_input_currency = round(v.price_amount / v.price_input_units, 2), cur
    else:
        v.price_input_amount = v.price_input_currency = v.price_input_units = None


def rebase_for_contract_change(db: Session, deal: Deal, user_id: str | None) -> dict:
    """Перед новой версией по изменению условий: действующая цена и условия оплаты → в предложение."""
    from .deal_calc import negotiated
    from .deal_service import audit
    changed = {}
    if revisions_apply(deal):
        for vid, n in negotiated(db, deal).items():
            v = next((x for x in deal.variants if x.id == vid), None)
            if v is None or v.price_rub is None:
                continue
            before = {"price_rub": v.price_rub, "price_amount": v.price_amount, "client_payments": v.client_payments}
            if n.get("price_rub") is not None and abs(float(n["price_rub"]) - float(v.price_rub)) > 0.005:
                _set_price(v, n["price_rub"])
            if n.get("client_payments"):
                v.client_payments = n["client_payments"]
            after = {"price_rub": v.price_rub, "price_amount": v.price_amount, "client_payments": v.client_payments}
            if after != before:
                changed[vid] = after["price_rub"]
                audit(db, user_id, deal.id, "variant", v.id, "contract_rebase", before, after,
                      "Условия контракта изменены: цена после торгов и редакций КП перенесена в предложение")
    info = dict(deal.negotiation_info or {})
    info["rebased_version_id"] = deal.approved_version_id
    deal.negotiation_info = info
    return changed


def repair_existing(db: Session) -> int:
    """Разовое исправление сделок, где новая версия по изменению условий уже утверждена, а в предложении первая цена:
    цена версии → в предложение. Повторный запуск ничего не меняет."""
    from .deal_service import audit
    n = 0
    rows = db.execute(select(Deal, DealVersion).join(DealVersion, DealVersion.id == Deal.approved_version_id)
                      .where(DealVersion.reason == "contract_change", Deal.deleted_at.is_(None))).all()
    for deal, ver in rows:
        if db.scalar(select(KpRevision.id).where(KpRevision.deal_id == deal.id, KpRevision.version_id == ver.id).limit(1)):
            continue
        prices = {x.get("id"): x.get("price_rub") for x in (ver.calc_input or {}).get("variants") or []}
        for v in deal.variants:
            vp = prices.get(v.id)
            if vp is None or v.price_rub is None or abs(float(vp) - float(v.price_rub)) <= TOL_RUB:
                continue
            before = {"price_rub": v.price_rub, "price_amount": v.price_amount}
            _set_price(v, vp)
            audit(db, None, deal.id, "variant", v.id, "contract_rebase", before, {"price_rub": v.price_rub, "price_amount": v.price_amount},
                  f"Исправление D-104: цена утверждённой версии № {ver.version_no} перенесена в предложение")
            n += 1
    if n:
        db.commit()
    return n
