"""«Действующее предложение» (D-103): одна точка, где собирается цена, которую клиент видит сейчас, —
в валюте предложения, за 1 шт. и за всё, с НДС и без, — и сверка всех мест, где цена хранится.

Только чтение: расчёт, торги и редакции КП не меняются. Источник действующей цены — тот же, что у карточки
и списков после торгов (`deal_calc.actual_offer`, D-093): последняя редакция КП утверждённой версии,
а пока редакций нет — цена КД в предложении. Остальные места хранения показываются рядом, расхождения — списком.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Deal, DealVersion, KpRevision, NegotiationStep
from . import settings_service
from .deal_calc import actual_offer

TOL_RUB = 1.0          # расхождение меньше рубля — округление
POST_APPROVAL = ("approved", "kp_sent", "won", "contract", "execution", "closed")


def _f(x) -> Optional[float]:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def _n(x) -> str:
    return f"{float(x):,.0f}".replace(",", "\u00a0")


def _money(rub: Optional[float], rate: float, units: int, vat: float) -> Optional[dict]:
    if rub is None:
        return None
    k = rate or 1.0
    return {"net_rub": round(rub, 2), "gross_rub": round(rub * (1 + vat / 100), 2),
            "net": round(rub / k, 2), "gross": round(rub * (1 + vat / 100) / k, 2),
            "unit_net": round(rub / k / units, 2), "unit_gross": round(rub * (1 + vat / 100) / k / units, 2)}


def build(db: Session, deal: Deal) -> dict:
    from ..api.pricing import revision_totals, variant_units
    from .execution_service import won_variant
    vat = float(settings_service.get(db, "sales_vat_pct") or 22.0)
    ver = db.get(DealVersion, deal.approved_version_id) if deal.approved_version_id else None
    rev = step = None
    if ver is not None:
        rev = db.scalar(select(KpRevision).where(KpRevision.deal_id == deal.id, KpRevision.version_id == ver.id)
                        .order_by(KpRevision.no.desc()).limit(1))
        step = db.scalar(select(NegotiationStep).where(NegotiationStep.deal_id == deal.id, NegotiationStep.version_id == ver.id)
                         .order_by(NegotiationStep.no.desc()).limit(1))
    totals = revision_totals(deal, ver, rev.changes or {}) if (rev is not None and ver is not None) else {}
    won = won_variant(deal) if deal.won_variant_id else None
    res_vars = ((ver.calc_result or {}).get("variants") or {}) if ver is not None else {}

    out = []
    for v in deal.variants:
        if not v.in_kp or v.price_rub is None:
            continue
        units = variant_units(deal, v)
        ao = actual_offer(deal, v, db)
        post = deal.status.value in POST_APPROVAL
        cur_set = bool((v.price_currency or "").strip())
        cur = (v.price_currency or "RUB").upper()
        rate = float(v.price_fx_rate or 0) if cur != "RUB" else 1.0
        price = _f(ao["price_rub"])
        t = totals.get(v.id) or {}
        extras = round(sum(a for _, a in t.get("extras") or [] if a), 2) if t else 0.0
        ver_price = _f((res_vars.get(v.id) or {}).get("base", {}).get("price_rub"))
        step_price = _f(((step.result or {}).get(v.id) or {}).get("price", {}).get("net_rub")) if step is not None else None
        rev_price = _f((rev.prices or {}).get(v.id)) if rev is not None else None

        # после утверждения цена клиенту — цена утверждённой версии; после изменения условий контракта версия несёт цену
        # торгов, а цена КД в предложении остаётся первой — поэтому без редакций берём цену версии, а не предложения
        if rev_price is None and post and ver_price is not None:
            price = ver_price
        if rev_price is not None:
            source = {"kind": "revision", "title": f"Редакция КП № {rev.no}", "on": rev.created_at.isoformat() if rev.created_at else None,
                      "sent": bool(rev.sent_at)}
        elif ver is not None:
            source = {"kind": "version", "title": f"Утверждённая версия № {ver.version_no}", "on": ver.created_at.isoformat() if ver.created_at else None,
                      "sent": deal.status.value not in ("approved",)}
        else:
            source = {"kind": "draft", "title": "Цена КД (расчёт не утверждён)", "on": None, "sent": False}

        places = [{"key": "variant", "title": "Цена КД в предложении", "price_rub": _f(v.price_rub)}]
        if ver is not None:
            places.append({"key": "version", "title": f"Утверждённая версия № {ver.version_no}", "price_rub": ver_price})
        if step is not None:
            places.append({"key": "step", "title": f"Шаг торгов № {step.no}", "price_rub": step_price})
        if rev is not None:
            places.append({"key": "revision", "title": f"Редакция КП № {rev.no}", "price_rub": rev_price})
            places.append({"key": "calc", "title": "В расчёте маржи (без услуг из КП)", "price_rub": round(price - extras, 2) if price is not None else None})

        issues: list[dict] = []
        if not cur_set:
            issues.append({"level": "warn", "text": "Валюта цены клиенту не задана — цена считается в рублях"})
        if cur != "RUB" and not rate:
            issues.append({"level": "error", "text": f"Нет курса цены {cur}: сумму в валюте не посчитать"})
        if rev_price is None and cur != "RUB" and rate and v.price_amount is not None \
                and abs(float(v.price_amount) * rate - float(v.price_rub)) > max(TOL_RUB, float(v.price_rub) * 1e-6):
            issues.append({"level": "warn", "text": f"Цена в {cur} × курс не равна цене в рублях ({_n(float(v.price_amount) * rate)} и {_n(v.price_rub)} ₽)"})
        if ver_price is not None and abs(ver_price - float(v.price_rub)) > TOL_RUB:
            if not post:
                issues.append({"level": "info", "text": "Цена КД изменена после утверждения — действует после нового утверждения"})
            elif rev_price is None:
                issues.append({"level": "error", "text": f"Цена в предложении ({_n(v.price_rub)} ₽) не равна утверждённой версии № {ver.version_no} "
                                                         f"({_n(ver_price)} ₽): карточка, списки, договор и текущий расчёт берут цену предложения"})
        if step is not None and rev is not None and step.kp_revision_id and step.kp_revision_id != rev.id:
            issues.append({"level": "info", "text": f"После шага торгов № {step.no} КП правили вручную (редакция № {rev.no})"})
        elif step_price is not None and rev_price is not None and abs(step_price - rev_price) > TOL_RUB:
            issues.append({"level": "warn", "text": f"Цена шага торгов № {step.no} не совпадает с редакцией КП № {rev.no}"})
        if extras:
            issues.append({"level": "info", "text": f"В цену КП входят услуги на {_n(extras)} ₽ без НДС; маржа считается без них"})
        if v.price_input_units and v.price_input_units != units:
            issues.append({"level": "warn", "text": f"Цену вводили за 1 шт. при количестве {v.price_input_units}, сейчас {units} шт. — проверьте итог"})

        out.append({"id": v.id, "name": v.name, "units": units, "currency": cur, "currency_set": cur_set, "rate": rate or None, "vat_pct": vat,
                    "price": _money(price, rate, units, vat), "first": _money(_f(v.price_rub), rate, units, vat),
                    "change_pct": round((price - float(v.price_rub)) / float(v.price_rub) * 100, 2) if price is not None and v.price_rub else None,
                    "source": source, "client_payments": ao.get("client_payments") or [], "negotiated": bool(ao.get("negotiated")),
                    "won": bool(won is not None and won.id == v.id), "places": places, "issues": issues})
    return {"approved": ver is not None, "variants": out,
            "issues_count": sum(1 for x in out for i in x["issues"] if i["level"] in ("warn", "error"))}
