"""Заявки клиентов на запчасти: подбор строк по каталогу, наличие, цена и маржа (ТЗ запчастей, разделы 5–6)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Part, PartCrossRef, PartRequest, PartRequestLine, SupplyRoute
from . import settings_service
from .markup_chain import ChainError, simulate
from .parts_pricing import price_line


def norm_article(s: str) -> str:
    """Артикул для сравнения: без пробелов, дефисов, точек, слешей и регистра."""
    return re.sub(r"[\s\-./_]", "", (s or "")).upper()


def next_number(db: Session) -> str:
    year = date.today().year
    prefix = f"ЗЧ-{year}-"
    last = db.scalars(select(PartRequest.number).where(PartRequest.number.like(f"{prefix}%"))).all()
    n = max([int(x.rsplit("-", 1)[1]) for x in last if x.rsplit("-", 1)[1].isdigit()] or [0]) + 1
    return f"{prefix}{n:04d}"


def parse_lines_text(text: str) -> list[dict]:
    """Строки, вставленные из письма или Excel: «артикул <Tab> наименование <Tab> количество» (наименование и количество — по желанию)."""
    out = []
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        parts = [p.strip() for p in (raw.split("\t") if "\t" in raw else re.split(r"\s*;\s*", raw))]
        art = parts[0] if parts else ""
        qty, name = 1.0, ""
        rest = parts[1:]
        if rest and re.fullmatch(r"\d+([.,]\d+)?", rest[-1]):
            qty = float(rest[-1].replace(",", "."))
            rest = rest[:-1]
        name = " ".join(rest)
        out.append({"raw_article": art, "raw_name": name, "qty": qty})
    return out


class Index:
    """Индекс каталога для подбора: артикул, OEM, номера поставщиков и аналогов (только активные карточки)."""

    def __init__(self, db: Session):
        self.by_article: dict[str, list[str]] = {}
        self.by_oem: dict[str, list[str]] = {}
        self.by_cross: dict[str, list[tuple[str, str]]] = {}
        for pid, art, oem in db.execute(select(Part.id, Part.article, Part.oem_number).where(Part.status == "active")).all():
            if art:
                self.by_article.setdefault(norm_article(art), []).append(pid)
            if oem:
                self.by_oem.setdefault(norm_article(oem), []).append(pid)
        active = set(sum(self.by_article.values(), []))
        for pid, num, kind in db.execute(select(PartCrossRef.part_id, PartCrossRef.number, PartCrossRef.kind)).all():
            if pid in active:
                self.by_cross.setdefault(norm_article(num), []).append((pid, "supplier" if kind == "supplier" else "analog"))

    def match(self, raw: str) -> tuple[Optional[str], str, list[str]]:
        """→ (part_id, вид совпадения, все кандидаты). Несколько кандидатов — подбор остаётся менеджеру."""
        k = norm_article(raw)
        if not k:
            return None, "", []
        for kind, src in (("article", self.by_article), ("oem", self.by_oem)):
            ids = src.get(k) or []
            if len(ids) == 1:
                return ids[0], kind, ids
            if ids:
                return None, "", ids
        cross = self.by_cross.get(k) or []
        ids = list(dict.fromkeys(pid for pid, _ in cross))
        if len(ids) == 1:
            return ids[0], cross[0][1], ids
        return None, "", ids


def match_lines(db: Session, req: PartRequest) -> dict:
    idx = Index(db)
    matched = ambiguous = 0
    for line in req.lines:
        if line.part_id:
            continue
        pid, kind, cands = idx.match(line.raw_article)
        if pid:
            line.part_id, line.match_kind = pid, kind
            p = db.get(Part, pid)
            if p and not line.raw_name:
                line.raw_name = p.name_ru
            if p and line.unit in ("", "шт") and p.unit:
                line.unit = p.unit
            matched += 1
        elif cands:
            line.calc = {**(line.calc or {}), "candidates": cands[:10]}
            ambiguous += 1
    return {"matched": matched, "ambiguous": ambiguous, "unmatched": sum(1 for l in req.lines if not l.part_id)}


def _route_steps(db: Session, route_id: Optional[str]) -> list[dict]:
    if not route_id:
        return []
    r = db.get(SupplyRoute, route_id)
    if r is None:
        return []
    return [{"seller_entity_id": s.seller_entity_id, "buyer_entity_id": s.buyer_entity_id, "currency": s.currency, "incoterms": s.incoterms}
            for s in r.steps]


def unit_cost_rub(db: Session, part: Part, route_id: Optional[str], rules: dict, fx: dict) -> tuple[Optional[float], str]:
    """Себестоимость единицы для компании в РФ: закупочная цена по маршруту с наценками группы, в рублях по курсу ЦБ.
    Без маршрута — закупочная цена в рублях. Без закупочной цены — неизвестна."""
    if not part.purchase_price:
        return None, "нет закупочной цены"
    steps = _route_steps(db, route_id or part.route_id)
    cur = part.purchase_currency or "EUR"
    if steps:
        if steps[0]["currency"] != cur:   # цена поставщика — в валюте первой ступени
            steps = [{**steps[0], "currency": cur}, *steps[1:]]
        try:
            return simulate(part.purchase_price, steps, rules, fx=fx, part_group=part.part_group or None,
                            supplier_id=part.supplier_id)["landed_rub"], "маршрут"
        except ChainError as e:
            return None, str(e)
    rate = 1.0 if cur == "RUB" else fx.get(cur)
    return (part.purchase_price * rate, "закупочная цена") if rate else (None, f"нет курса {cur}")


def contract_price(db: Session, part: Part, client_id: str, on: date) -> Optional[tuple[float, str]]:
    for cp in part.prices:
        if cp.client_id == client_id and (not cp.valid_from or cp.valid_from <= on) and (not cp.valid_to or cp.valid_to >= on):
            return cp.price, cp.currency
    return None


def price_lines(db: Session, req: PartRequest, fx: dict, rules: dict) -> None:
    bands = settings_service.get(db, "parts_margin_bands")
    tol = settings_service.get(db, "parts_cd_tolerance_pp")
    today = date.today()
    for line in req.lines:
        if line.status in ("cancelled", "unavailable") or not line.part_id:
            continue
        part = db.get(Part, line.part_id)
        available = sum((s.qty or 0) - (s.reserved or 0) for s in part.stock)
        if not line.source:
            line.source = "stock" if available >= (line.qty or 0) else "order"
        if line.source == "order" and line.lead_days is None:
            line.lead_days = part.lead_days
        cost, cost_note = unit_cost_rub(db, part, line.route_id, rules, fx)
        manual = (line.price, line.currency) if line.price_source == "manual" and line.price else None
        pricelist = (part.sale_price, part.sale_currency or "EUR") if part.sale_price else None
        res = price_line(qty=line.qty, unit_cost_rub=cost, fx=fx, manual=manual, contract=contract_price(db, part, req.client_id, today),
                         pricelist=pricelist, bands=bands, cd_tolerance_pp=tol)
        line.price, line.currency, line.price_source = res["price"], res["currency"], res["price_source"]
        line.calc = {**{k: v for k, v in (line.calc or {}).items() if k == "candidates"}, **res, "cost_note": cost_note, "available": available}
        if line.price is not None and line.status == "new":
            line.status = "quoted"


def line_payload(line: PartRequestLine, part: Optional[Part]) -> dict:
    from ..api.common import serialize
    d = serialize(line)
    calc = d.pop("calc", {}) or {}
    # себестоимость, маржа и отклонение от порога — только КД, ФД, ЗГД (ключи cost, margin_pct, profit_rub закрываются filter_payload);
    # менеджер видит порог и то, чья виза нужна
    for k in ("cost", "margin_pct", "profit_rub", "threshold_pct", "approval", "cost_note"):
        d[k] = calc.get(k)
    d["sum_rub"], d["price_rub"], d["available"], d["candidates"] = calc.get("sum_rub"), calc.get("price_rub"), calc.get("available"), calc.get("candidates", [])
    d["part"] = {"id": part.id, "article": part.article, "name_ru": part.name_ru, "manufacturer": part.manufacturer, "unit": part.unit} if part else None
    return d


def approvals_pending(req: PartRequest, lines: list[dict]) -> Optional[str]:
    """Нужна ли виза по цене: строки ниже порога, на которые ещё нет визы при текущей цене."""
    covered = {(a_l["id"], a_l["price"]) for a in (req.approvals or []) for a_l in a.get("lines", []) if a.get("level") in ("cd", "fd")}
    fd_covered = {(a_l["id"], a_l["price"]) for a in (req.approvals or []) for a_l in a.get("lines", []) if a.get("level") == "fd"}
    need = None
    for l in lines:
        if l.get("approval") == "fd" and (l["id"], l["price"]) not in fd_covered:
            return "fd"
        if l.get("approval") == "cd" and (l["id"], l["price"]) not in covered:
            need = "cd"
    return need


def count_requests(db: Session) -> int:
    return db.scalar(select(func.count(PartRequest.id))) or 0
