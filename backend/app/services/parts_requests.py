"""Заявки клиентов на запчасти: подбор строк по каталогу, наличие, цена и маржа (ТЗ запчастей, разделы 5–6)."""
from __future__ import annotations

import os

import re
from datetime import date
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Factory, Part, PartCrossRef, PartRequest, PartRequestLine, SupplyRoute
from . import settings_service
from .markup_chain import ChainError, simulate
from .parts_pricing import price_line, supplier_currency


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


def unit_cost_rub(db: Session, part: Part, route_id: Optional[str], rules: dict, fx: dict,
                  quote: Optional[tuple[float, str, Optional[str]]] = None) -> tuple[Optional[float], str]:
    """Себестоимость единицы для компании в РФ: цена поставщика по маршруту с наценками группы, в рублях по курсу ЦБ.
    Цена поставщика — выбранный ответ на запрос (quote: цена, валюта, поставщик), иначе последняя закупочная цена.
    Без маршрута — цена в рублях. Без цены — неизвестна."""
    if quote and quote[0]:
        price, cur, supplier_id, label = quote[0], quote[1] or "EUR", quote[2] or part.supplier_id, "ответ поставщика"
    elif part.purchase_price:
        price, cur, supplier_id, label = part.purchase_price, part.purchase_currency or "EUR", part.supplier_id, "закупочная цена"
    else:
        return None, "нет закупочной цены"
    steps = _route_steps(db, route_id or part.route_id)
    if steps:
        if steps[0]["currency"] != cur:   # цена поставщика — в валюте первой ступени
            steps = [{**steps[0], "currency": cur}, *steps[1:]]
        try:
            return simulate(price, steps, rules, fx=fx, part_group=part.part_group or None, supplier_id=supplier_id)["landed_rub"], f"{label}, маршрут"
        except ChainError as e:
            return None, str(e)
    rate = 1.0 if cur == "RUB" else fx.get(cur)
    return (price * rate, label) if rate else (None, f"нет курса {cur}")


def contract_price(db: Session, part: Part, client_id: str, on: date) -> Optional[tuple[float, str]]:
    for cp in part.prices:
        if cp.client_id == client_id and (not cp.valid_from or cp.valid_from <= on) and (not cp.valid_to or cp.valid_to >= on):
            return cp.price, cp.currency
    return None


def supplier_currency_of(db: Session, supplier_id: Optional[str]) -> str:
    f = db.get(Factory, supplier_id) if supplier_id else None
    return supplier_currency(f.name, f.short_name, f.country, f.default_currency) if f else "RUB"


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
        if not line.supplier_id and part.supplier_id:      # поставщик по умолчанию — из карточки; меняется выбором ответа на запрос
            line.supplier_id = part.supplier_id
            if line.price is None and line.currency in (None, "", "RUB"):
                line.currency = supplier_currency_of(db, line.supplier_id)
        quote = (line.quote_price, line.quote_currency, line.supplier_id) if line.quote_price else None
        cost, cost_note = unit_cost_rub(db, part, line.route_id, rules, fx, quote)
        manual = (line.price, line.currency) if line.price_source == "manual" and line.price else None
        pricelist = (part.sale_price, part.sale_currency or "EUR") if part.sale_price else None
        res = price_line(qty=line.qty, unit_cost_rub=cost, fx=fx, manual=manual, contract=contract_price(db, part, req.client_id, today),
                         pricelist=pricelist, bands=bands, cd_tolerance_pp=tol)
        line.price, line.price_source = res["price"], res["price_source"]
        line.currency = res["currency"] if res["price"] is not None else (line.currency or "RUB")   # без цены остаётся выбранная валюта
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


# ------------------------------------------------------------------ заказ клиента из файла (D-171)

_ART = ("каталожн", "артикул", "кат. №", "кат.№", "номер детали", "part no", "part number", "part#", "p/n", "pn", "oem", "код", "обозначение", "номер")
_NAME = ("наименование", "название", "описание", "description", "name", "деталь", "товар", "позиция")
_QTY = ("кол-во", "колич", "кол.", "qty", "quantity", "шт", "количество")
_UNIT = ("ед.", "ед ", "единиц", "unit", "uom", "изм")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _num(v) -> Optional[float]:
    s = _cell(v).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        x = float(s)
        return x if x > 0 else None
    except ValueError:
        return None


def _find_col(header: list[str], keys: tuple[str, ...], taken: set[int]) -> Optional[int]:
    for i, h in enumerate(header):
        low = h.lower()
        if i not in taken and low and any(k in low for k in keys):
            return i
    return None


def read_order_rows(path: str, filename: str) -> list[list]:
    """Строки файла заказа клиента: Excel (xlsx/xlsm) или CSV/TXT (разделитель — ; , или Tab)."""
    import csv
    ext = os.path.splitext(filename or path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets:            # первый лист с данными
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if any(any(_cell(c) for c in r) for r in rows):
                break
        return rows
    if ext in (".csv", ".txt"):
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=";,\t")
            delim = dialect.delimiter
        except csv.Error:
            delim = ";" if text.count(";") >= text.count(",") else ","
        return [r for r in csv.reader(text.splitlines(), delimiter=delim)]
    raise ValueError("Поддерживаются файлы Excel (.xlsx) и CSV. Старый .xls откройте в Excel и сохраните как .xlsx; PDF и скан — вставьте строки текстом")


def parse_order_file(path: str, filename: str) -> dict:
    """Заказ клиента → строки заявки. Заголовок ищется в первых 20 строках по словам «артикул / каталожный номер»,
    «наименование», «количество», «ед.»; без заголовка — первая колонка номер, последняя числовая — количество."""
    numbered = [(i + 1, [_cell(c) for c in r]) for i, r in enumerate(read_order_rows(path, filename))]
    numbered = [(n, r) for n, r in numbered if any(r)]      # номер строки в файле — для сообщений
    rows = [r for _, r in numbered]
    if not rows:
        raise ValueError("Файл пустой")
    head_i, cols = None, {}
    for i, r in enumerate(rows[:20]):
        taken: set[int] = set()
        a = _find_col(r, _ART, taken)
        if a is not None:
            taken.add(a)
        n = _find_col(r, _NAME, taken)
        if n is not None:
            taken.add(n)
        q = _find_col(r, _QTY, taken)
        if q is not None:
            taken.add(q)
        u = _find_col(r, _UNIT, taken)
        if (a is not None or n is not None) and q is not None:
            head_i, cols = i, {"article": a, "name": n, "qty": q, "unit": u}
            break
    body = rows[head_i + 1:] if head_i is not None else rows
    if head_i is None:      # без заголовка: номер — первая колонка, количество — последняя числовая
        width = max(len(r) for r in body)
        qcol = next((j for j in range(width - 1, 0, -1) if sum(1 for r in body if j < len(r) and _num(r[j])) >= max(1, len(body) // 2)), None)
        cols = {"article": 0, "name": 1 if width > 2 or (width == 2 and qcol != 1) else None, "qty": qcol, "unit": None}
    out, skipped = [], []
    g = lambda r, k: r[cols[k]] if cols.get(k) is not None and cols[k] < len(r) else ""   # noqa: E731
    start = (head_i + 1) if head_i is not None else 0
    for j, r in enumerate(body):
        i = numbered[start + j][0]
        art, name = g(r, "article"), g(r, "name")
        if not art and not name:
            continue
        low = f"{art} {name}".lower()
        if any(w in low for w in ("итого", "всего", "total")):
            continue
        qty = _num(g(r, "qty")) if cols.get("qty") is not None else 1.0
        if qty is None:
            skipped.append({"row": i, "article": art, "name": name, "reason": "нет количества"})
            qty = 1.0
        out.append({"raw_article": art, "raw_name": name if name != art else "", "qty": qty, "unit": g(r, "unit") or "шт", "row": i})
    if not out:
        raise ValueError("Не нашли строк с номером или наименованием детали — проверьте, что в файле есть колонки «Артикул / каталожный номер» и «Количество»")
    header = rows[head_i] if head_i is not None else []
    return {"lines": out, "columns": {k: (header[v] if header and v is not None and v < len(header) else (f"колонка {v + 1}" if v is not None else None)) for k, v in cols.items()},
            "header_row": numbered[head_i][0] if head_i is not None else None, "skipped": skipped}


def preview_lines(db: Session, lines: list[dict]) -> list[dict]:
    """Подбор по каталогу без сохранения: какая карточка найдена, остаток на складе, кандидаты при неоднозначности."""
    idx = Index(db)
    out = []
    for l in lines:
        p = db.get(Part, l["part_id"]) if l.get("part_id") else None
        kind, cands = ("catalog" if p else ""), []
        if p is None and (l.get("raw_article") or "").strip():
            pid, kind, cands = idx.match(l["raw_article"])
            p = db.get(Part, pid) if pid else None
        by_wh = stock_by_warehouse(db, p) if p else []
        stock = round(sum(w["available"] for w in by_wh), 3) if p else None
        qty = float(l.get("qty") or 0)
        if p is not None:     # строка из каталога: номер и наименование — из карточки
            l = {**l, "raw_article": l.get("raw_article") or p.article, "raw_name": l.get("raw_name") or p.name_ru,
                 "unit": l.get("unit") if l.get("unit") not in (None, "", "шт") else (p.unit or "шт")}
        out.append({**l, "part": {"id": p.id, "article": p.article, "name_ru": p.name_ru, "unit": p.unit, "manufacturer": p.manufacturer, "available": stock,
                                  "warehouses": by_wh} if p else None,
                    "stock_state": None if p is None else ("full" if stock >= qty > 0 else "partial" if stock > 0 else "none"),
                    "match_kind": kind or "", "candidates": (cands or [])[:5]})
    return out



def stock_by_warehouse(db: Session, part: Part) -> list[dict]:
    """Свободный остаток по складам (количество минус резерв), только склады с остатком или резервом."""
    from ..models import Warehouse
    out = []
    for st in part.stock:
        if (st.qty or 0) == 0 and (st.reserved or 0) == 0:
            continue
        w = db.get(Warehouse, st.warehouse_id)
        out.append({"warehouse_id": st.warehouse_id, "warehouse": w.name if w else "", "qty": st.qty or 0, "reserved": st.reserved or 0,
                    "available": round((st.qty or 0) - (st.reserved or 0), 3)})
    return sorted(out, key=lambda x: -x["available"])
