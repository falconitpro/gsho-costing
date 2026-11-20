"""Торги по сделке (D-074): шаги снижения цены и изменения условий после КП, данные для решения, КП на каждом шаге, аналитика.

Шаг торгов хранится в `negotiation_steps` и порождает редакцию КП (`kp_revisions`) с файлом: цена в КП — итог шага,
условия оплаты и срок поставки — из шага. Цена утверждённой версии не меняется: она — первая цена КП, от неё считаются
снижение и коэффициент утарговывания.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc import calc_deal, solve_price
from ..calc.types import PaymentLine
from ..models import CompetitorOffer, Competitor, Deal, DealVersion, KpRevision, NegotiationStep, User
from . import settings_service
from .deal_calc import build_calc_input

MODES = ("pct", "amount", "target")          # снижение в %, снижение на сумму, новая цена
BASES = ("net", "gross")                     # от цены без НДС / с НДС
REASONS = {"client": "просьба клиента", "competitor": "предложение конкурента", "tender": "тендер / переторжка",
           "volume": "объём / повторный заказ", "terms": "изменение условий", "other": "другое"}


def _vat(db: Session) -> float:
    return float(settings_service.get(db, "sales_vat_pct") or 22.0)


def _variants(deal: Deal):
    return [v for v in deal.variants if v.in_kp and v.price_rub is not None]


def steps(db: Session, deal: Deal, ver: DealVersion) -> list[NegotiationStep]:
    return list(db.scalars(select(NegotiationStep).where(NegotiationStep.deal_id == deal.id, NegotiationStep.version_id == ver.id)
                           .order_by(NegotiationStep.no)).all())


def _last_rev(db: Session, deal: Deal, ver: DealVersion) -> Optional[KpRevision]:
    return db.scalar(select(KpRevision).where(KpRevision.deal_id == deal.id, KpRevision.version_id == ver.id).order_by(KpRevision.no.desc()).limit(1))


def current(db: Session, deal: Deal, ver: DealVersion) -> dict:
    """Текущее состояние по предложениям: первая цена КП, текущая (последняя редакция), условия оплаты и срок."""
    from . import offer_prices
    last = (steps(db, deal, ver) or [None])[-1]
    s = settings_service.get_all(db)
    out = {}
    for v in _variants(deal):
        cur = offer_prices.current(db, deal, v) if ver.id == deal.approved_version_id else None    # D-109
        first = float(cur["kd_rub"]) if cur and cur["kd_rub"] is not None else float(v.price_rub)
        if cur is not None:
            price = float(cur["revision"].total_rub) if cur["revision"] is not None else first
        else:
            rev = _last_rev(db, deal, ver)
            price = float((rev.prices or {}).get(v.id, first)) if rev else first
        terms = ((last.terms or {}).get(v.id) if last else None) or {}
        pay = terms.get("client_payments") or v.client_payments or deal.client_payments or s.get("default_client_payment_scheme") or []
        from ..api.pricing import variant_units
        out[v.id] = {"name": v.name, "units": variant_units(deal, v), "first_rub": first, "price_rub": price, "client_payments": pay, "delivery_term": terms.get("delivery_term") or "",
                     "currency": (v.price_currency or "RUB").upper(), "kp_rate": float(v.price_fx_rate or 1.0) if (v.price_currency or "RUB").upper() != "RUB" else 1.0}
    return out


def _rate(cur: str, kp_cur: str, kp_rate: float, rates: dict) -> Optional[float]:
    cur = (cur or "RUB").upper()
    if cur == "RUB":
        return 1.0
    if cur == kp_cur:
        return kp_rate
    r = rates.get(cur)
    return float(r) if r else None


def client_rates(deal: Deal) -> dict[str, float]:
    """Внутренние курсы заказчика для торгов (D-079): RUB за единицу валюты; в расчёте сделки не участвуют."""
    return {k: float(v) for k, v in ((deal.negotiation_info or {}).get("client_rates") or {}).items() if v}


def _input_rate(cur: str, st: dict, rates: dict, client: dict) -> Optional[float]:
    """Сколько рублей расчёта (по курсу цены КП) в единице введённой валюты. По курсам заказчика — если они заданы:
    рубли и другие валюты пересчитываются в валюту КП по его курсам, дальше — по курсу цены КП."""
    cur, kp = (cur or "RUB").upper(), st["currency"]
    if cur == kp:
        return st["kp_rate"]
    if kp != "RUB" and kp in client:
        if cur == "RUB":
            return st["kp_rate"] / client[kp]
        if cur in client:
            return client[cur] / client[kp] * st["kp_rate"]
    return _rate(cur, kp, st["kp_rate"], rates)


def set_client_rates(deal: Deal, rates: dict, user_id: str, comment: str) -> dict:
    clean = {}
    for k, v in (rates or {}).items():
        k = str(k).upper()[:3]
        if v in (None, "", 0):
            continue
        try:
            x = float(str(v).replace(",", "."))
        except ValueError:
            raise ValueError(f"Курс {k}: введите число")
        if not 0 < x < 100000:
            raise ValueError(f"Курс {k} вне разумных пределов")
        clean[k] = round(x, 6)
    info = dict(deal.negotiation_info or {})
    hist = list(info.get("client_rates_history") or [])
    hist.append({"at": now().isoformat(timespec="seconds"), "by": user_id, "rates": clean, "comment": comment.strip()[:300]})
    info.update(client_rates=clean, client_rates_history=hist[-50:])
    deal.negotiation_info = info
    return clean


def new_price(st: dict, inp: dict, vat: float, rates: dict, client: Optional[dict] = None) -> float:
    """Новая цена без НДС, RUB, по вводу шага: {mode, basis, currency, value}."""
    mode, basis = inp.get("mode") or "pct", inp.get("basis") or "net"
    if mode not in MODES or basis not in BASES:
        raise ValueError("Способ снижения: %, сумма или новая цена; база — с НДС или без НДС")
    try:
        val = float(str(inp.get("value") if inp.get("value") is not None else "").replace(",", "."))
    except ValueError:
        raise ValueError("Введите число")
    cur_rub = st["price_rub"]
    if mode == "pct":
        if not 0 <= val < 100:
            raise ValueError("Снижение в процентах — от 0 до 100")
        return round(cur_rub * (1 - val / 100.0), 2)
    rate = _input_rate(inp.get("currency") or st["currency"], st, rates, client or {})
    if rate is None:
        raise ValueError(f"Нет курса ЦБ для {inp.get('currency')}")
    n = st.get("units", 1) if inp.get("per_unit", True) else 1      # D-085: сумма — за 1 шт., в цену — × количество
    rub = val * rate * n / ((1 + vat / 100.0) if basis == "gross" else 1.0)
    price = cur_rub - rub if mode == "amount" else rub
    if price <= 0:
        raise ValueError("Цена после снижения должна быть больше нуля")
    return round(price, 2)


def _threshold(db: Session, price_rub: Optional[float]) -> float:
    from ..calc.types import OverheadStep, Thresholds
    s = settings_service.get_all(db)
    return Thresholds(margin_threshold_pct=s["margin_threshold_pct"],
                      margin_scale=[OverheadStep(**o) for o in (s.get("margin_threshold_scale") or []) if o.get("pct") is not None]).margin_for(price_rub)


def _money(rub: float, st: dict, vat: float) -> dict:
    k = st["kp_rate"] or 1.0
    return {"net_rub": round(rub, 2), "gross_rub": round(rub * (1 + vat / 100), 2), "currency": st["currency"],
            "net": round(rub / k, 2), "gross": round(rub * (1 + vat / 100) / k, 2)}


def evaluate(db: Session, deal: Deal, ver: DealVersion, prices: dict[str, float], terms: dict[str, dict]) -> dict:
    """Пересчёт по цене и условиям шага: маржа по сценариям, прибыль, разрыв, минимальная цена по порогу и безубыточная."""
    st = current(db, deal, ver)
    inp = build_calc_input(db, deal)
    for v in inp.variants:
        if v.id in st:
            v.price_rub = prices.get(v.id, st[v.id]["price_rub"])
            pay = (terms.get(v.id) or {}).get("client_payments") or st[v.id]["client_payments"]
            if pay:
                v.client_payments = [PaymentLine(**l) for l in pay]
    res = calc_deal(inp)
    vat = _vat(db)
    out = {}
    for vid, s in st.items():
        r = res.variants.get(vid) or {}
        b, p, o = r.get("base"), r.get("pessimistic"), r.get("optimistic")
        price = prices.get(vid, s["price_rub"])
        thr = _threshold(db, price)
        try:
            min_thr = solve_price(inp, vid, target_margin_pct=thr)
            min_zero = solve_price(inp, vid, target_margin_pct=0.0)
        except Exception:
            min_thr = min_zero = None
        out[vid] = {"name": s["name"], "price": _money(price, s, vat), "before": _money(s["price_rub"], s, vat), "first": _money(s["first_rub"], s, vat),
                    "change_rub": round(price - s["price_rub"], 2), "change_pct": round((price - s["price_rub"]) / s["price_rub"] * 100, 2) if s["price_rub"] else None,
                    "from_first_pct": round((price - s["first_rub"]) / s["first_rub"] * 100, 2) if s["first_rub"] else None,
                    "cost_rub": round(b.cost_total_rub, 2) if b else None, "cost_pess_rub": round(p.cost_total_rub, 2) if p else None,
                    "margin": {"base": b.margin_pct if b else None, "pessimistic": p.margin_pct if p else None, "optimistic": o.margin_pct if o else None},
                    "profit_rub": round(b.profit_rub, 2) if b and b.profit_rub is not None else None,
                    "cash_gap_rub": round(b.cash_gap.amount_rub, 2) if b else None, "cash_gap_days": b.cash_gap.days if b else None,
                    "finance_rub": round((b.items_rub or {}).get("finance", 0.0), 2) if b else None,
                    "threshold_pct": thr, "min_price_threshold": _money(min_thr, s, vat) if min_thr else None,
                    "min_price_zero": _money(min_zero, s, vat) if min_zero else None,
                    "room_rub": round(price - min_thr, 2) if min_thr else None,
                    "room_pct": round((price - min_thr) / price * 100, 2) if min_thr and price else None}
        warn = []
        if b and b.margin_pct is not None and b.margin_pct < thr:
            warn.append(f"маржа {b.margin_pct:.2f} % ниже порога {thr:g} %")
        # D-088: в торгах решение — по базовому сценарию; пессимистичный и оптимистичный — в денежном потоке и аналитике
        out[vid]["warnings"] = warn
    return out


def competitors(db: Session, deal: Deal) -> list[dict]:
    from ..api.competitors import money
    names = {c.id: c.name for c in db.scalars(select(Competitor)).all()}
    rows = db.scalars(select(CompetitorOffer).where(CompetitorOffer.deal_id == deal.id, CompetitorOffer.deleted_at.is_(None))
                      .order_by(CompetitorOffer.on.desc())).all()
    out = []
    for o in rows:
        m = money(db, o.amount, o.currency, o.vat_included, o.vat_pct, o.fx_rate, o.currency, o.on)
        out.append({"id": o.id, "competitor": names.get(o.competitor_id or "", "") or "название неизвестно", "on": o.on.isoformat(), "variant_id": o.variant_id,
                    "equipment": o.equipment, "qty": o.qty, "rub_net": m["rub_net"], "rub_gross": m["rub_gross"], "amount": o.amount, "currency": o.currency,
                    "vat_included": o.vat_included, "delivery_term": o.delivery_term or "", "payment_terms": o.payment_terms or "", "source": o.source})
    return out


def step_dict(s: NegotiationStep, names: dict[str, str]) -> dict:
    return {"id": s.id, "no": s.no, "at": s.created_at.isoformat() if s.created_at else None, "by_name": names.get(s.created_by or "", ""),
            "kind": s.kind, "reason": s.reason, "reason_title": REASONS.get(s.reason, s.reason), "comment": s.comment, "input": s.input, "terms": s.terms,
            "result": s.result, "price_before_rub": s.price_before_rub, "price_rub": s.price_rub, "margin_pct": s.margin_pct,
            "show_discount": s.show_discount, "kp_revision_id": s.kp_revision_id, "sent_at": s.sent_at.isoformat() if s.sent_at else None,
            "sent_to": s.sent_to or [], "client_answer": s.client_answer or "", "review": s.review or {}}


def analytics(deal: Deal, ver: DealVersion, st: dict, rows: list[NegotiationStep], comps: list[dict]) -> dict:
    """Итоги торгов: первая и текущая цена, снижение, коэффициент утарговывания, шаги, сроки, маржа, отрыв от конкурентов."""
    first = sum(s["first_rub"] for s in st.values())
    now = sum(s["price_rub"] for s in st.values())
    res = (ver.calc_result or {}).get("variants") or {}
    cost = sum(((res.get(vid) or {}).get("base") or {}).get("cost_total_rub") or 0 for vid in st)
    m0 = round((first - cost) / first * 100, 2) if first and cost else None
    m1 = rows[-1].margin_pct if rows else m0
    start = ver.created_at.date() if ver.created_at else None
    end = rows[-1].created_at.date() if rows and rows[-1].created_at else None
    best = min((c["rub_net"] for c in comps), default=None)
    steps_price = [r for r in rows if r.price_rub is not None and r.price_before_rub is not None and r.price_rub < r.price_before_rub - 0.005]
    return {"first_rub": round(first, 2), "current_rub": round(now, 2), "discount_rub": round(first - now, 2),
            "discount_pct": round((first - now) / first * 100, 2) if first else None,
            # коэффициент утарговывания: доля снижения от первой цены КП (0 — без снижения); и итоговая цена к первой
            "bargain_coef": round((first - now) / first, 4) if first else None,
            "bargain_pct": round((first - now) / first * 100, 2) if first else None,   # процент утарговывания (D-078) "price_ratio": round(now / first, 4) if first else None,
            "steps": len(rows), "price_steps": len(steps_price), "terms_steps": sum(1 for r in rows if r.terms),
            "avg_step_pct": round(sum((r.price_before_rub - r.price_rub) / r.price_before_rub * 100 for r in steps_price) / len(steps_price), 2) if steps_price else None,
            "days": (end - start).days if start and end else 0, "first_kp_on": start.isoformat() if start else None, "last_step_on": end.isoformat() if end else None,
            "margin_first_pct": m0, "margin_now_pct": m1, "margin_lost_pp": round(m0 - m1, 2) if m0 is not None and m1 is not None else None,
            "best_competitor_rub": best, "gap_to_competitor_pct": round((now - best) / best * 100, 2) if best else None,
            "outcome": deal.outcome or "", "status": deal.status.value}


def revision_changes(db: Session, deal: Deal, ver: DealVersion, st: dict, prices: dict[str, float], terms: dict[str, dict], show_discount: bool) -> dict:
    """Изменения для редакции КП: от последней редакции — услуги и комплектация, цена и условия — из шага."""
    rev = _last_rev(db, deal, ver)
    prev = dict((rev.changes or {}) if rev else {})
    out = {}
    for vid, s in st.items():
        ch = dict(prev.get(vid) or {})
        # услуги из прошлой редакции входят в итог; цена шага — итог КП, поэтому «база» = итог минус услуги
        extras = sum(float(e.get("price_rub") or 0) for e in ch.get("extras") or [] if str(e.get("name") or "").strip())
        target = prices.get(vid, s["price_rub"])
        ch["adjustments"] = []
        if show_discount and target < s["first_rub"] + extras - 0.005:
            ch.pop("base_rub", None)
            ch["adjustments"] = [{"kind": "discount", "mode": "amount", "value": round(s["first_rub"] + extras - target, 2), "label": "Скидка"}]
        else:
            ch["base_rub"] = round(target - extras, 2)
        t = terms.get(vid) or {}
        if t.get("client_payments"):
            ch["client_payments"] = t["client_payments"]
        if t.get("delivery_term"):
            ch["delivery_term"] = t["delivery_term"]
        out[vid] = ch
    return out


def names(db: Session) -> dict[str, str]:
    return {u.id: u.display_name or u.email for u in db.scalars(select(User)).all()}


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return date.today()
