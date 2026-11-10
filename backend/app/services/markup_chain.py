"""Цепочка поставки запчастей через компании группы: наценка на каждой ступени и маржа каждой компании.

ТЗ модуля «Запасные части», раздел 2. Модуль чистый (без БД и веба), чтобы его можно было проверять тестами.

Маршрут — упорядоченные ступени продажи. Ступень 1: поставщик (не компания группы) продаёт первой компании
по своей цене. Каждая следующая ступень: компания группы перепродаёт следующей с наценкой из политики
(или по фиксированной трансфертной цене). Последний покупатель — компания, продающая клиенту (РФ).

Политика наценок (`rules`):
    {"default": {entity_id: pct},                                         # ставка компании-продавца по умолчанию
     "by_group": [{"entity_id", "part_group", "pct"}],                     # исключение для группы товара
     "by_supplier": [{"entity_id", "supplier_id", "pct"}],                 # исключение для поставщика
     "min_margin_pct": 1.0}                                                # минимум маржи компании на ступени
Приоритет: поставщик → группа товара → ставка по умолчанию → 0.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


class ChainError(ValueError):
    """Маршрут или данные для расчёта некорректны (сообщение — для пользователя)."""


def markup_pct_for(rules: Mapping[str, Any], entity_id: str, part_group: Optional[str] = None,
                   supplier_id: Optional[str] = None) -> float:
    rules = rules or {}
    if supplier_id:
        for r in rules.get("by_supplier") or []:
            if r.get("entity_id") == entity_id and r.get("supplier_id") == supplier_id:
                return float(r.get("pct") or 0)
    if part_group:
        for r in rules.get("by_group") or []:
            if r.get("entity_id") == entity_id and (r.get("part_group") or "").strip().lower() == part_group.strip().lower():
                return float(r.get("pct") or 0)
    return float((rules.get("default") or {}).get(entity_id) or 0)


def gsho_markup_pct_for(rules: Mapping[str, Any], entity_id: str, supplier_id: Optional[str] = None, machine_type: Optional[str] = None) -> Optional[float]:
    """Наценка компании группы на ГШО (D-153): поставщик → тип машины → ставка ГШО по умолчанию. Нет ставки ГШО — None
    (ставки запчастей на ГШО не переносятся: у техники своя экономика)."""
    rules = rules or {}
    if supplier_id:
        for r in rules.get("gsho_by_supplier") or []:
            if r.get("entity_id") == entity_id and r.get("supplier_id") == supplier_id:
                return float(r.get("pct") or 0)
    if machine_type:
        for r in rules.get("gsho_by_machine_type") or []:
            if r.get("entity_id") == entity_id and (r.get("machine_type") or "").strip().lower() == machine_type.strip().lower():
                return float(r.get("pct") or 0)
    d = rules.get("gsho_default") or {}
    return float(d[entity_id]) if entity_id in d and d[entity_id] is not None else None


def validate_steps(steps: Sequence[Mapping[str, Any]]) -> list[str]:
    """Проверки маршрута: первая ступень — от поставщика, далее продавец = покупатель предыдущей ступени."""
    errs: list[str] = []
    if not steps:
        return ["В маршруте нет ни одной ступени"]
    if steps[0].get("seller_entity_id"):
        errs.append("Первая ступень — продажа от поставщика: продавец не указывается")
    for i, s in enumerate(steps):
        n = i + 1
        if not s.get("buyer_entity_id"):
            errs.append(f"Ступень {n}: не указан покупатель")
        if not s.get("currency"):
            errs.append(f"Ступень {n}: не указана валюта")
        if i > 0:
            prev = steps[i - 1].get("buyer_entity_id")
            if s.get("seller_entity_id") != prev:
                errs.append(f"Ступень {n}: продавец должен быть покупателем ступени {i}")
            if s.get("seller_entity_id") == s.get("buyer_entity_id"):
                errs.append(f"Ступень {n}: компания не может продавать сама себе")
    return errs


def _to(amount: float, cur_from: str, cur_to: str, fx: Mapping[str, float]) -> float:
    """Перевод через рубль; fx — курс ЦБ: рублей за единицу валюты."""
    if cur_from == cur_to:
        return amount
    rate = lambda c: 1.0 if c == "RUB" else fx.get(c)   # noqa: E731
    a, b = rate(cur_from), rate(cur_to)
    if not a or not b:
        missing = cur_from if not a else cur_to
        raise ChainError(f"Нет курса {missing} к рублю — добавьте курс или выберите другую валюту ступени")
    return amount * a / b


def simulate(purchase_price: float, steps: Sequence[Mapping[str, Any]], rules: Mapping[str, Any], *,
             fx: Mapping[str, float], part_group: Optional[str] = None, supplier_id: Optional[str] = None,
             client_price_rub: Optional[float] = None, extra_costs_rub: float = 0.0,
             entity_names: Optional[Mapping[str, str]] = None, mode: str = "parts", machine_type: Optional[str] = None) -> dict[str, Any]:
    """Цены на каждой ступени, маржа каждой компании группы и маржа группы.

    purchase_price — цена поставщика в валюте первой ступени. extra_costs_rub — прочие расходы до продажи клиенту
    (логистика, пошлины и т. п.), в этой проверке наценок — по желанию, чтобы увидеть маржу группы.
    """
    errs = validate_steps(steps)
    if errs:
        raise ChainError("; ".join(errs))
    if purchase_price is None or purchase_price <= 0:
        raise ChainError("Укажите цену поставщика больше нуля")
    names = entity_names or {}
    min_margin = (rules or {}).get("min_margin_pct")
    out_steps: list[dict[str, Any]] = []
    companies: list[dict[str, Any]] = []
    warnings: list[str] = []

    first = steps[0]
    price, cur = float(purchase_price), first["currency"]
    out_steps.append({"no": 1, "seller": "Поставщик", "seller_entity_id": None, "buyer_entity_id": first["buyer_entity_id"],
                      "buyer": names.get(first["buyer_entity_id"], first["buyer_entity_id"]), "currency": cur,
                      "incoterms": first.get("incoterms") or "", "price": round(price, 2), "markup_pct": None,
                      "price_rub": round(_to(price, cur, "RUB", fx), 2)})
    purchase_rub = _to(price, cur, "RUB", fx)

    for i, s in enumerate(steps[1:], start=2):
        seller = s["seller_entity_id"]
        sale_cur = s["currency"]
        cost_in_sale_cur = _to(price, cur, sale_cur, fx)
        fixed = s.get("transfer_price")
        if fixed:
            sale, pct = float(fixed), None
        elif mode == "gsho":
            pct = gsho_markup_pct_for(rules, seller, supplier_id, machine_type)
            if pct is None:
                raise ChainError(f"Для «{names.get(seller, seller)}» не задана наценка на ГШО в политике наценок группы")
            sale = cost_in_sale_cur * (1 + pct / 100)
        else:
            pct = markup_pct_for(rules, seller, part_group, supplier_id)
            sale = cost_in_sale_cur * (1 + pct / 100)
        margin = sale - cost_in_sale_cur
        margin_pct = margin / sale * 100 if sale else 0.0
        seller_name = names.get(seller, seller)
        out_steps.append({"no": i, "seller": seller_name, "seller_entity_id": seller, "buyer_entity_id": s["buyer_entity_id"],
                          "buyer": names.get(s["buyer_entity_id"], s["buyer_entity_id"]), "currency": sale_cur,
                          "incoterms": s.get("incoterms") or "", "price": round(sale, 2), "markup_pct": pct,
                          "transfer_price": bool(fixed), "price_rub": round(_to(sale, sale_cur, "RUB", fx), 2)})
        companies.append({"entity_id": seller, "name": seller_name, "currency": sale_cur, "margin": round(margin, 2),
                          "margin_rub": round(_to(margin, sale_cur, "RUB", fx), 2), "margin_pct": round(margin_pct, 2)})
        if margin < 0:
            warnings.append(f"{seller_name}: отрицательная маржа на ступени {i}")
        elif min_margin is not None and margin_pct < float(min_margin):
            warnings.append(f"{seller_name}: маржа {margin_pct:.2f} % ниже минимума {float(min_margin):.2f} %")
        price, cur = sale, sale_cur

    final_buyer = steps[-1]["buyer_entity_id"]
    landed_rub = _to(price, cur, "RUB", fx) + float(extra_costs_rub or 0)
    result: dict[str, Any] = {"steps": out_steps, "companies": companies, "warnings": warnings,
                              "final_buyer_entity_id": final_buyer, "final_buyer": names.get(final_buyer, final_buyer),
                              "purchase_rub": round(purchase_rub, 2), "landed_rub": round(landed_rub, 2),
                              "intragroup_markup_rub": round(sum(c["margin_rub"] for c in companies), 2)}
    if client_price_rub:
        cp = float(client_price_rub)
        seller_margin = cp - landed_rub
        group_margin = cp - purchase_rub - float(extra_costs_rub or 0)
        result["client_price_rub"] = round(cp, 2)
        result["seller_margin_rub"] = round(seller_margin, 2)
        result["seller_margin_pct"] = round(seller_margin / cp * 100, 2)
        result["group_margin_rub"] = round(group_margin, 2)
        result["group_margin_pct"] = round(group_margin / cp * 100, 2)
        # доля маржи группы по компаниям (продающая клиенту — последней)
        parts = [{"name": c["name"], "margin_rub": c["margin_rub"]} for c in companies]
        parts.append({"name": result["final_buyer"], "margin_rub": round(seller_margin, 2)})
        result["group_split"] = [{**p, "share_pct": round(p["margin_rub"] / group_margin * 100, 1) if group_margin else 0.0} for p in parts]
        if seller_margin < 0:
            warnings.append(f"{result['final_buyer']}: цена клиенту ниже стоимости с наценками группы")
    return result
