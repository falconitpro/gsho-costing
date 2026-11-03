"""Импорт двуязычного RU-ZH шаблона RFQ (раздел 4 ТЗ) — по реальному шаблону компании.

Лист «Техданные и КП 技术参数及报价»:
  A — метка L (поля логиста) | B — № (код 0.1 … 14.21, бывают трёхуровневые 8.5.1) | C — Параметр (RU) | D — 参数 (ZH)
  E — Ед. изм. | F — Требования заказчика (RU, заполняет заказчик) | G — Требования (ZH) | H — Значение (кит.) |
  I — Значение (рус.) | J — Комментарий / документ.
  Строки «N. Название раздела» — заголовки разделов 0…14.
Лист «ЗИП на ТО 维保备件»: наработка и валюта в шапке, таблица: №, Система/узел, Наименование, Кат. номер,
  Производитель, Ед., Кол-во на замену, Периодичность, Кол-во замен (формула), Общее кол-во (формула),
  Цена за ед., Сумма (формула), Наличие/срок поставки, Комментарий.

Разборщик ищет колонки по заголовкам, поэтому перестановка колонок и добавление строк не ломают импорт.
Все поля опросника (не только обязательные) сохраняются в карточке: заполненные показываются по разделам.
"""
from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any, Optional

from openpyxl import load_workbook

CODE_RE = re.compile(r"^\d{1,2}\.\d{1,2}(\.\d{1,2})?$")
SECTION_RE = re.compile(r"^(\d{1,2})\.\s*(.+)$")

# Коды → поля карточки сделки / позиции
FIELD_MAP = {
    "0.1": "requested_at", "0.2": "kp_due", "0.3": "priority",
    "0.4": "temp_min", "0.5": "temp_max", "0.6": "operating_conditions",
    "0.7": "factory_name", "0.8": "factory_address",
    "1.1": "machine_type", "1.2": "model", "1.3": "country", "1.4": "year",
    "1.5": "length_mm", "1.6": "width_mm", "1.7": "height_mm",
    "1.14": "weight_kg", "1.15": "weight_gross_kg", "1.16": "payload_kg", "1.17": "bucket_m3",
    "1.19": "speed_empty", "1.20": "speed_loaded",
    "2.1": "engine_manufacturer", "2.2": "engine_model", "2.3": "engine_emission", "2.4": "engine_cylinders",
    "2.5": "engine_displacement", "2.6": "engine_power_kw",
    "12.6": "options_total",
    "13.1": "currency", "13.2": "basis", "13.3": "delivery_point", "13.4": "qty", "13.5": "unit_price",
    "13.6": "total_price", "13.7": "zip_price_per_unit", "13.8": "zip_price", "13.9": "grand_total",
    "14.1": "production_days", "14.2": "delivery_days", "14.3": "total_days", "14.4": "warranty_months",
    "14.5": "warranty_hours", "14.6": "warranty_scope", "14.7": "spare_parts_lead_days", "14.8": "training",
}
HEADER_CODES = {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"}
REQUIRED_CODES = {"0.7", "1.1", "1.2", "13.4", "13.5", "13.1", "14.1"}
NUMERIC_CODES = {"0.4", "0.5", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9", "1.10", "1.11", "1.12", "1.13", "1.14", "1.15", "1.16",
                 "1.17", "1.18", "1.19", "1.20", "1.21", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.12", "2.14", "4.10", "4.11",
                 "4.14", "5.5", "7.3", "7.4", "8.1", "8.4", "8.6", "9.4", "9.13", "9.14", "12.1", "12.2", "12.3", "12.4", "12.5",
                 "12.6", "13.4", "13.5", "13.6", "13.7", "13.8", "13.9", "14.1", "14.2", "14.3", "14.4", "14.5", "14.7"}
NA_VALUES = {"", "n/a", "na", "-", "—", "нет данных", "无", "不适用"}
CURRENCY_MAP = {"cny": "CNY", "rmb": "CNY", "人民币": "CNY", "юан": "CNY", "usd": "USD", "доллар": "USD", "美元": "USD",
                "eur": "EUR", "евро": "EUR", "欧元": "EUR", "rub": "RUB", "руб": "RUB", "卢布": "RUB"}
HEADER_ALIASES = {
    "code": ("№",), "name_ru": ("параметр",), "name_zh": ("参数",), "unit": ("ед. изм", "ед.изм", "единица"),
    "req_ru": ("требования заказчика\n", "требования заказчика", "требования"), "req_zh": ("требования заказчика (кит",),
    "value_zh": ("значение (кит", "值（中文", "数值（中文"), "value_ru": ("значение (рус", "数值（俄文", "значение"),
    "comment": ("комментарий", "备注"),
}


@dataclass
class ImportError_:
    code: str
    message: str
    severity: str = "error"   # error | warning | info


@dataclass
class RfqField:
    code: str
    section: str
    section_no: int
    name_ru: str
    name_zh: str
    unit: str
    requirement: Any
    requirement_zh: Any
    value: Any            # нормализованное значение: число, либо текст (RU, иначе ZH)
    value_ru: Any
    value_zh: Any
    comment: Any
    is_l: bool
    row: int

    def as_dict(self) -> dict:
        return {"code": self.code, "section": self.section, "section_no": self.section_no, "name": self.name_ru, "name_zh": self.name_zh,
                "unit": self.unit, "requirement": self.requirement, "requirement_zh": self.requirement_zh, "value": self.value,
                "value_ru": self.value_ru, "value_zh": self.value_zh, "comment": self.comment, "is_l": self.is_l}


@dataclass
class RfqParsed:
    template_version: str = ""
    title: str = ""
    sections: list[dict] = field(default_factory=list)          # [{no, title}]
    fields: dict[str, RfqField] = field(default_factory=dict)   # все поля опросника (в т.ч. пустые)
    header: dict[str, Any] = field(default_factory=dict)        # поля сделки (0.x)
    position: dict[str, Any] = field(default_factory=dict)      # поля позиции (1.x, 13.x, 14.x)
    options: list[dict] = field(default_factory=list)           # 12.x
    logistics_params: dict[str, Any] = field(default_factory=dict)   # метка L (заполненные)
    tech_params: dict[str, Any] = field(default_factory=dict)        # остальные заполненные
    spare_parts: list[dict] = field(default_factory=list)       # лист «ЗИП на ТО»
    zip_target_hours: Optional[float] = None
    zip_currency: str = "CNY"
    errors: list[ImportError_] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not [e for e in self.errors if e.severity == "error"]

    @property
    def filled_count(self) -> int:
        return sum(1 for f in self.fields.values() if f.value not in (None, ""))

    def to_dict(self) -> dict:
        return {
            "template_version": self.template_version, "title": self.title, "sections": self.sections,
            "header": self.header, "position": self.position, "options": self.options,
            "logistics_params": self.logistics_params, "tech_params": self.tech_params,
            "spare_parts": self.spare_parts, "zip_target_hours": self.zip_target_hours, "zip_currency": self.zip_currency,
            "missing_required": self.missing_required, "filled_count": self.filled_count, "total_count": len(self.fields),
            "errors": [e.__dict__ for e in self.errors],
            "fields": {c: f.as_dict() for c, f in self.fields.items()},
        }


def _norm(v: Any) -> str:
    return str(v).strip().lower() if v is not None else ""


def _parse_number(raw: Any) -> Optional[float]:
    """Число из ячейки: 9 850 → 9850; «12 мес.» → 12; «5 050 ± 50» → 5050; «≥ 200» → 200; «95,6 (при 2 400 об/мин)» → 95.6;
    «2 × 280 кВт», «200–1000» → None (это текст, не число)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw)
    # заводские записи вида «5 050 ± 50», «≥ 200», «≈ 1,48 м³», «95,6 (при 2 400 об/мин)»: допуск, знак сравнения и пояснение в скобках
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"±.*$", "", s)
    s = s.strip().lstrip("≥≤≈~<>=").strip()
    s = s.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    m = re.match(r"^[-+]?\d+(\.\d+)?", s)
    if not m:
        return None
    rest = s[m.end():]
    if re.search(r"[\d×x/–-]", rest):      # вторая числовая группа, умножение, диапазон → текст
        return None
    return float(m.group(0))


def _find_main_header(ws) -> tuple[int, dict[str, int], Optional[int]]:
    """Строка заголовков и индексы колонок; колонка метки L — первая непустая колонка левее «№» (обычно A)."""
    for r in range(1, min(ws.max_row, 30) + 1):
        cells = {c: _norm(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 20) + 1)}
        if any(v == "№" for v in cells.values()) and any("параметр" in v for v in cells.values()):
            cols: dict[str, int] = {}
            for c, v in cells.items():
                if v == "№": cols["code"] = c
                elif v.startswith("параметр"): cols["name_ru"] = c
                elif v.startswith("参数"): cols["name_zh"] = c
                elif v.startswith("ед") or "单位" in v: cols["unit"] = c
                elif v.startswith("требования заказчика (кит") or "客户要求 （中文" in v or "客户要求（中文" in v: cols["req_zh"] = c
                elif v.startswith("требования"): cols["req_ru"] = c
                elif v.startswith("значение (кит") or "数值（中文" in v: cols["value_zh"] = c
                elif v.startswith("значение (рус") or "数值（俄文" in v: cols["value_ru"] = c
                elif v.startswith("значение"): cols.setdefault("value_ru", c)
                elif v.startswith("комментарий") or v.startswith("备注"): cols["comment"] = c
            if "code" in cols and ("value_ru" in cols or "value_zh" in cols):
                l_col = cols["code"] - 1 if cols["code"] > 1 else None
                return r, cols, l_col
    raise ValueError("Не найдена таблица опросника (строка с «№» и «Параметр») — проверьте, что загружен шаблон RFQ.")


def _value(raw_ru: Any, raw_zh: Any, code: str, name: str, out: RfqParsed) -> Any:
    raw = raw_ru if _norm(raw_ru) not in NA_VALUES else raw_zh
    if _norm(raw) in NA_VALUES:
        return None
    if code in NUMERIC_CODES:
        num = _parse_number(raw)
        if num is None:
            out.errors.append(ImportError_(code, f"Поле {code} «{name}»: ожидается число, получено «{raw}».", "warning"))
            return str(raw).strip()
        return num
    return raw if not isinstance(raw, str) else raw.strip()


# если завод/менеджер не заполнил значение, а это условие самого заказчика — берём из «Требований заказчика»
# раздел 0 (дата запроса, срок КП, температуры, условия эксплуатации) заполняет заказчик — тоже из требований
L_MARKS = {"l", "л", "+", "x", "да", "yes", "1", "true"}
FROM_REQUIREMENT = {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "13.2", "13.3", "13.4"}


@lru_cache(maxsize=1)
def _canonical() -> tuple[dict[str, str], frozenset[str]]:
    """Встроенный шаблон: название поля → код (только уникальные названия) и коды с меткой L (данные логисту)."""
    from .rfq_template import DEFAULT_TEMPLATE
    wb = load_workbook(DEFAULT_TEMPLATE, data_only=True)
    ws = next((w for w in wb.worksheets if "тех" in w.title.lower()), wb.worksheets[0])
    hdr, cols, l_col = _find_main_header(ws)
    seen: dict[str, list[str]] = {}
    l_codes: set[str] = set()
    for r in range(hdr + 1, ws.max_row + 1):
        code = str(ws.cell(r, cols["code"]).value or "").strip()
        name = _norm(ws.cell(r, cols.get("name_ru", cols["code"] + 1)).value)
        if CODE_RE.match(code) and name:
            seen.setdefault(name, []).append(code)
        if CODE_RE.match(code) and l_col is not None and _norm(ws.cell(r, l_col).value) in L_MARKS:
            l_codes.add(code)
    return {n: codes[0] for n, codes in seen.items() if len(codes) == 1}, frozenset(l_codes)


def _canonical_names() -> dict[str, str]:
    return _canonical()[0]


def _canonical_code(code: str, name: str, enabled: bool) -> str:
    """Код по названию, если в файле строки сдвинуты (вставлена/удалена строка): только внутри того же раздела."""
    if not enabled:
        return code
    canon = _canonical_names().get(_norm(name))
    if canon and canon != code and canon.split(".")[0] == code.split(".")[0]:
        return canon
    return code


def parse_rfq(path: str, by_name: bool = True) -> RfqParsed:
    wb = load_workbook(path, data_only=True)
    out = RfqParsed()
    main = next((ws for ws in wb.worksheets if "тех" in ws.title.lower() or "технич" in ws.title.lower() or "кп" in ws.title.lower()), wb.worksheets[0])
    for r in range(1, 5):
        v = main.cell(r, 2).value or main.cell(r, 1).value
        if v and "запрос" in str(v).lower():
            out.title = str(v).strip()
            break
    try:
        hdr_row, cols, l_col = _find_main_header(main)
    except ValueError as e:
        out.errors.append(ImportError_("", str(e)))
        return out

    section, section_no = "", 0
    merged = list(main.merged_cells.ranges)
    for r in range(hdr_row + 1, main.max_row + 1):
        code_raw = main.cell(r, cols["code"]).value
        code = str(code_raw).strip() if code_raw is not None else ""
        name = main.cell(r, cols.get("name_ru", cols["code"] + 1)).value
        if not code and not name:
            continue
        m = SECTION_RE.match(code) if code else None
        if m and not CODE_RE.match(code):
            section_no, section = int(m.group(1)), m.group(2).strip()
            out.sections.append({"no": section_no, "title": section})
            continue
        if isinstance(code_raw, float) and code_raw.is_integer() is False:
            code = f"{code_raw:.2f}".rstrip("0")  # 8.50 → 8.5 (Excel мог хранить код числом)
        if not CODE_RE.match(code):
            continue
        file_code = code
        code = _canonical_code(code, str(name or ""), by_name)
        if code in out.fields:
            # такой код уже занят строкой с «родным» названием — оставляем номер из файла
            code = file_code
            if code in out.fields:
                out.errors.append(ImportError_(file_code, f"Строка {file_code} «{name}» повторяет уже распознанное поле — пропущена.", "warning"))
                continue
        elif code != file_code:
            out.errors.append(ImportError_(code, f"Строка {file_code} «{name}» распознана по названию как поле {code} (нумерация в файле отличается от шаблона).", "info"))
        get = lambda key: main.cell(r, cols[key]).value if key in cols else None
        raw_ru, raw_zh = get("value_ru"), get("value_zh")
        if raw_ru is None and raw_zh is None:
            # объединённая ячейка значения (строки 12.x): берём верхнюю левую ячейку диапазона
            for rng in merged:
                if rng.min_row <= r <= rng.max_row and "value_ru" in cols and rng.min_col <= cols["value_ru"] <= rng.max_col:
                    raw_ru = main.cell(rng.min_row, rng.min_col).value
                    break
        is_l = l_col is not None and _norm(main.cell(r, l_col).value) in L_MARKS
        name_ru = str(name).strip() if name else ""
        f = RfqField(code=code, section=section, section_no=section_no, name_ru=name_ru, name_zh=str(get("name_zh") or "").strip(),
                     unit=str(get("unit") or "").strip(), requirement=get("req_ru"), requirement_zh=get("req_zh"),
                     value=None, value_ru=raw_ru, value_zh=raw_zh, comment=get("comment"), is_l=is_l, row=r)
        f.value = _value(raw_ru, raw_zh, code, name_ru or code, out)
        if f.value is None and code in FROM_REQUIREMENT and _norm(f.requirement) not in NA_VALUES:
            f.value = _value(f.requirement, None, code, name_ru or code, out)
            if f.value is not None:
                out.errors.append(ImportError_(code, f"Поле {code} «{name_ru}» не заполнено заводом — взято из требований заказчика: «{f.requirement}».", "info"))
        if f.value is None and code in REQUIRED_CODES:
            out.errors.append(ImportError_(code, f"Обязательное поле {code} «{name_ru}» не заполнено заводом."))
            out.missing_required.append(code)
        out.fields[code] = f

    if by_name and not any(f.is_l for f in out.fields.values()):
        # в файле нет отметок «L» (данные логисту) — берём их из встроенного шаблона по кодам
        l_codes = _canonical()[1]
        for code, f in out.fields.items():
            f.is_l = code in l_codes
        if l_codes:
            out.errors.append(ImportError_("", "В файле нет отметок «L» (данные для логиста) — применены отметки из шаблона компании.", "info"))
    for code in REQUIRED_CODES - set(out.fields):
        out.errors.append(ImportError_(code, f"В шаблоне нет обязательного поля {code}."))
        out.missing_required.append(code)
    out.template_version = "v2" if "14.21" in out.fields and "13.9" in out.fields else ("v1" if "14.21" in out.fields else "unknown")

    # распределение по группам
    for code, f in out.fields.items():
        filled = f.value not in (None, "")
        key = FIELD_MAP.get(code)
        if code in HEADER_CODES and key:
            out.header[key] = f.value
        elif f.section_no == 12 and code != "12.6":
            if filled or f.name_ru:
                price = _parse_number(f.value) if filled else None
                if f.name_ru or price:
                    out.options.append({"code": code, "name": f.name_ru, "name_zh": f.name_zh, "value": price if price is not None else f.value, "comment": f.comment})
        elif key:
            out.position[key] = f.value
        if filled:
            entry = f.as_dict()
            (out.logistics_params if f.is_l else out.tech_params)[code] = entry
    # валюта
    cur = _norm(out.position.get("currency"))
    out.position["currency"] = next((v for k, v in CURRENCY_MAP.items() if k in cur), None) or (cur.upper()[:3] if cur else None)
    if out.position.get("currency") not in ("CNY", "USD", "EUR", "RUB", None):
        out.errors.append(ImportError_("13.1", f"Неизвестная валюта «{out.fields['13.1'].value}»."))
    if out.position.get("qty") is not None:
        out.position["qty"] = int(out.position["qty"])
    opts_total = out.position.pop("options_total", None)
    per_machine = sum(o["value"] for o in out.options if isinstance(o.get("value"), (int, float)))
    out.position["options_price_per_unit"] = per_machine or (opts_total if isinstance(opts_total, (int, float)) else 0.0)
    out.position["options_price"] = out.position["options_price_per_unit"] * (out.position.get("qty") or 1)

    # лист «ЗИП на ТО»
    zip_ws = next((ws for ws in wb.worksheets if "зип" in ws.title.lower() or "备件" in ws.title or "spare" in ws.title.lower()), None)
    if zip_ws is not None:
        out.spare_parts = _parse_spare_parts(zip_ws, out)
    return out


def _parse_spare_parts(ws, out: RfqParsed) -> list[dict]:
    # шапка листа: наработка и валюта
    for r in range(1, 12):
        for c in range(1, 8):
            v = _norm(ws.cell(r, c).value)
            if "наработка" in v and out.zip_target_hours is None:
                for cc in range(c + 1, min(ws.max_column, 12) + 1):
                    n = _parse_number(ws.cell(r, cc).value)
                    if n: out.zip_target_hours = n; break
            if v.startswith("валюта") or "币种" in v:
                for cc in range(c + 1, min(ws.max_column, 12) + 1):
                    cv = _norm(ws.cell(r, cc).value)
                    if cv:
                        out.zip_currency = next((val for k, val in CURRENCY_MAP.items() if k in cv), "CNY"); break
    header_row, cols = None, {}
    aliases = {"system": ("система",), "name": ("наименование зип", "наименование"), "part_no": ("каталожный",), "brand": ("производитель",),
               "unit": ("ед. изм", "ед.изм"), "qty": ("кол-во на одну", "кол-во на замену"), "interval": ("периодичность",),
               "replacements": ("кол-во замен",), "total_qty": ("общее кол", "总数量"), "price": ("цена за ед", "цена"),
               "total": ("сумма",), "lead": ("наличие", "срок поставки"), "comment": ("комментарий",)}
    for r in range(1, min(ws.max_row, 20) + 1):
        cells = {c: _norm(ws.cell(r, c).value).replace("\n", " ") for c in range(1, min(ws.max_column, 20) + 1)}
        found: dict[str, int] = {}
        for c, v in cells.items():
            for key, names in aliases.items():
                if key not in found and any(v.startswith(n) for n in names):
                    found[key] = c; break
        if "name" in found and "price" in found:
            header_row, cols = r, found; break
    if header_row is None:
        out.errors.append(ImportError_("ЗИП", "Лист «ЗИП на ТО»: не найдены заголовки таблицы.", "warning"))
        return []
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(r, cols["name"]).value
        if not name:
            continue
        g = lambda k: ws.cell(r, cols[k]).value if k in cols else None
        price = _parse_number(g("price"))
        qty = _parse_number(g("qty"))
        interval = _parse_number(g("interval"))
        rows.append({
            "row": r, "system": str(g("system") or "").strip(), "part_no": str(g("part_no") or "").strip(), "brand": str(g("brand") or "").strip(),
            "name": str(name).strip(), "unit": str(g("unit") or "").strip(), "interval_hours": interval, "qty_per_service": qty or 1,
            "replacements": _parse_number(g("replacements")), "total_qty": _parse_number(g("total_qty")),
            "unit_price": price or 0.0, "total_price": _parse_number(g("total")), "currency": out.zip_currency,
            "lead_days": g("lead"), "comment": g("comment"),
            "filled": bool(price or qty or interval),
        })
    filled = [x for x in rows if x["filled"]]
    if rows and not filled:
        out.errors.append(ImportError_("ЗИП", "Лист «ЗИП на ТО» не заполнен заводом (нет цен и периодичности).", "warning"))
    for x in filled:
        if not x["unit_price"]:
            out.errors.append(ImportError_("ЗИП", f"Строка {x['row']} листа ЗИП «{x['name']}»: пустая цена.", "warning"))
    return rows


# ------------------------------------------------------------------ справочник полей шаблона

def template_fields(path: str) -> dict:
    """Словарь полей опросника из файла шаблона — для формы ручного ввода и карточки (без значений)."""
    parsed = parse_rfq(path)
    return {"title": parsed.title, "sections": parsed.sections,
            "fields": [{k: v for k, v in f.as_dict().items() if k not in ("value", "value_ru", "value_zh", "comment")} for f in parsed.fields.values()],
            "required": sorted(REQUIRED_CODES), "numeric": sorted(NUMERIC_CODES)}


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
        "history": [{**h, "on": str(h["on"])} for h in sorted(same, key=lambda h: h["on"], reverse=True)[:5]],   # JSON: даты строками
    }
