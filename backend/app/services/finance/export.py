"""Экспорт XLSX «Финансов» (docs/finance/01, раздел 12; 04 — GET /api/finance/export; FIN-SEC-009, FIN-SEC-012; D-145).

Файл строится из тех же read-сервисов и DTO, что и экран, после той же проверки прав: состав строк и колонок задаёт сервер, клиент передаёт
только фильтры. Лист «Пояснения» — дата формирования, пользователь, фильтры, курсы, правила расчёта. Текст, начинающийся с = + - @ (и табуляции
или возврата каретки), записывается с апострофом — Excel не исполнит его как формулу. Факт выгрузки — в журнале действий, без содержимого.
"""
from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

KINDS = {"obligations": "Платёжный календарь", "debt": "Дебиторка и кредиторка", "liquidity": "Ликвидность", "economy": "Экономика сделки", "reconciliation": "Сверка"}
MAX_ROWS = 20_000
_DANGER = ("=", "+", "-", "@", "\t", "\r")
HEAD = PatternFill("solid", fgColor="E30613")


def safe(v: Any) -> Any:
    """Значение для ячейки: числа — числами, строки-формулы — текстом (FIN-SEC-012)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    t = str(v)
    if t and t[0] in _DANGER:
        try:
            float(t.replace(" ", "").replace(",", "."))   # «-1500.00» — отрицательное число, не формула
            return float(t.replace(" ", "").replace(",", "."))
        except ValueError:
            return "'" + t
    return t


def num(v) -> Any:
    if v in (None, ""):
        return ""
    try:
        return float(Decimal(str(v)))
    except Exception:
        return safe(v)


class Book:
    def __init__(self):
        self.wb = Workbook()
        self.wb.remove(self.wb.active)

    def sheet(self, title: str, columns: list[tuple[str, Callable[[dict], Any], int]], rows: list[dict]) -> None:
        ws = self.wb.create_sheet(title[:31])
        ws.append([c[0] for c in columns])
        for cell in ws[1]:
            cell.font, cell.fill, cell.alignment = Font(bold=True, color="FFFFFF"), HEAD, Alignment(wrap_text=True, vertical="top")
        for r in rows[:MAX_ROWS]:
            ws.append([safe(f(r)) for _, f, _ in columns])
        for i, (_, _, w) in enumerate(columns, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    def notes(self, kind: str, user, filters: dict, lines: list[str], rates: list[dict], truncated: bool) -> None:
        ws = self.wb.create_sheet("Пояснения")
        ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 34, 100
        rows = [("Отчёт", KINDS[kind]), ("Сформирован", datetime.now().strftime("%d.%m.%Y %H:%M")), ("Пользователь", user.display_name)]
        rows += [(f"Фильтр: {k}", v) for k, v in filters.items() if v not in (None, "", False)]
        if truncated:
            rows.append(("Ограничение", f"выгружено первых {MAX_ROWS} строк — сузьте фильтр"))
        rows += [("Курс " + (r.get("currency") or ""), f"{r.get('rate')} RUB на {r.get('on')} ({r.get('source', '')})") for r in rates if r]
        rows += [("Правило", x) for x in lines]
        rows.append(("Права", "состав строк и колонок — по вашим правам в CRM; скрытые объекты и поля в файл не попадают"))
        for a, b in rows:
            ws.append([safe(a), safe(b)])
        for c in ws["A"]:
            c.font = Font(bold=True)

    def bytes(self) -> bytes:
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()


def _ob_cols() -> list:
    return [("Срок по договору", lambda r: r["legal_due_on"] or r["legal_due_text"], 16), ("Ожидаемая дата", lambda r: r["forecast_on"], 14),
            ("Направление", lambda r: "нам платят" if r["direction"] == "in" else "мы платим", 12), ("Строка графика", lambda r: r["title"], 46),
            ("Контрагент", lambda r: r["counterparty"]["name"], 30), ("Сделка", lambda r: (r.get("deal") or {}).get("number"), 14),
            ("Договор", lambda r: (r.get("contract") or {}).get("number"), 16), ("Источник", lambda r: r["source"]["type_title"], 18),
            ("Сумма", lambda r: num(r["amount"]), 16), ("Валюта", lambda r: r["currency"], 8), ("Оплачено (подтверждено)", lambda r: num(r["confirmed_paid"]), 16),
            ("Временно разнесено", lambda r: num(r["provisional_paid"]), 16), ("Остаток", lambda r: num(r["remaining"]), 16),
            ("Статус", lambda r: r["status_title"], 18), ("Дней просрочки", lambda r: r["days_overdue"], 10),
            ("Требует сверки", lambda r: "needs_reconciliation" in r["flags"], 10), ("Конфликт с источником", lambda r: "conflict" in r["flags"], 10)]


def build(db, user, vis, kind: str, params: dict) -> tuple[bytes, str, int, Optional[str]]:
    """→ (файл, имя файла, число строк, deal_id для журнала)."""
    from . import debt, liquidity, obligations as obl, reconciliation
    b = Book()
    today = params.get("as_of") or date.today()
    deal_id = None
    if kind == "obligations":
        f = obl.ObligationFilter(as_of=today, direction=params.get("direction"), currency=params.get("currency"), legal_entity_id=params.get("legal_entity_id"),
                                 deal_id=params.get("deal_id"), contract_id=params.get("contract_id"), source_type=params.get("source_type"), status=params.get("status"),
                                 date_from=params.get("date_from"), date_to=params.get("date_to"), search=params.get("search") or "",
                                 include_inactive=bool(params.get("include_inactive")), reconciliation=bool(params.get("reconciliation")), limit=MAX_ROWS + 1)
        res = obl.query(db, vis, f)
        rows = res["items"]
        b.sheet("Календарь", _ob_cols(), rows)
        b.notes(kind, user, {**{k: v for k, v in params.items() if k != "as_of"}, "дата отчёта": today.isoformat()},
                ["Остаток = сумма − подтверждённые разнесения; временные разнесения показаны отдельно.", "Просрочка — от юридического срока."], [], len(rows) > MAX_ROWS)
        n = min(len(rows), MAX_ROWS)
        name = f"finance-calendar-{today:%Y%m%d}.xlsx"
    elif kind == "debt":
        res = debt.build(db, user, vis, today, params.get("direction"), params.get("group_by") or "counterparty", params.get("currency"), params.get("legal_entity_id"))
        rows = [{**r, "group": g["title"], "bucket_title": next(x["title"] for x in res["buckets"] if x["code"] == r["bucket"])} for g in res["groups"] for r in g["rows"]]
        cols = [("Группа", lambda r: r["group"], 30), *_ob_cols()[:1], ("Ageing", lambda r: r["bucket_title"], 14), *_ob_cols()[2:]]
        b.sheet("Задолженность", cols, rows)
        b.sheet("Итоги по валютам", [("Направление", lambda r: r["dir"], 14), ("Валюта", lambda r: r["cur"], 8), ("Сумма", lambda r: num(r["amount"]), 16),
                                     ("Оплачено", lambda r: num(r["confirmed"]), 16), ("Временно", lambda r: num(r["provisional"]), 16), ("Остаток", lambda r: num(r["remaining"]), 16),
                                     *[(x["title"], (lambda code: (lambda r: num(r[code])))(x["code"]), 14) for x in res["buckets"]]],
                [{"dir": "нам должны" if d == "in" else "мы должны", "cur": c, **v} for d, cur in res["totals"].items() for c, v in cur.items()])
        b.sheet("Без даты", [("Строка", lambda r: r["title"], 46), ("Контрагент", lambda r: r["counterparty"]["name"], 30), ("Остаток", lambda r: num(r["remaining"]), 16),
                             ("Валюта", lambda r: r["currency"], 8), ("Почему без даты", lambda r: r["legal_due_text"], 40)], res["undated"])
        b.notes(kind, user, {"направление": params.get("direction") or "все", "группировка": params.get("group_by") or "counterparty", "дата отчёта": today.isoformat()},
                res["notes"], list(res["rates"].values()), len(rows) > MAX_ROWS)
        n = len(rows)
        name = f"finance-debt-{today:%Y%m%d}.xlsx"
    elif kind == "liquidity":
        res = liquidity.build(db, vis, today, int(params.get("horizon_days") or 90), params.get("group_by") or "week", params.get("currency"),
                              exclude_intragroup=str(params.get("exclude_intragroup") or "").lower() in ("1", "true", "yes"))
        rows = [{"currency": c["currency"], "status": c["balance_status"], **p} for c in res["currencies"] for p in c["points"]]
        b.sheet("Прогноз остатков", [("Валюта", lambda r: r["currency"], 8), ("Дата", lambda r: r["on"], 12), ("Поступления", lambda r: num(r["inflow"]), 16),
                                     ("Списания", lambda r: num(r["outflow"]), 16), ("Остаток", lambda r: num(r["balance"]) if r["balance"] is not None else "неизвестен", 16),
                                     ("Входящий остаток", lambda r: {"confirmed": "подтверждён", "partial": "частично", "unknown": "не подтверждён"}.get(r["status"], r["status"]), 16),
                                     ("Есть просроченное", lambda r: r["late_in_point"], 10), ("Строк", lambda r: r["count"], 8)], rows)
        b.sheet("Валюты", [("Валюта", lambda r: r["currency"], 8), ("Входящий остаток", lambda r: num(r["opening_balance"]), 16), ("Минимум", lambda r: num(r["minimum"]), 16),
                           ("Дата минимума", lambda r: r["minimum_on"], 12), ("Разрывов", lambda r: len(r["cash_gaps"]), 8),
                           ("Без даты", lambda r: num(r["undated"].get("in")) if r["undated"] else "", 14), ("Требует сверки (шт.)", lambda r: r["needs_reconciliation"].get("count"), 10)],
                res["currencies"])
        b.notes(kind, user, {"горизонт, дней": params.get("horizon_days") or 90, "шаг": params.get("group_by") or "week", "валюта": params.get("currency"), "дата отчёта": today.isoformat()},
                [res["formula"], res["rub"]["note"]], [c["fx"] for c in res["currencies"] if c["fx"] and c["currency"] != "RUB"], False)
        n = len(rows)
        name = f"finance-liquidity-{today:%Y%m%d}.xlsx"
    elif kind == "economy":
        from fastapi import HTTPException
        from ...api.common import get_deal_or_404
        from . import economy
        if not params.get("deal_id"):
            raise HTTPException(422, "Для экономики укажите deal_id")
        deal = get_deal_or_404(db, params["deal_id"], user)
        deal_id = deal.id
        e = economy.build(db, vis, deal, today)
        b.sheet("Статьи", [("№", lambda r: r["no"], 5), ("Статья", lambda r: r["title"], 40), ("План, базовый", lambda r: num(r["plan"].get("base")), 16),
                           ("План, пессим.", lambda r: num(r["plan"].get("pessimistic")), 16), ("План, оптим.", lambda r: num(r["plan"].get("optimistic")), 16),
                           ("Прогноз", lambda r: num(r["forecast"]), 16), ("Признано", lambda r: num(r["fact"]), 16), ("Отклонение, ₽", lambda r: num(r["deviation_rub"]), 16),
                           ("Отклонение, %", lambda r: num(r["deviation_pct"]), 12)], e["items"])
        p = e["plan"]["base"] or {}
        kp = [{"k": "Выручка", "plan": p.get("revenue"), "fc": e["forecast"]["revenue"], "fact": e["pnl_fact"]["revenue"]},
              {"k": "Себестоимость", "plan": p.get("cost"), "fc": e["forecast"]["cost"], "fact": e["pnl_fact"]["cost"]},
              {"k": "Прибыль", "plan": p.get("profit"), "fc": e["forecast"]["profit"], "fact": e["pnl_fact"]["profit"]},
              {"k": "Маржа, %", "plan": p.get("margin_pct"), "fc": e["forecast"]["margin_pct"], "fact": e["pnl_fact"]["margin_pct"]},
              {"k": "Кассовый разрыв", "plan": (e["cash_gap"]["plan_base"] or {}).get("amount_rub"), "fc": (e["cash_gap"]["forecast"] or {}).get("amount_rub"),
               "fact": (e["cash_gap"]["fact"] or {}).get("amount_rub")},
              {"k": "Поступило (деньги)", "plan": None, "fc": None, "fact": e["cash_fact"]["in_rub"]}, {"k": "Оплачено (деньги)", "plan": None, "fc": None, "fact": e["cash_fact"]["out_rub"]}]
        b.sheet("Итоги", [("Показатель", lambda r: r["k"], 26), ("План (базовый)", lambda r: num(r["plan"]), 18), ("Текущий прогноз", lambda r: num(r["fc"]), 18),
                          ("Факт", lambda r: num(r["fact"]), 18)], kp)
        b.sheet("Чего не хватает", [("Код", lambda r: r["code"], 22), ("Пояснение", lambda r: r["text"], 100)], e["data_gaps"])
        b.notes(kind, user, {"сделка": deal.number, "вариант": e["variant"]["name"], "утверждённая версия": e["version_no"], "дата отчёта": today.isoformat()},
                ["План — утверждённая версия, не пересчитывается.", "Факт P&L — проведённые события признания; поступления денег выручкой не считаются.",
                 "Факт денег — проведённые операции с подтверждённым разнесением на обязательства сделки."], [], False)
        n = len(e["items"])
        name = f"finance-economy-{deal.number}-{today:%Y%m%d}.xlsx"
    elif kind == "reconciliation":
        res = reconciliation.build(db, user, vis, today, params.get("queue"), params.get("deal_id"))
        qt = dict(reconciliation.QUEUES)
        b.sheet("Сверка", [("Очередь", lambda r: qt[r["queue"]], 22), ("Важность", lambda r: res["severity_titles"][r["severity"]], 12), ("Объект", lambda r: r["object"]["title"], 40),
                           ("Сделка", lambda r: (r.get("deal") or {}).get("number"), 14), ("Что не так", lambda r: r["problem"], 70), ("Сумма", lambda r: num(r["amount"]), 16),
                           ("Валюта", lambda r: r["currency"], 8), ("Дата", lambda r: r["on"], 12), ("Ответственный", lambda r: r["responsible"], 24),
                           ("Что сделать", lambda r: r["action"]["title"], 24)], res["items"])
        b.notes(kind, user, {"очередь": params.get("queue") or "все", "дата отчёта": today.isoformat()}, ["Сверка только показывает расхождения; исправление — в источнике."], [], False)
        n = len(res["items"])
        name = f"finance-reconciliation-{today:%Y%m%d}.xlsx"
    else:
        from fastapi import HTTPException
        raise HTTPException(422, "kind: " + ", ".join(KINDS))
    return b.bytes(), name, n, deal_id
