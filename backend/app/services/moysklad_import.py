"""Перенос каталога запчастей из МойСклад (ТЗ запчастей, раздел 4).

Источник — штатный экспорт МойСклад «Товары (Excel)»: одна строка на товар, столбцы по названию.
Разбор (parse_*) — чистые функции без БД, проверяются тестами; применение к БД — apply_products / apply_stock.

Что переносится из «Товары (Excel)»:
    UUID → ms_id, Код → ms_code, Артикул, Наименование, Единица измерения (пусто → «шт»), Архивный → статус,
    Описание, Цена продажи, «Цена: <клиент>» → договорные цены клиента, Закупочная цена, Минимальная цена,
    Код ТН ВЭД, Страна, Вес, Объем, Изображение, «Неснижаемый остаток для склада <склад>» → минимальный остаток,
    доп. поля «Применимо на оборудовании», «Срок поставки, дней», «Спецификация, №», «Доп. соглашение, №».
Нулевые цены и веса МойСклад выгружает как «0,00» — они считаются незаполненными.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

CURRENCY_WORDS = {"евро": "EUR", "eur": "EUR", "руб": "RUB", "rub": "RUB", "рубль": "RUB", "юань": "CNY", "cny": "CNY",
                  "доллар": "USD", "usd": "USD", "сом": "KGS", "kgs": "KGS"}
SERVICE_PRICE_COLUMNS = {"Цена продажи"}               # «Цена: Цена продажи» — прайс, а не цена клиента
MIN_STOCK_PREFIX = "Неснижаемый остаток для склада "
VIRTUAL_WAREHOUSE_HINTS = ("бишкек",)                 # склад компании в Киргизии — виртуальный


def num(v: Any) -> Optional[float]:
    """«2 064,10» → 2064.1; пусто, «0,00» и нечисловое → None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) or None
    s = str(v).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f or None


def currency(v: Any) -> str:
    s = (str(v or "")).strip().lower()
    for word, code in CURRENCY_WORDS.items():
        if s.startswith(word):
            return code
    return s.upper()[:3] if len(s) == 3 else ""


def norm_name(s: str) -> str:
    """Название организации для сопоставления: без кавычек, регистра и лишних пробелов («ООО «Х»» = ООО "Х")."""
    return re.sub(r"\s+", " ", re.sub(r"[«»\"'“”„]", "", s or "")).strip().lower()


def _text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _yes(v: Any) -> bool:
    return _text(v).lower() in ("да", "yes", "true", "1")


def read_rows(path: str) -> list[tuple]:
    from openpyxl import load_workbook   # read_only даёт неверные размеры листа у выгрузки МойСклад
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(path, data_only=True)
    return list(wb.worksheets[0].iter_rows(values_only=True))


def parse_products(rows: list[tuple]) -> tuple[list[dict], dict]:
    """Строки листа «Товары (Excel)» → карточки и отчёт (сколько чего заполнено, дубли артикулов)."""
    if not rows:
        return [], {"error": "Файл пустой"}
    header = [_text(h) for h in rows[0]]
    ix = {h: i for i, h in enumerate(header) if h}
    if "Наименование" not in ix or "Артикул" not in ix:
        return [], {"error": "Это не выгрузка «Товары (Excel)» из МойСклад: нет столбцов «Наименование» и «Артикул»"}
    client_cols = [(h[len("Цена: "):], i) for h, i in ix.items() if h.startswith("Цена: ") and h[len("Цена: "):] not in SERVICE_PRICE_COLUMNS]
    min_cols = [(h[len(MIN_STOCK_PREFIX):], i) for h, i in ix.items() if h.startswith(MIN_STOCK_PREFIX)]
    g = lambda r, name: r[ix[name]] if name in ix and ix[name] < len(r) else None   # noqa: E731

    out: list[dict] = []
    filled: Counter = Counter()
    for r in rows[1:]:
        if not any(v not in (None, "") for v in r):
            continue
        name = _text(g(r, "Наименование"))
        if not name:
            continue
        unit = _text(g(r, "Единица измерения"))
        rec: dict[str, Any] = {
            "ms_id": _text(g(r, "UUID")) or None,
            "ms_code": _text(g(r, "Код")),
            "article": _text(g(r, "Артикул")),
            "name_ru": name,
            "unit": unit or "шт",
            "status": "archive" if _yes(g(r, "Архивный")) else "active",
            "description": _text(g(r, "Описание")),
            "sale_price": num(g(r, "Цена: Цена продажи")),
            "sale_currency": currency(g(r, "Валюта (Цена продажи)")),
            "purchase_price": num(g(r, "Закупочная цена")),
            "purchase_currency": currency(g(r, "Валюта (Закупочная цена)")),
            "min_price": num(g(r, "Минимальная цена")),
            "min_price_currency": currency(g(r, "Валюта (Минимальная цена)")),
            "hs_code": re.sub(r"\D", "", _text(g(r, "Код ТН ВЭД"))),
            "origin_country": _text(g(r, "Страна")),
            "weight_kg": num(g(r, "Вес")),
            "volume_m3": num(g(r, "Объем")),
            "photos": [p for p in [_text(g(r, "Изображение"))] if p],
            "lead_days": int(num(g(r, "Доп. поле: Срок поставки, дней")) or 0) or None,
            "spec_no": _text(g(r, "Доп. поле: Спецификация, №")),
            "addendum_no": _text(g(r, "Доп. поле: Доп. соглашение, №")),
            "applicability_text": _text(g(r, "Доп. поле: Применимо на оборудовании")),
            "client_prices": [],
            "min_stock": {},
            "source": "moysklad",
        }
        for client, i in client_cols:
            price = num(r[i]) if i < len(r) else None
            if price:
                rec["client_prices"].append({"client_name": client, "price": price,
                                             "currency": currency(g(r, f"Валюта ({client})")) or rec["sale_currency"] or "EUR"})
        for wh, i in min_cols:
            q = num(r[i]) if i < len(r) else None
            if q:
                rec["min_stock"][wh] = q
        for k in ("sale_price", "purchase_price", "min_price", "weight_kg", "volume_m3", "lead_days", "description", "hs_code", "origin_country",
                  "applicability_text", "spec_no"):
            if rec[k]:
                filled[k] += 1
        filled["unit_from_source"] += bool(unit)
        filled["client_prices"] += bool(rec["client_prices"])
        filled["photos"] += bool(rec["photos"])
        out.append(rec)
    arts = Counter(r["article"] for r in out if r["article"])
    report = {
        "rows": len(out),
        "filled": dict(filled),
        "no_article": sum(1 for r in out if not r["article"]),
        "duplicate_articles": sorted(a for a, n in arts.items() if n > 1),
        "clients": [c for c, _ in client_cols],
        "warehouses": [w for w, _ in min_cols],
        "currencies": dict(Counter(r["sale_currency"] for r in out)),
    }
    return out, report


def parse_stock(rows: list[tuple]) -> tuple[list[dict], dict]:
    """Отчёт МойСклад «Остатки по складам» (Excel): строка заголовка с «Артикул» и «Остаток», после «Остаток» — склады.

    Поддерживается и отчёт «Остатки по товарам»: столбцы «Остаток», «Резерв», «Ожидание» без разбивки по складам —
    тогда остаток записывается на склад, указанный при загрузке (default_warehouse)."""
    hdr_i = next((i for i, r in enumerate(rows[:30]) if r and "Артикул" in [_text(v) for v in r] and "Остаток" in [_text(v) for v in r]), None)
    if hdr_i is None:
        return [], {"error": "Не найдена строка заголовка с «Артикул» и «Остаток»"}
    header = [_text(v) for v in rows[hdr_i]]
    ia, iq = header.index("Артикул"), header.index("Остаток")
    known = {"Резерв", "Ожидание", "Доступно", "Ед. изм.", "Дней на складе", "Себестоимость", "Сумма себестоимости", "Цена продажи", "Сумма продажи",
             "Наименование", "Код", "Артикул", "Остаток"}
    wh_cols = [(h, i) for i, h in enumerate(header) if i > iq and h and h not in known]
    ir = header.index("Резерв") if "Резерв" in header else None
    iw = header.index("Ожидание") if "Ожидание" in header else None
    out: list[dict] = []
    for r in rows[hdr_i + 1:]:
        art = _text(r[ia]) if ia < len(r) else ""
        if not art:
            continue
        rec = {"article": art, "name": _text(r[header.index("Наименование")]) if "Наименование" in header else "", "total": num(r[iq]) or 0.0,
               "reserved": (num(r[ir]) or 0.0) if ir is not None else 0.0, "in_transit": (num(r[iw]) or 0.0) if iw is not None else 0.0,
               "by_warehouse": {}}
        for h, i in wh_cols:
            q = num(r[i]) if i < len(r) else None
            if q:
                rec["by_warehouse"][h] = q
        out.append(rec)
    return out, {"rows": len(out), "warehouses": [h for h, _ in wh_cols]}


# ------------------------------------------------------------------ применение к БД

def _warehouse(db, name: str, cache: dict) -> Any:
    from sqlalchemy import select
    from ..models import LegalEntity, Warehouse
    if name in cache:
        return cache[name]
    w = db.scalar(select(Warehouse).where(Warehouse.name == name))
    if w is None:
        virtual = any(h in name.lower() for h in VIRTUAL_WAREHOUSE_HINTS)
        ent = db.scalar(select(LegalEntity).where(LegalEntity.code == ("kg" if virtual else "ru")))
        w = Warehouse(name=name, kind="virtual" if virtual else "physical", legal_entity_id=ent.id if ent else None)
        db.add(w)
        db.flush()
    cache[name] = w
    return w


def apply_products(db, records: Iterable[dict], user_id: Optional[str] = None, dry_run: bool = False) -> dict:
    """Создаёт или обновляет карточки (по UUID МойСклад). Поля, заполненные в модуле вручную, пустыми значениями не затираются."""
    from sqlalchemy import func, select
    from ..models import Client, Part, PartApplicability, PartClientPrice, PartStock
    clients = {norm_name(c.name): c.id for c in db.scalars(select(Client)).all()}
    by_ms = {p.ms_id: p for p in db.scalars(select(Part).where(Part.ms_id.is_not(None))).all()}
    created = updated = 0
    unmatched_clients: Counter = Counter()
    wh_cache: dict = {}
    now = datetime.now(timezone.utc)
    simple = ("ms_code", "article", "name_ru", "unit", "status", "description", "sale_price", "sale_currency", "purchase_price", "purchase_currency",
              "min_price", "min_price_currency", "hs_code", "origin_country", "weight_kg", "volume_m3", "lead_days", "spec_no", "addendum_no", "photos")
    for rec in records:
        p = by_ms.get(rec["ms_id"]) if rec.get("ms_id") else None
        if p is None:
            p = Part(ms_id=rec.get("ms_id"), source="moysklad", created_by=user_id, article=rec["article"], name_ru=rec["name_ru"])
            if not dry_run:
                db.add(p)
            created += 1
        else:
            updated += 1
        for k in simple:
            v = rec.get(k)
            if v not in (None, "", []) or k in ("status", "unit"):
                setattr(p, k, v)
        if rec.get("applicability_text") and not any(a.other_model == rec["applicability_text"] for a in p.applicability):
            p.applicability.append(PartApplicability(other_model=rec["applicability_text"]))
        for cp in rec.get("client_prices") or []:
            cid = clients.get(norm_name(cp["client_name"]))
            if not cid:
                unmatched_clients[cp["client_name"]] += 1
            row = next((x for x in p.prices if (x.client_id and x.client_id == cid) or (not x.client_id and x.client_name == cp["client_name"])), None)
            if row is None:
                p.prices.append(PartClientPrice(client_id=cid, client_name=cp["client_name"], price=cp["price"], currency=cp["currency"]))
            else:
                row.price, row.currency = cp["price"], cp["currency"]
        for wh_name, q in (rec.get("min_stock") or {}).items():
            if dry_run:
                continue
            w = _warehouse(db, wh_name, wh_cache)
            st = next((s for s in p.stock if s.warehouse_id == w.id), None)
            if st is None:
                p.stock.append(PartStock(warehouse_id=w.id, min_qty=q, as_of=now))
            else:
                st.min_qty = q
    if dry_run:
        db.rollback()
    else:
        db.flush()
    total = db.scalar(select(func.count(Part.id)))
    return {"created": created, "updated": updated, "unmatched_clients": dict(unmatched_clients), "total_parts": total, "dry_run": dry_run}


def apply_stock(db, records: Iterable[dict], default_warehouse: Optional[str] = None) -> dict:
    """Записывает остатки по складам (снимок на момент переноса). Товары ищутся по артикулу; неоднозначные и ненайденные — в отчёт."""
    from sqlalchemy import select
    from ..models import Part, PartStock
    by_art: dict[str, list] = {}
    for p in db.scalars(select(Part)).all():
        by_art.setdefault(p.article, []).append(p)
    wh_cache: dict = {}
    now = datetime.now(timezone.utc)
    done, missing, ambiguous = 0, [], []
    for rec in records:
        parts = by_art.get(rec["article"]) or []
        if not parts:
            missing.append(rec["article"])
            continue
        if len(parts) > 1:
            ambiguous.append(rec["article"])
            continue
        p = parts[0]
        split = rec["by_warehouse"] or ({default_warehouse: rec["total"]} if default_warehouse else {})
        for wh_name, q in split.items():
            w = _warehouse(db, wh_name, wh_cache)
            st = next((s for s in p.stock if s.warehouse_id == w.id), None)
            if st is None:
                st = PartStock(warehouse_id=w.id)
                p.stock.append(st)
            st.qty, st.as_of = q, now
            if not rec["by_warehouse"]:
                st.reserved, st.in_transit = rec["reserved"], rec["in_transit"]
        done += 1
    db.flush()
    return {"updated": done, "missing_articles": missing[:200], "missing_count": len(missing), "ambiguous_articles": ambiguous}
