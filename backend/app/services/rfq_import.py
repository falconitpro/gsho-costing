"""Импорт двуязычного RU-ZH шаблона RFQ (раздел 4 ТЗ).

Шаблон — Excel с листами «Техданные и КП» и «ЗИП на ТО». Поля идентифицируются по коду (0.1 … 14.21)
в первой колонке таблицы; разборщик ищет колонки «Код», «RU», «ZH», «Значение», «Ед.», «L» по заголовкам,
поэтому перестановка колонок и добавление строк не ломают импорт. Версия шаблона читается из ячейки
с текстом «Версия шаблона» (или «Template version») либо определяется по набору кодов.

Результат — словарь по кодам + разобранные позиции + лист ЗИП + список ошибок для предпросмотра.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from openpyxl import load_workbook

CODE_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")

# Коды с меткой L (уходят логисту в карточку оборудования)
L_CODES = {"1.5", "1.6", "1.7", "1.14", "1.15", "1.16", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"} | {f"14.{i}" for i in range(7, 22)}

# Коды → поля карточки сделки / позиции
FIELD_MAP = {
    "0.1": "kp_due", "0.2": "kp_validity_days", "0.3": "priority",
    "0.4": "temp_min", "0.5": "temp_max", "0.6": "operating_conditions",
    "0.7": "factory_name", "0.8": "factory_address",
    "1.1": "machine_type", "1.2": "model",
    "1.5": "length_mm", "1.6": "width_mm", "1.7": "height_mm",
    "1.14": "weight_kg", "1.15": "weight_shipping_kg", "1.16": "weight_max_kg",
    "2.1": "engine_model", "2.2": "engine_power_kw", "2.3": "engine_emission", "2.4": "engine_fuel",
    "2.5": "engine_displacement", "2.6": "engine_manufacturer",
    "13.2": "basis", "13.3": "delivery_point", "13.4": "qty", "13.5": "unit_price", "13.6": "currency",
    "13.7": "price_validity", "13.8": "zip_price",
    "14.1": "production_days", "14.2": "delivery_days", "14.3": "packaging", "14.4": "warranty_months", "14.5": "warranty_hours",
    "14.8": "training",
}
REQUIRED_CODES = {"0.7", "1.1", "1.2", "13.4", "13.5", "13.6", "14.1"}
NUMERIC_CODES = {"0.4", "0.5", "1.5", "1.6", "1.7", "1.14", "1.15", "1.16", "2.2", "13.4", "13.5", "13.8", "14.1", "14.2", "14.4", "14.5"}
NA_VALUES = {"", "n/a", "na", "-", "—", "нет данных", "无"}
KNOWN_UNITS = {"мм", "mm", "кг", "kg", "т", "t", "квт", "kw", "л.с.", "hp", "м³", "m3", "л", "l", "°c", "дн", "дней", "days", "мес", "months", "м·ч", "мч", "h"}


@dataclass
class ImportError_:
    code: str
    message: str
    severity: str = "error"   # error | warning


@dataclass
class RfqField:
    code: str
    label_ru: str
    label_zh: str
    value: Any
    unit: str = ""
    is_l: bool = False
    row: int = 0


@dataclass
class RfqParsed:
    template_version: str = ""
    fields: dict[str, RfqField] = field(default_factory=dict)
    header: dict[str, Any] = field(default_factory=dict)        # поля сделки (0.x)
    position: dict[str, Any] = field(default_factory=dict)      # поля позиции (1.x, 13.x, 14.x)
    options: list[dict] = field(default_factory=list)           # 12.x
    logistics_params: dict[str, Any] = field(default_factory=dict)   # метка L
    tech_params: dict[str, Any] = field(default_factory=dict)        # остальное (каталог)
    spare_parts: list[dict] = field(default_factory=list)       # лист «ЗИП на ТО»
    errors: list[ImportError_] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not [e for e in self.errors if e.severity == "error"]

    def to_dict(self) -> dict:
        return {
            "template_version": self.template_version,
            "header": self.header, "position": self.position, "options": self.options,
            "logistics_params": self.logistics_params, "tech_params": self.tech_params,
            "spare_parts": self.spare_parts, "missing_required": self.missing_required,
            "errors": [e.__dict__ for e in self.errors],
            "fields": {c: {"label_ru": f.label_ru, "label_zh": f.label_zh, "value": f.value, "unit": f.unit, "is_l": f.is_l}
                       for c, f in self.fields.items()},
        }


def _norm(v: Any) -> str:
    return str(v).strip().lower() if v is not None else ""


def _find_header(ws) -> tuple[int, dict[str, int]]:
    """Ищет строку заголовков и индексы колонок: code, ru, zh, value, unit, l."""
    aliases = {
        "code": {"код", "code", "№", "编号"}, "ru": {"параметр", "наименование", "ru", "parameter", "параметр (ru)"},
        "zh": {"zh", "中文", "参数", "名称"}, "value": {"значение", "value", "数值", "ответ завода", "answer"},
        "unit": {"ед.", "ед", "unit", "единица", "单位"}, "l": {"l", "логист", "метка"},
    }
    for r in range(1, min(ws.max_row, 40) + 1):
        cells = {c: _norm(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 20) + 1)}
        cols: dict[str, int] = {}
        for key, names in aliases.items():
            for c, v in cells.items():
                if v in names and key not in cols:
                    cols[key] = c
        if "code" in cols and "value" in cols:
            return r, cols
    # резерв: колонка с кодами определяется по содержимому
    for c in range(1, min(ws.max_column, 10) + 1):
        hits = sum(1 for r in range(1, min(ws.max_row, 200) + 1) if CODE_RE.match(_norm(ws.cell(r, c).value)))
        if hits >= 10:
            return 0, {"code": c, "ru": c + 1, "zh": c + 2, "value": c + 3, "unit": c + 4, "l": c + 5}
    raise ValueError("Не найдена таблица с кодами полей RFQ — проверьте, что загружен шаблон RFQ.")


def _parse_number(raw: Any) -> Optional[float]:
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    s = re.sub(r"[^\d.\-]", "", s)
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_rfq(path: str) -> RfqParsed:
    wb = load_workbook(path, data_only=True)
    out = RfqParsed()
    main = next((ws for ws in wb.worksheets if "тех" in ws.title.lower() or "кп" in ws.title.lower()), wb.worksheets[0])

    # версия шаблона
    for row in main.iter_rows(min_row=1, max_row=10, max_col=8):
        for cell in row:
            v = _norm(cell.value)
            if "версия шаблона" in v or "template version" in v:
                nxt = main.cell(cell.row, cell.column + 1).value
                out.template_version = str(nxt or v.split(":")[-1]).strip()
    try:
        hdr_row, cols = _find_header(main)
    except ValueError as e:
        out.errors.append(ImportError_("", str(e)))
        return out

    for r in range(hdr_row + 1, main.max_row + 1):
        code = _norm(main.cell(r, cols["code"]).value)
        if not CODE_RE.match(code):
            continue
        raw = main.cell(r, cols["value"]).value
        unit = _norm(main.cell(r, cols["unit"]).value) if "unit" in cols else ""
        is_l = code in L_CODES or ("l" in cols and _norm(main.cell(r, cols["l"]).value) in {"l", "+", "x", "да", "yes", "1", "true"})
        f = RfqField(code=code, label_ru=str(main.cell(r, cols.get("ru", cols["code"] + 1)).value or ""),
                     label_zh=str(main.cell(r, cols.get("zh", cols["code"] + 2)).value or ""),
                     value=raw, unit=unit, is_l=is_l, row=r)
        # проверки
        if _norm(raw) in NA_VALUES:
            f.value = None
            if code in REQUIRED_CODES:
                out.errors.append(ImportError_(code, f"Обязательное поле {code} «{f.label_ru}» пустое или N/A."))
                out.missing_required.append(code)
        elif code in NUMERIC_CODES:
            num = _parse_number(raw)
            if num is None:
                out.errors.append(ImportError_(code, f"Поле {code} «{f.label_ru}»: ожидается число, получено «{raw}»."))
            else:
                f.value = num
            if unit and unit not in KNOWN_UNITS:
                out.errors.append(ImportError_(code, f"Поле {code}: нераспознанная единица измерения «{unit}».", "warning"))
        out.fields[code] = f

    if not out.template_version:
        out.template_version = "v1" if "14.21" in out.fields else "unknown"
    for code in REQUIRED_CODES - set(out.fields):
        out.errors.append(ImportError_(code, f"В шаблоне нет обязательного поля {code}."))
        out.missing_required.append(code)

    # распределение по группам
    for code, f in out.fields.items():
        name = FIELD_MAP.get(code)
        section = int(code.split(".")[0])
        if section == 0 and name:
            out.header[name] = f.value
        elif section == 12:
            if f.value not in (None, "", 0):
                out.options.append({"code": code, "name": f.label_ru, "name_zh": f.label_zh, "value": f.value})
        elif name:
            out.position[name] = f.value
        if f.is_l:
            out.logistics_params[code] = {"name": f.label_ru, "value": f.value, "unit": f.unit}
        else:
            out.tech_params[code] = {"name": f.label_ru, "name_zh": f.label_zh, "value": f.value, "unit": f.unit}
    cur = _norm(out.position.get("currency"))
    out.position["currency"] = {"cny": "CNY", "rmb": "CNY", "人民币": "CNY", "usd": "USD", "eur": "EUR", "rub": "RUB"}.get(cur, cur.upper() or None)
    if out.position.get("currency") not in ("CNY", "USD", "EUR", "RUB", None):
        out.errors.append(ImportError_("13.6", f"Неизвестная валюта «{out.position['currency']}»."))
    opts_total = sum(_parse_number(o["value"]) or 0 for o in out.options if isinstance(o["value"], (int, float, str)))
    out.position["options_price"] = opts_total

    # лист «ЗИП на ТО»
    zip_ws = next((ws for ws in wb.worksheets if "зип" in ws.title.lower() or "spare" in ws.title.lower()), None)
    if zip_ws is not None:
        out.spare_parts = _parse_spare_parts(zip_ws, out)
    return out


def _parse_spare_parts(ws, out: RfqParsed) -> list[dict]:
    """Построчно: система/узел, каталожный номер, наименование, периодичность, кол-во, цена."""
    header_row = None
    cols: dict[str, int] = {}
    aliases = {
        "system": {"система", "узел", "система/узел", "system", "系统"}, "part_no": {"каталожный номер", "кат. №", "part no", "p/n", "零件号"},
        "name": {"наименование", "name", "名称"}, "interval": {"периодичность", "interval", "периодичность, м·ч", "周期"},
        "qty": {"кол-во", "количество", "qty", "数量"}, "price": {"цена", "price", "单价"}, "currency": {"валюта", "currency", "货币"},
        "total": {"итого", "сумма", "total", "总价"},
    }
    for r in range(1, min(ws.max_row, 20) + 1):
        cells = {c: _norm(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 15) + 1)}
        found = {k: c for k, names in aliases.items() for c, v in cells.items() if v in names}
        if "name" in found and "price" in found:
            header_row, cols = r, found
            break
    if header_row is None:
        out.errors.append(ImportError_("ЗИП", "Лист «ЗИП на ТО»: не найдены заголовки таблицы.", "warning"))
        return []
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(r, cols["name"]).value
        if not name:
            continue
        price = _parse_number(ws.cell(r, cols["price"]).value)
        if price is None:
            out.errors.append(ImportError_("ЗИП", f"Строка {r} листа ЗИП «{name}»: пустая цена.", "warning"))
        rows.append({
            "row": r, "system": str(ws.cell(r, cols["system"]).value or "") if "system" in cols else "",
            "part_no": str(ws.cell(r, cols["part_no"]).value or "") if "part_no" in cols else "",
            "name": str(name),
            "interval_hours": _parse_number(ws.cell(r, cols["interval"]).value) if "interval" in cols else None,
            "qty_per_service": _parse_number(ws.cell(r, cols["qty"]).value) or 1 if "qty" in cols else 1,
            "unit_price": price or 0.0,
            "currency": str(ws.cell(r, cols["currency"]).value or "CNY") if "currency" in cols else "CNY",
            "total_price": _parse_number(ws.cell(r, cols["total"]).value) if "total" in cols else None,
        })
    return rows


# ------------------------------------------------------------------ проверка цены по истории

def price_reference(unit_price: float, currency: str, history: list[dict], threshold_pct: float) -> dict:
    """«Справка по цене»: последняя цена завода на модель, дата, отклонение в %, нужен ли комментарий."""
    same = [h for h in history if h.get("currency") == currency]
    if not same:
        return {"last_price": None, "on": None, "deviation_pct": None, "needs_comment": False, "history": []}
    last = max(same, key=lambda h: h["on"])
    dev = (unit_price - last["unit_price"]) / last["unit_price"] * 100 if last["unit_price"] else None
    return {
        "last_price": last["unit_price"], "on": str(last["on"]), "currency": currency,
        "deviation_pct": None if dev is None else round(dev, 2),
        "needs_comment": dev is not None and abs(dev) > threshold_pct,
        "history": sorted(same, key=lambda h: h["on"], reverse=True)[:5],
    }
