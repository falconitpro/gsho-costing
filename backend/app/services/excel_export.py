"""Итоговый Excel-расчёт (раздел 13 ТЗ).

Листы: «Сводка», «Себестоимость», «Логистика и партии», «Сервис», «Платежи и денежный поток», «Курсы»,
«Исходные данные RFQ», «ЗИП на ТО», «История и справки», «Визы».
Формулы сохраняются живыми: итоги по статьям, себестоимость, маржа, наценка и прибыль на «Сводке» —
формулы, ссылающиеся на «Себестоимость», чтобы ФД мог проверить расчёт в Excel.
Версия менеджера — без листов «Сводка», «Себестоимость», «Платежи и денежный поток» (и «Курсы»).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..calc.types import COST_ITEMS, EVENT_TITLES, DealCalcInput, DealCalcResult

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")
EVENT_RU = {e.value: t for e, t in EVENT_TITLES.items()}
GUARANTEE_RU = {"none": "нет", "advance_return": "возврат аванса", "performance": "исполнение обязательств"}
ALLOC_RU = {"cost": "по стоимости", "weight": "по весу", "volume": "по объёму"}
FIN_RU = {"money_cost": "стоимость денег", "conversion_fees": "конвертация", "transfer_fees": "комиссии за переводы",
          "guarantees": "банковские гарантии", "deposit_income": "доход от размещения"}
FX_SOURCE_RU = {"cbr": "ЦБ РФ", "cbr_stale": "ЦБ РФ, последний загруженный", "manual": "введён вручную"}
ITEM_RU = {k: t for _, k, t in COST_ITEMS} | {"freight": "доставка до границы РФ", "door_to_door": "доставка от границы до клиента",
                                               "ins": "страхование", "таможенная стоимость": "таможенная стоимость (пошлина, НДС)",
                                               "_base": "пересчёт в базовую валюту", "broker_rub": "брокер и СВХ", "rf_delivery_rub": "доставка по РФ",
                                               "certification_rub": "сертификация"}


def ru_only(v):
    """Текст без китайских надписей: «Общие данные / 总体参数» → «Общие данные»."""
    if not isinstance(v, str) or not _CJK.search(v):
        return v
    parts = [p for p in re.split(r"\s*[/|]\s*", v) if p and not _CJK.search(p)]
    return _CJK.sub("", " / ".join(parts)).strip()

ACCENT = "FF3131"
HDR_FONT = Font(bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor=ACCENT)
BOLD = Font(bold=True)
THIN = Side(style="thin", color="BBBBBB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
NUM = "#,##0.00"
NUM0 = "#,##0"
PCT = "0.00"
SCEN_TITLES = {"pessimistic": "Пессимистичный", "base": "Базовый", "optimistic": "Оптимистичный"}
SCEN_ORDER = ["pessimistic", "base", "optimistic"]

MANAGER_HIDDEN_SHEETS = {"Сводка", "Себестоимость", "Платежи и денежный поток", "Курсы"}


def _hdr(ws, row: int, values: list[str], col: int = 1) -> None:
    for i, v in enumerate(values):
        c = ws.cell(row, col + i, v)
        c.font, c.fill, c.border = HDR_FONT, HDR_FILL, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _widths(ws, widths: dict[int, float]) -> None:
    for i, w in widths.items():
        ws.column_dimensions[get_column_letter(i)].width = w


class ExcelExporter:
    def __init__(self, inp: DealCalcInput, res: DealCalcResult, meta: Optional[dict[str, Any]] = None,
                 manager_version: bool = False):
        self.inp, self.res = inp, res
        self.meta = meta or {}
        self.manager = manager_version
        self.wb = Workbook()
        self.wb.remove(self.wb.active)
        # адреса ячеек итогов себестоимости: (variant_id, scenario) → "Себестоимость!$X$n"
        self.cost_total_ref: dict[tuple[str, str], str] = {}
        self.item_ref: dict[tuple[str, str, str], str] = {}

    def _n(self, key: Optional[str]) -> str:
        """Название позиции / партии / завода по идентификатору."""
        return (self.meta.get("names") or {}).get(key or "", key or "")

    def _ref(self, ref: str) -> str:
        """Платёж из листа «Курсы»: «<позиция>/завод №1» → «SHILI FL-0.5(C): оплата заводу, платёж 1»."""
        if "/" not in ref:
            return ITEM_RU.get(ref, ref)
        key, rest = ref.split("/", 1)
        what = f"оплата заводу, платёж {rest[7:]}" if rest.startswith("завод №") else ITEM_RU.get(rest, rest)
        return f"{self._n(key)}: {what}"

    # ------------------------------------------------------------------ публичный API
    def build(self, path: str) -> str:
        if not self.manager:
            self._sheet_cost()
            self._sheet_summary()
            self.wb.move_sheet("Сводка", offset=-1)
        self._sheet_logistics()
        self._sheet_service()
        if not self.manager:
            self._sheet_payments()
            self._sheet_fx()
        self._sheet_rfq()
        self._sheet_spare_parts()
        self._sheet_history()
        self._sheet_visas()
        for ws in self.wb.worksheets:   # нигде не оставляем китайских надписей
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and not c.value.startswith("=") and _CJK.search(c.value):
                        c.value = ru_only(c.value)
        self.wb.save(path)
        return path

    # ------------------------------------------------------------------ «Себестоимость»
    def _sheet_cost(self) -> None:
        ws = self.wb.create_sheet("Себестоимость")
        ws["A1"] = f"Себестоимость по позициям — сделка {self.inp.deal_no}, расчёт от {self.inp.calc_date:%d.%m.%Y}"
        ws["A1"].font = Font(bold=True, size=13)
        ws["A2"] = f"Курс ЦБ РФ на дату расчёта ({FX_SOURCE_RU.get(self.inp.fx.source, self.inp.fx.source)}): " + ", ".join(
            f"{c} {r:.4f}" for c, r in self.inp.fx.rates.items() if c != "RUB")
        row = 4
        for v in self.inp.variants:
            ws.cell(row, 1, f"Предложение: {v.name}").font = Font(bold=True, size=12)
            row += 1
            for scen in SCEN_ORDER:
                r = self.res.variants[v.id][scen]
                ws.cell(row, 1, f"Сценарий: {SCEN_TITLES[scen]}").font = BOLD
                row += 1
                pos_ids = [p.position_id for p in r.positions]
                _hdr(ws, row, ["№", "Статья", "Ед."] + [self._n(pid) for pid in pos_ids] + ["Итого, RUB"])
                row += 1
                first_item_row = row
                for no, key, title in COST_ITEMS:
                    ws.cell(row, 1, no)
                    ws.cell(row, 2, title)
                    ws.cell(row, 3, "RUB")
                    for j, p in enumerate(r.positions):
                        c = ws.cell(row, 4 + j, p.items_rub.get(key, 0.0))
                        c.number_format = NUM
                    last_col = 3 + len(pos_ids)
                    tot = ws.cell(row, last_col + 1, f"=SUM({get_column_letter(4)}{row}:{get_column_letter(last_col)}{row})")
                    tot.number_format = NUM
                    self.item_ref[(v.id, scen, key)] = f"'Себестоимость'!${get_column_letter(last_col + 1)}${row}"
                    row += 1
                last_col = 3 + len(pos_ids)
                ws.cell(row, 2, "Себестоимость, RUB").font = BOLD
                for j in range(len(pos_ids)):
                    col = get_column_letter(4 + j)
                    c = ws.cell(row, 4 + j, f"=SUM({col}{first_item_row}:{col}{row - 1})")
                    c.number_format, c.font = NUM, BOLD
                col = get_column_letter(last_col + 1)
                c = ws.cell(row, last_col + 1, f"=SUM({col}{first_item_row}:{col}{row - 1})")
                c.number_format, c.font = NUM, BOLD
                self.cost_total_ref[(v.id, scen)] = f"'Себестоимость'!${col}${row}"
                row += 1
                ws.cell(row, 2, "Таможенная стоимость, RUB")
                for j, p in enumerate(r.positions):
                    ws.cell(row, 4 + j, p.customs_value_rub).number_format = NUM
                row += 2
        _widths(ws, {1: 5, 2: 42, 3: 8, 4: 22, 5: 22, 6: 22, 7: 22, 8: 22})

    # ------------------------------------------------------------------ «Сводка»
    def _sheet_summary(self) -> None:
        ws = self.wb.create_sheet("Сводка")
        ws["A1"] = f"Сводка по сделке {self.inp.deal_no}"
        ws["A1"].font = Font(bold=True, size=14, color=ACCENT)
        ws["A2"] = f"Дата расчёта: {self.inp.calc_date:%d.%m.%Y}; базовая валюта: {self.inp.base_currency}; порог маржи: {self.inp.thresholds.margin_threshold_pct} %"
        ws["A3"] = "Маржа считается от выручки без НДС (M% = (P−C)/P); наценка от себестоимости — справочно."
        row = 5
        for v in self.inp.variants:
            ws.cell(row, 1, f"Вариант: {v.name}").font = Font(bold=True, size=12)
            row += 1
            _hdr(ws, row, ["Показатель"] + [SCEN_TITLES[s] for s in SCEN_ORDER])
            row += 1
            base = self.res.variants[v.id]["base"]
            price_row = row
            ws.cell(row, 1, "Цена продажи без НДС, RUB")
            for j, s in enumerate(SCEN_ORDER):
                ws.cell(row, 2 + j, base.price_rub or 0).number_format = NUM
            row += 1
            cost_row = row
            ws.cell(row, 1, "Себестоимость, RUB")
            for j, s in enumerate(SCEN_ORDER):
                ws.cell(row, 2 + j, f"={self.cost_total_ref[(v.id, s)]}").number_format = NUM
            row += 1
            ws.cell(row, 1, "Прибыль, RUB")
            for j in range(3):
                col = get_column_letter(2 + j)
                ws.cell(row, 2 + j, f"={col}{price_row}-{col}{cost_row}").number_format = NUM
            row += 1
            ws.cell(row, 1, "Маржа от выручки, %")
            for j in range(3):
                col = get_column_letter(2 + j)
                c = ws.cell(row, 2 + j, f"=IF({col}{price_row}=0,\"\",({col}{price_row}-{col}{cost_row})/{col}{price_row}*100)")
                c.number_format = PCT
            row += 1
            ws.cell(row, 1, "Наценка от себестоимости, % (справочно)")
            for j in range(3):
                col = get_column_letter(2 + j)
                ws.cell(row, 2 + j, f"=IF({col}{cost_row}=0,\"\",({col}{price_row}-{col}{cost_row})/{col}{cost_row}*100)").number_format = PCT
            row += 1
            ws.cell(row, 1, "Прибыль с учётом стоимости денег, RUB (чистая прибыльность)")
            for j, s in enumerate(SCEN_ORDER):
                ws.cell(row, 2 + j, self.res.variants[v.id][s].profit_rub or 0).number_format = NUM
            row += 1
            ws.cell(row, 1, "Кассовый разрыв, RUB")
            for j, s in enumerate(SCEN_ORDER):
                ws.cell(row, 2 + j, self.res.variants[v.id][s].cash_gap.amount_rub).number_format = NUM
            row += 1
            ws.cell(row, 1, "Кассовый разрыв, дней / дата")
            for j, s in enumerate(SCEN_ORDER):
                g = self.res.variants[v.id][s].cash_gap
                ws.cell(row, 2 + j, f"{g.days} / {g.starts_on:%d.%m.%Y}" if g.starts_on else "—")
            row += 1
            ws.cell(row, 1, "Срок поставки от договора, дней")
            for j, s in enumerate(SCEN_ORDER):
                ws.cell(row, 2 + j, self.res.variants[v.id][s].delivery_days)
            row += 1
            # задача 3: срок поставки клиенту по позициям (с резервом и округлением до недели), задача 1: БГ на аванс и типовые схемы
            for pc in base.positions:
                if pc.delivery_term:
                    dt = pc.delivery_term
                    ws.cell(row, 1, f"Срок клиенту, {pc.position_id}: расчёт {dt.calc_days} → +{dt.reserve_pct:g} % = {dt.with_reserve} → {dt.text}")
                    row += 1
            ws.cell(row, 1, "Банковская гарантия на аванс, RUB")
            for j, s in enumerate(SCEN_ORDER):
                ws.cell(row, 2 + j, self.res.variants[v.id][s].guarantee_cost_rub).number_format = NUM
            row += 1
            if base.defaults_used.get("client_payments") or base.defaults_used.get("factory_payments"):
                ws.cell(row, 1, "Внимание: применена типовая схема оплаты (" + ", ".join(k for k, used in base.defaults_used.items() if used) + ") — условия КД не заданы")
                row += 1
            if base.sensitivity:
                sn = base.sensitivity
                ws.cell(row, 1, "Чувствительность: Δ маржи при курсе +1 %, п.п. / RUB")
                ws.cell(row, 2, sn.margin_delta_pct_per_1pct_fx).number_format = "0.000"
                ws.cell(row, 3, sn.margin_delta_rub_per_1pct_fx).number_format = NUM
                row += 1
                ws.cell(row, 1, f"Курс безубыточности по порогу, RUB за {self.inp.base_currency}")
                ws.cell(row, 2, sn.breakeven_rate_base_ccy).number_format = "0.0000"
                row += 1
            row += 1
        if self.res.approval.reasons or self.res.warnings:
            ws.cell(row, 1, "Визы и предупреждения").font = BOLD
            row += 1
            for t in self.res.approval.reasons + self.res.warnings:
                ws.cell(row, 1, t)
                row += 1
        _widths(ws, {1: 58, 2: 20, 3: 20, 4: 20})

    # ------------------------------------------------------------------ «Логистика и партии»
    def _sheet_logistics(self) -> None:
        ws = self.wb.create_sheet("Логистика и партии")
        ws["A1"] = "Партии, маршруты и статьи логиста"
        ws["A1"].font = Font(bold=True, size=13)
        row = 3
        _hdr(ws, row, ["Партия", "Маршрут", "Транспортных единиц", "Транзит, дн", "Оформление, дн", "Доставка до границы РФ",
                       "Валюта", "Страхование, %", "Брокер и СВХ, RUB", "Доставка по РФ", "Распределение общих статей"])
        row += 1
        for b in self.inp.batches:
            d2d = b.door_to_door if b.door_to_door and b.door_to_door.amount else None
            fr_amount, fr_cur = ((d2d.amount * b.door_to_door_border_pct / 100, d2d.currency) if d2d else (b.intl_freight.amount, b.intl_freight.currency))
            rf = (f"{d2d.amount * (100 - b.door_to_door_border_pct) / 100:,.2f} {d2d.currency}".replace(",", " ") if d2d else b.rf_delivery_rub)
            vals = [self._n(b.id), b.route, b.transport_units, b.transit_days, b.customs_days, fr_amount, fr_cur,
                    b.insurance_pct, b.broker_rub, rf, ALLOC_RU.get(b.allocation.value, b.allocation.value)]
            for j, v in enumerate(vals):
                ws.cell(row, 1 + j, v)
            row += 1
        row += 1
        _hdr(ws, row, ["Тип машины", "Завод", "Модель", "Кол-во", "Партия", "Вес, кг", "Объём, м³", "Внутрикитайская логистика", "Валюта",
                       "Экспортные формальности", "Валюта", "Код ТН ВЭД", "Пошлина, %", "НДС, %", "Утильсбор, RUB", "Сертификация, RUB"])
        row += 1
        for p in self.inp.positions:
            vals = [p.machine_type, self._n(p.factory), p.model, p.qty, self._n(p.batch_id) if p.batch_id else "—", p.weight_kg, p.volume_m3,
                    p.domestic_logistics.amount, p.domestic_logistics.currency, p.export_formalities.amount, p.export_formalities.currency,
                    p.hs_code, p.duty_pct, p.vat_pct, p.util_fee_rub, p.certification_rub]
            for j, v in enumerate(vals):
                ws.cell(row, 1 + j, v)
            row += 1
        comments = self.meta.get("logistics_comments") or []
        if comments:
            row += 1
            _hdr(ws, row, ["Статья", "Позиция/партия", "Значение", "История (среднее)", "Отклонение, %", "Комментарий логиста"])
            row += 1
            for c in comments:
                for j, k in enumerate(["item", "ref", "value", "history_avg", "deviation_pct", "comment"]):
                    v = c.get(k)
                    ws.cell(row, 1 + j, ITEM_RU.get(v, v) if k == "item" else v)
                row += 1
        _widths(ws, {1: 26, 2: 40, 3: 20, 4: 12, 5: 16, 6: 22, 7: 10, 8: 22, 9: 10, 10: 22, 11: 10, 12: 14, 13: 12, 14: 10, 15: 16, 16: 18})

    # ------------------------------------------------------------------ «Сервис»
    def _sheet_service(self) -> None:
        ws = self.wb.create_sheet("Сервис")
        ws["A1"] = "Сервис до наработки — детализация по периодам"
        ws["A1"].font = Font(bold=True, size=13)
        row = 3
        rows = self.meta.get("service_rows") or []
        if not rows:
            ws.cell(row, 1, "Флаг «Сервис до наработки» не активен или расчёт сервиса не выполнен.")
            return
        _hdr(ws, row, ["Месяц", "Наработка, м·ч", "Регламенты", "Чел·ч", "Работы, RUB", "Выезды", "Выезды, RUB", "ЗИП, валюта", "Итого RUB-часть"])
        row += 1
        first = row
        for r in rows:
            vals = [r["month"], r["hours_cum"], ", ".join(r["regulations"]), r["labour_hours"], r["labour_rub"], r["visits"],
                    r["visit_rub"], r["spare_parts_fx"], r["total_rub_part"]]
            for j, v in enumerate(vals):
                c = ws.cell(row, 1 + j, v)
                if j in (4, 6, 7, 8):
                    c.number_format = NUM
            row += 1
        ws.cell(row, 1, "Итого").font = BOLD
        for col in (4, 5, 7, 8, 9):
            L = get_column_letter(col)
            c = ws.cell(row, col, f"=SUM({L}{first}:{L}{row - 1})")
            c.font, c.number_format = BOLD, NUM
        row += 2
        for k, title in (("commissioning_rub", "Шеф-монтаж и ПНР, RUB"), ("training_rub", "Обучение персонала, RUB"),
                         ("reserve_fx", "Резерв на внеплановые ремонты (валюта)"), ("total_fx_part", "Итого валютная часть"),
                         ("total_rub_part", "Итого рублёвая часть")):
            if k in self.meta.get("service_totals", {}):
                ws.cell(row, 1, title)
                ws.cell(row, 2, self.meta["service_totals"][k]).number_format = NUM
                row += 1
        _widths(ws, {1: 8, 2: 16, 3: 30, 4: 10, 5: 16, 6: 8, 7: 16, 8: 16, 9: 18})

    # ------------------------------------------------------------------ «Платежи и денежный поток»
    def _sheet_payments(self) -> None:
        ws = self.wb.create_sheet("Платежи и денежный поток")
        ws["A1"] = "Графики платежей и накопленный остаток (по неделям, RUB по курсу сценария)"
        ws["A1"].font = Font(bold=True, size=13)
        row = 3
        _hdr(ws, row, ["Предложение (оплата клиента)", "№", "Доля, %", "Сумма", "Событие", "Через, дн", "Банковская гарантия"])
        row += 1
        for v in self.inp.variants:
            for l in v.client_payments:
                for j, val in enumerate([v.name, l.no, l.share_pct, l.amount, EVENT_RU.get(l.event.value, l.event.value),
                                         f"{l.offset_days} {'раб.' if l.offset_kind == 'working' else 'кал.'}", GUARANTEE_RU.get(l.guarantee.value, l.guarantee.value)]):
                    ws.cell(row, 1 + j, val)
                row += 1
        row += 1
        _hdr(ws, row, ["Позиция (оплата заводу)", "№", "Доля, %", "Событие", "Через, дн", "Курс зафиксирован"])
        row += 1
        for p in self.inp.positions:
            for l in p.factory_payments:
                for j, val in enumerate([f"{p.machine_type} {self._n(p.id)}".strip(), l.no, l.share_pct, EVENT_RU.get(l.event.value, l.event.value),
                                         f"{l.offset_days} {'раб.' if l.offset_kind == 'working' else 'кал.'}", l.fixed_rate if l.fixed_rate else "нет"]):
                    ws.cell(row, 1 + j, val)
                row += 1
        row += 1
        _hdr(ws, row, ["Предложение — даты оплат клиента (базовый сценарий)", "№", "Событие-основание", "Дата", "Сумма, RUB", "Аккредитив"])
        row += 1
        for v in self.inp.variants:
            for pd in self.res.variants[v.id]["base"].payment_dates:
                for j, val in enumerate([v.name, pd["no"], EVENT_RU.get(pd["event"], pd["event"]), pd["on"], pd["amount_rub"], "да" if pd.get("via") == "lc" else "нет"]):
                    c = ws.cell(row, 1 + j, val)
                    if j == 3:
                        c.number_format = "DD.MM.YYYY"
                    elif j == 4:
                        c.number_format = NUM
                row += 1
        row += 1
        for v in self.inp.variants:
            for s in SCEN_ORDER:
                r = self.res.variants[v.id][s]
                n0 = lambda x: f"{x:,.0f}".replace(",", " ")   # noqa: E731
                ws.cell(row, 1, f"{v.name} — {SCEN_TITLES[s]}: кассовый разрыв {n0(r.cash_gap.amount_rub)} RUB, {r.cash_gap.days} дн; "
                                f"финансовые расходы: " + ", ".join(f"{FIN_RU.get(k, k)} {n0(val)}" for k, val in r.finance_breakdown.items())).font = BOLD
                row += 1
                _hdr(ws, row, ["Неделя с", "Притоки", "Оттоки", "Накопленный остаток"])
                row += 1
                first = row
                for pt in r.cashflow:
                    ws.cell(row, 1, pt.on).number_format = "DD.MM.YYYY"
                    ws.cell(row, 2, pt.inflow).number_format = NUM
                    ws.cell(row, 3, pt.outflow).number_format = NUM
                    f = f"=B{row}-C{row}" if row == first else f"=D{row - 1}+B{row}-C{row}"
                    ws.cell(row, 4, f).number_format = NUM
                    row += 1
                row += 1
        _widths(ws, {1: 40, 2: 18, 3: 18, 4: 22, 5: 16, 6: 14, 7: 16})

    # ------------------------------------------------------------------ «Курсы»
    def _sheet_fx(self) -> None:
        ws = self.wb.create_sheet("Курсы")
        ws["A1"] = f"Курсы ЦБ РФ на {self.inp.fx.as_of:%d.%m.%Y} ({FX_SOURCE_RU.get(self.inp.fx.source, self.inp.fx.source)}) и прогноз на даты платежей"
        ws["A1"].font = Font(bold=True, size=13)
        s = self.inp.fx_settings
        ws["A2"] = (f"Ключевая ставка {s.key_rate_pct} %, ставка НБК {s.pboc_rate_pct} %, глубина истории {s.history_years} г., "
                    f"веса: форвард {s.weight_forward}, тренд и сезонность {s.weight_trend}, эксперт {s.weight_expert}; "
                    f"ΔK: {'историческая волатильность' if s.use_historical_volatility else 'фиксированные'}")
        row = 4
        _hdr(ws, row, ["Платёж", "Валюта", "Дата платежа", "Дней до платежа", "Курс ЦБ сегодня", "Форвард", "Тренд", "Сезонность", "Сезонность мин.", "Сезонность макс.",
                       "Экспертная оценка", "Вес форварда", "Вес тренда", "Вес эксперта", "Прогноз", "Отклонение пессим., %", "Отклонение оптим., %",
                       "Курс пессим.", "Курс базовый", "Курс оптим.", "Курс зафиксирован"])
        row += 1
        seen = set()
        comps = []
        for c in sorted(self.res.fx_components, key=lambda c: c.on):
            key = (c.payment_ref, c.on, c.currency)
            if c.payment_ref.endswith("_alloc") or key in seen:
                continue
            seen.add(key)
            comps.append(c)
        for c in comps:
            vals = [self._ref(c.payment_ref), c.currency, c.on, c.t_days, c.k_cbr, c.comp_a_forward, c.comp_b_trend, c.comp_c_season,
                    c.season_spread[0], c.season_spread[1], c.comp_g_expert, *c.weights, c.forecast,
                    c.dk_pct.get("pessimistic"), c.dk_pct.get("optimistic"),
                    c.rate_by_scenario.get("pessimistic"), c.rate_by_scenario.get("base"), c.rate_by_scenario.get("optimistic"),
                    "да" if c.fixed else "нет"]
            for j, v in enumerate(vals):
                cell = ws.cell(row, 1 + j, v)
                if isinstance(v, date):
                    cell.number_format = "DD.MM.YYYY"
                elif isinstance(v, float):
                    cell.number_format = "0.0000"
            row += 1
        row += 1
        acc = self.meta.get("forecast_accuracy") or []
        if acc:
            ws.cell(row, 1, "Точность прошлых прогнозов (прогноз vs факт из план/факт закрытых сделок)").font = BOLD
            row += 1
            _hdr(ws, row, ["Горизонт, дн", "Сделок", "Средняя ошибка, %", "Ошибка форварда, %", "Ошибка тренда, %", "Ошибка эксперта, %"])
            row += 1
            for a in acc:
                for j, k in enumerate(["horizon", "n", "mape", "mape_a", "mape_b", "mape_g"]):
                    ws.cell(row, 1 + j, a.get(k))
                row += 1
        _widths(ws, {1: 46, 2: 8, 3: 13, 4: 10, 5: 12, 6: 12, 7: 12, 8: 12, 9: 12, 10: 12, 11: 12, 12: 10, 13: 10, 14: 10, 15: 12,
                     16: 12, 17: 12, 18: 12, 19: 12, 20: 12, 21: 12})

    # ------------------------------------------------------------------ «Исходные данные RFQ»
    def _sheet_rfq(self) -> None:
        ws = self.wb.create_sheet("Исходные данные RFQ")
        ws["A1"] = "Исходные данные позиций (из RFQ заводов)"
        ws["A1"].font = Font(bold=True, size=13)
        row = 3
        _hdr(ws, row, ["Позиция", "Завод", "Тип машины", "Модель", "Кол-во", "Цена за ед.", "Опции", "Валюта", "Производство, дн",
                       "Доставка, дн", "Гарантийные случаи, %", "Файл-источник"])
        row += 1
        srcs = self.meta.get("rfq_sources") or {}
        for p in self.inp.positions:
            vals = [self._n(p.id), self._n(p.factory), p.machine_type, p.model, p.qty, p.unit_price, p.options_price, p.currency,
                    p.production_days, p.delivery_days, p.warranty_reserve_pct, srcs.get(p.id, "")]
            for j, v in enumerate(vals):
                ws.cell(row, 1 + j, v)
            row += 1
        row += 1
        fields = self.meta.get("rfq_fields") or {}   # position_id → {code: {name, value, unit}}
        for pid, fs in fields.items():
            ws.cell(row, 1, f"Технические характеристики: {self._n(pid)}").font = BOLD
            row += 1
            _hdr(ws, row, ["№", "Параметр", "Значение", "Ед.", "Для логиста"])
            row += 1
            for code, f in sorted(fs.items(), key=lambda kv: [int(x) for x in kv[0].split(".")]):
                unit = str(f.get("unit") or "").split(" / ")[0]
                for j, v in enumerate([code, f.get("name"), f.get("value_ru") or f.get("value"), unit, "да" if f.get("is_l") else ""]):
                    ws.cell(row, 1 + j, v)
                row += 1
            row += 1
        _widths(ws, {1: 30, 2: 46, 3: 30, 4: 20, 5: 12, 6: 16, 7: 12, 8: 8, 9: 14, 10: 12, 11: 14, 12: 30})

    def _sheet_spare_parts(self) -> None:
        ws = self.wb.create_sheet("ЗИП на ТО")
        ws["A1"] = "ЗИП на ТО (из листа 2 шаблона RFQ)"
        ws["A1"].font = Font(bold=True, size=13)
        _hdr(ws, 3, ["Позиция", "Система/узел", "Кат. №", "Наименование", "Периодичность, м·ч", "Кол-во", "Цена", "Валюта", "Выполнений", "Сумма"])
        row = 4
        target = self.meta.get("target_hours") or 10000
        for pid, rows in (self.meta.get("spare_parts") or {}).items():
            for sp in rows:
                ws.cell(row, 1, self._n(pid))
                for j, k in enumerate(["system", "part_no", "name", "interval_hours", "qty_per_service", "unit_price", "currency"]):
                    ws.cell(row, 2 + j, sp.get(k))
                ws.cell(row, 9, f"=IF(E{row}>0,INT({target}/E{row}),0)")
                ws.cell(row, 10, f"=I{row}*F{row}*G{row}").number_format = NUM
                row += 1
        _widths(ws, {2: 20, 3: 14, 4: 36, 5: 18})

    def _sheet_history(self) -> None:
        ws = self.wb.create_sheet("История и справки")
        ws["A1"] = "Справки по ценам заводов и сверки логистики с историей"
        ws["A1"].font = Font(bold=True, size=13)
        row = 3
        _hdr(ws, row, ["Позиция", "Завод", "Модель", "Цена RFQ", "Последняя цена", "Дата", "Отклонение, %", "Комментарий менеджера"])
        row += 1
        for pr in self.meta.get("price_references") or []:
            for j, k in enumerate(["position_id", "factory", "model", "unit_price", "last_price", "on", "deviation_pct", "comment"]):
                v = pr.get(k)
                ws.cell(row, 1 + j, self._n(v) if k in ("position_id", "factory") else v)
            row += 1
        row += 1
        _hdr(ws, row, ["Статья", "Позиция/партия", "Введено", "Последние 5 значений", "Среднее", "Отклонение, %", "Комментарий логиста"])
        row += 1
        for c in self.meta.get("logistics_comments") or []:
            for j, k in enumerate(["item", "ref", "value", "history", "history_avg", "deviation_pct", "comment"]):
                v = c.get(k)
                if k == "item":
                    v = ITEM_RU.get(v, v)
                if isinstance(v, (list, dict)):
                    v = "; ".join(f"{h.get('amount')} ({h.get('on')})" for h in v) if isinstance(v, list) else str(v)
                ws.cell(row, 1 + j, v)
            row += 1
        _widths(ws, {1: 24, 2: 24, 3: 16, 4: 30, 5: 16, 6: 14, 7: 40, 8: 40})

    def _sheet_visas(self) -> None:
        ws = self.wb.create_sheet("Визы")
        ws["A1"] = f"Визы по версии расчёта {self.meta.get('version_no', '')}"
        ws["A1"].font = Font(bold=True, size=13)
        _hdr(ws, 3, ["Ступень", "Роль", "Пользователь", "Дата", "Решение", "Комментарий"])
        row = 4
        for a in self.meta.get("approvals") or []:
            role_ru = {"cd": "КД", "fd": "ФД", "gd": "ЗГД", "manager": "менеджер", "admin": "администратор"}
            dec_ru = {"approved": "согласовано", "declined": "отказ", "rejected": "отказ", True: "согласовано", False: "отказ"}
            for j, k in enumerate(["step", "role", "user", "at", "decision", "comment"]):
                v = a.get(k)
                ws.cell(row, 1 + j, role_ru.get(v, v) if k == "role" else dec_ru.get(v, v) if k == "decision" else v)
            row += 1
        if not self.manager:
            row += 1
            ws.cell(row, 1, "Требуемые ступени по результату расчёта:").font = BOLD
            row += 1
            ap = self.res.approval
            for name, flag in (("Коммерческая (КД)", ap.commercial), ("Финансовая (ФД)", ap.financial), ("ЗГД", ap.general)):
                ws.cell(row, 1, name)
                ws.cell(row, 2, "требуется" if flag else "не требуется")
                row += 1
        _widths(ws, {1: 24, 2: 12, 3: 28, 4: 18, 5: 14, 6: 50})


def export_excel(inp: DealCalcInput, res: DealCalcResult, path: str, meta: Optional[dict] = None,
                 manager_version: bool = False) -> str:
    return ExcelExporter(inp, res, meta, manager_version).build(path)
