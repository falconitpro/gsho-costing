"""Таблица технических характеристик позиции (задача 4), глоссарий и переводчик, запрос заводу и загрузка ответа (задачи 4, 9, 10).

Таблица: № / Параметр (RU) / Ед. изм. / Требования заказчика / Значение (RU) / Комментарий.
Порядок работы: менеджер заполняет требования (вручную или импортом файла клиента) → формирует двуязычный запрос
заводу (RU+ZH; строки «реализуется в России» не попадают) → завод заполняет значения → менеджер загружает ответ
(вручную или импортом), значения переводятся ZH→RU через глоссарий, затем переводчик (машинный перевод — на проверку).
Импорт таблиц — с предпросмотром и выбором столбцов/строк; сопоставление строк по № и названию.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
from functools import lru_cache
from typing import Any, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import adapters
from ..models import EquipmentExecution, Factory, Glossary, ImportMapping, IntegrationLog, MachineType, Position, PositionTechParam, TechParamTemplate
from .rfq_import import template_fields
from .rfq_template import template_path

COLUMNS = ["no", "name_ru", "unit", "customer_requirement_ru", "value_ru", "comment_ru"]
COLUMN_TITLES = {"no": "№", "name_ru": "Параметр", "unit": "Ед. изм.", "customer_requirement_ru": "Требования заказчика",
                 "value_ru": "Значение", "comment_ru": "Комментарий", "name_zh": "参数", "customer_requirement_zh": "客户要求",
                 "value_zh": "数值", "comment_zh": "备注"}
HEADER_ALIASES = {
    "no": ("№", "no", "n", "номер", "序号", "#"),
    "name_ru": ("параметр", "наименование", "характеристика", "parameter", "参数"),
    "unit": ("ед", "единиц", "unit", "单位"),
    "customer_requirement_ru": ("требован", "requirement", "客户要求", "要求"),
    "value_ru": ("значение (рус", "значение", "value", "数值（俄", "数值"),
    "value_zh": ("значение (кит", "数值（中", "值（中"),
    "comment_ru": ("комментар", "comment", "备注", "примеч"),
}
CJK = re.compile(r"[一-鿿]")


def _norm(s: Any) -> str:
    s = "" if s is None else str(s)
    s = s.lower().replace("ё", "е").replace(" ", " ")
    return re.sub(r"[\s\-_,.;:()«»\"']+", " ", s).strip()


def has_cjk(s: Any) -> bool:
    return bool(s) and bool(CJK.search(str(s)))


# ------------------------------------------------------------------ глоссарий и перевод

def glossary_map(db: Session, src: str, dst: str) -> dict[str, str]:
    """Подтверждённые термины глоссария: нормализованный термин → перевод."""
    out: dict[str, str] = {}
    for g in db.scalars(select(Glossary).where(Glossary.status == "confirmed")).all():
        if src == "ru" and g.term_ru and g.term_zh:
            out[_norm(g.term_ru)] = g.term_zh
        elif src == "zh" and g.term_zh and g.term_ru:
            out[_norm(g.term_zh)] = g.term_ru
    return out


def translate(db: Session, texts: list[str], src: str, dst: str, kind: str = "param", user_id: Optional[str] = None) -> list[tuple[str, str]]:
    """Перевод списка строк: (перевод, статус: glossary | machine | none). Машинные переводы сохраняются в глоссарий
    со статусом «machine» — в работе не используются, пока человек не подтвердит."""
    gm = glossary_map(db, src, dst)
    out: list[Optional[tuple[str, str]]] = [None] * len(texts)
    todo: list[int] = []
    for i, t in enumerate(texts):
        if not t or not str(t).strip():
            out[i] = ("", "none")
        elif _norm(t) in gm:
            out[i] = (gm[_norm(t)], "glossary")
        elif src == "zh" and not has_cjk(t):
            out[i] = (str(t), "none")          # число или латиница — переводить нечего
        else:
            todo.append(i)
    if todo:
        tr_adapter = adapters.translator()
        try:
            tr = tr_adapter.translate([str(texts[i]) for i in todo], src, dst)
            db.add(IntegrationLog(system="translator", operation=f"{src}->{dst}", ok=True,
                                  details={"provider": getattr(tr_adapter, "name", ""), "count": len(todo), "translated": sum(1 for x in tr if x)}))
        except Exception as e:   # переводчик недоступен — строки остаются для ручного перевода, ошибка в журнале интеграций
            tr = [None] * len(todo)
            db.add(IntegrationLog(system="translator", operation=f"{src}->{dst}", ok=False,
                                  details={"provider": getattr(tr_adapter, "name", ""), "count": len(todo), "error": str(e)[:500]}))
        existing = {(_norm(g.term_ru), g.kind) for g in db.scalars(select(Glossary)).all()}
        for i, res in zip(todo, tr):
            if res:
                out[i] = (res, "machine")
                ru, zh = (str(texts[i]), res) if src == "ru" else (res, str(texts[i]))
                if (_norm(ru), kind) not in existing:
                    db.add(Glossary(term_ru=ru[:512], term_zh=zh[:512], kind=kind, status="machine", changed_by=user_id))
                    existing.add((_norm(ru), kind))
            else:
                out[i] = ("", "none")
    return [x or ("", "none") for x in out]


# ------------------------------------------------------------------ шаблоны перечня и строки позиции

@lru_cache(maxsize=1)
def _general_fields() -> list[dict]:
    """Поля разделов 1–11 встроенного шаблона RFQ (читаются из файла один раз за процесс)."""
    try:
        fields = template_fields(template_path(None))["fields"]
    except Exception:
        fields = []
    return [f for f in fields if 1 <= (f.get("section_no") or 0) <= 11]


def section_label(code: str, title: str = "") -> str:
    """«1. Общие данные / 总体参数» — номер раздела по коду поля и название раздела (как в файле опросника)."""
    major = (code or "").split(".")[0]
    title = (title or "").strip()
    if title and re.match(r"^\d+\.", title):
        return title
    if not major.isdigit():
        return title or "Дополнительные параметры"
    return f"{major}. {title}" if title else f"{major}. {_general_sections().get(major, 'Раздел ' + major)}"


@lru_cache(maxsize=1)
def _general_sections() -> dict[str, str]:
    """Номер раздела → название раздела по встроенному шаблону RFQ."""
    out: dict[str, str] = {}
    for f in _general_fields():
        major = str(f["code"]).split(".")[0]
        if f.get("section") and major not in out:
            out[major] = f["section"]
    return out


def ensure_general_template(db: Session) -> list[TechParamTemplate]:
    """Стартовый перечень — фиксированные техпараметры RFQ (разделы 1–11); обязательные — по REQUIRED_CODES."""
    rows = list(db.scalars(select(TechParamTemplate).where(TechParamTemplate.machine_type_id.is_(None)).order_by(TechParamTemplate.no)).all())
    from .rfq_import import REQUIRED_CODES
    wanted = _general_fields()   # включая поля «L»: габариты и массы нужны и в КП
    have = {r.code for r in rows if r.code}
    missing = [f for f in wanted if f["code"] not in have]
    if rows and not missing:
        return rows
    for f in missing:
        rows.append(TechParamTemplate(machine_type_id=None, no=0, code=f["code"], name_ru=f.get("name") or f["code"], name_zh=f.get("name_zh") or "",
                                      unit=(f.get("unit") or "").split(" / ")[0], required=f["code"] in REQUIRED_CODES, section=f.get("section") or ""))
    # порядок — как в шаблоне RFQ (строки без кода, добавленные вручную, — в конце)
    order = {f["code"]: i for i, f in enumerate(wanted)}
    rows.sort(key=lambda r: (order.get(r.code, len(order)), r.no))
    for i, r in enumerate(rows, 1):
        r.no = i
    db.add_all(rows)
    db.flush()
    return rows


def template_for(db: Session, machine_type: str) -> list[TechParamTemplate]:
    mt = db.scalar(select(MachineType).where(MachineType.name == machine_type)) if machine_type else None
    if mt:
        rows = db.scalars(select(TechParamTemplate).where(TechParamTemplate.machine_type_id == mt.id).order_by(TechParamTemplate.no)).all()
        if rows:
            return rows
    return ensure_general_template(db)


def init_position_rows(db: Session, pos: Position, user_id: Optional[str], replace: bool = False) -> list[PositionTechParam]:
    """Перечень технических характеристик позиции: из исполнения каталога (задача 7) или из шаблона по типу машины."""
    if pos.tech_param_rows and not replace:
        return pos.tech_param_rows
    for r in list(pos.tech_param_rows or []):
        db.delete(r)
    rows: list[PositionTechParam] = []
    ex = db.get(EquipmentExecution, pos.execution_id) if pos.execution_id else None
    if ex and ex.tech_params:
        for i, r in enumerate(ex.tech_params, 1):
            rows.append(PositionTechParam(position_id=pos.id, no=r.get("no") or i, code=r.get("code") or "", name_ru=r.get("name_ru") or "",
                                          name_zh=r.get("name_zh") or "", unit=r.get("unit") or "", value_ru=r.get("value_ru") or "",
                                          value_zh=r.get("value_zh") or "", required=bool(r.get("required")), local_ru=bool(r.get("local_ru")),
                                          section=r.get("section") or section_label(r.get("code") or ""), source="catalog", filled_by=user_id))
    else:
        # поля, которые уже есть в карточке позиции (тип и модель), — сразу в таблицу
        known = {"1.1": pos.machine_type or "", "1.2": pos.model or ""}
        for t in template_for(db, pos.machine_type):
            rows.append(PositionTechParam(position_id=pos.id, no=t.no, code=t.code, name_ru=t.name_ru, name_zh=t.name_zh, unit=t.unit,
                                          value_ru=known.get(t.code, ""), required=t.required, section=section_label(t.code, t.section),
                                          source="manual", filled_by=user_id))
    db.add_all(rows)
    db.flush()
    db.refresh(pos)
    return rows


def _fmt_value(v: Any) -> str:
    """Число без «.0» (45.0 → «45»), остальное — как есть."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def rfq_rows(db: Session, parsed, machine_type: str = "") -> list[dict]:
    """Строки таблицы технических характеристик из разобранного RFQ: все поля разделов 1–11 из файла (с разделами, как в файле)
    плюс поля опросника компании, которых в файле нет (пустые, чтобы их можно было дозаполнить)."""
    from .rfq_import import REQUIRED_CODES
    tpl = template_for(db, machine_type)
    req_by_code = {t.code: t.required for t in tpl}
    out: dict[str, dict] = {}
    for code, f in parsed.fields.items():
        if not (1 <= f.section_no <= 11):
            continue                         # поля «L» (габариты, массы) — и логисту, и в таблицу: это тоже технические характеристики для КП
        v_ru, v_zh = f.value_ru, f.value_zh
        value_ru = "" if v_ru is None else str(v_ru).strip()
        value_zh = "" if v_zh is None else str(v_zh).strip()
        if not value_ru and value_zh and not has_cjk(value_zh):
            # завод вписал значение по-русски в столбец «Значение (кит.)» — это русское значение, текст сохраняем полностью («5 050 ± 50»)
            value_ru, value_zh = value_zh, ""
        if not value_ru and f.value not in (None, "") and not has_cjk(f.value):
            value_ru = _fmt_value(f.value)
        out[code] = {"code": code, "name_ru": f.name_ru, "name_zh": f.name_zh, "unit": (f.unit or "").split(" / ")[0],
                     "customer_requirement_ru": "" if f.requirement is None else str(f.requirement),
                     "customer_requirement_zh": "" if f.requirement_zh is None else str(f.requirement_zh),
                     "value_ru": value_ru, "value_zh": value_zh, "comment_ru": "" if f.comment is None else str(f.comment),
                     "required": req_by_code.get(code, code in REQUIRED_CODES), "section": section_label(code, f.section), "source": "rfq"}
    # поля опросника компании, которых нет в файле завода
    for t in tpl:
        if t.code and t.code not in out and 1 <= int(t.code.split(".")[0] or 0) <= 11:
            out[t.code] = {"code": t.code, "name_ru": t.name_ru, "name_zh": t.name_zh, "unit": t.unit, "customer_requirement_ru": "",
                           "customer_requirement_zh": "", "value_ru": "", "value_zh": "", "comment_ru": "", "required": t.required,
                           "section": section_label(t.code, t.section), "source": "template"}
    rows = sorted(out.values(), key=lambda r: [int(x) for x in r["code"].split(".")])
    for i, r in enumerate(rows, 1):
        r["no"] = i
    return rows


def rows_from_rfq(db: Session, pos: Position, parsed, user_id: Optional[str]) -> None:
    """Импорт RFQ (шаблон завода): таблица позиции заменяется строками из файла + недостающими полями опросника."""
    for r in list(pos.tech_param_rows or []):
        db.delete(r)
    for r in rfq_rows(db, parsed, pos.machine_type):
        db.add(PositionTechParam(position_id=pos.id, filled_by=user_id, translation_status="none", **r))
    db.flush()
    db.refresh(pos)


def row_dicts(pos: Position) -> list[dict]:
    out = [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in sorted(pos.tech_param_rows or [], key=lambda r: r.no)]
    for r in out:                       # строки, созданные до появления разделов, — раздел по коду поля
        if not r.get("section"):
            r["section"] = section_label(r.get("code") or "")
    return out


# ------------------------------------------------------------------ импорт таблиц с предпросмотром

def read_table(path: str, filename: str = "") -> list[dict]:
    """Листы файла → [{name, rows: [[ячейки]]}] (первые 300 строк каждого листа)."""
    ext = (os.path.splitext(filename or path)[1] or "").lower()
    sheets: list[dict] = []
    if ext in (".csv", ".txt"):
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            except csv.Error:
                dialect = csv.excel
                dialect.delimiter = ";"
            rows = [[c for c in r] for r in csv.reader(f, dialect)]
        sheets.append({"name": "CSV", "rows": rows[:300]})
    else:
        wb = load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            rows = []
            for i, r in enumerate(ws.iter_rows(values_only=True)):
                if i >= 300:
                    break
                rows.append(["" if v is None else (v.isoformat() if hasattr(v, "isoformat") else v) for v in r])
            sheets.append({"name": ws.title, "rows": rows})
    return sheets


def guess_mapping(rows: list[list]) -> dict:
    """Строка заголовков и соответствие столбцов по названиям (пользователь может поправить)."""
    best, best_row, best_cols = 0, 0, {}
    for i, row in enumerate(rows[:30]):
        cols: dict[str, int] = {}
        for j, cell in enumerate(row):
            v = _norm(cell)
            if not v:
                continue
            # самое длинное (конкретное) совпадение: «数值（中文）» → value_zh, а не value_ru по «数值»
            hits = [(len(a), key) for key, aliases in HEADER_ALIASES.items() for a in map(_norm, aliases) if v == a or v.startswith(a)]
            for _, key in sorted(hits, reverse=True):
                if key not in cols:
                    cols[key] = j
                    break
        if len(cols) > best:
            best, best_row, best_cols = len(cols), i, cols
    return {"header_row": best_row, "first_row": best_row + 1 if best else 0, "columns": best_cols}


def header_sig(rows: list[list], header_row: int) -> list[str]:
    if header_row is None or header_row >= len(rows):
        return []
    return [_norm(c) for c in rows[header_row]]


def saved_mapping(db: Optional[Session], scope: str, target: str, rows: list[list]) -> Optional[dict]:
    """Запомненное сопоставление для клиента/завода, если заголовки файла совпадают с прошлой загрузкой."""
    if db is None or not scope:
        return None
    for m in db.scalars(select(ImportMapping).where(ImportMapping.scope == scope, ImportMapping.target == target)
                        .order_by(ImportMapping.updated_at.desc())).all():
        hr = int((m.mapping or {}).get("header_row") or 0)
        if m.header_sig and header_sig(rows, hr) == m.header_sig:
            return {**m.mapping, "source": "saved"}
    return None


def remember_mapping(db: Session, scope: str, target: str, rows: list[list], mapping: dict, user_id: Optional[str]) -> None:
    if not scope:
        return
    hr = int(mapping.get("header_row") or 0)
    sig = header_sig(rows, hr)
    if not any(sig):
        return
    data = {"header_row": hr, "first_row": int(mapping.get("first_row") or hr + 1),
            "columns": {k: int(v) for k, v in (mapping.get("columns") or {}).items() if v is not None and v != ""}}
    m = next((x for x in db.scalars(select(ImportMapping).where(ImportMapping.scope == scope, ImportMapping.target == target)).all()
              if x.header_sig == sig), None)
    if m is None:
        db.add(ImportMapping(scope=scope, target=target, header_sig=sig, mapping=data, updated_by=user_id))
    else:
        m.mapping, m.updated_by = data, user_id


def preview(path: str, filename: str = "", db: Optional[Session] = None, scope: str = "", target: str = "requirements") -> dict:
    sheets = read_table(path, filename)
    out = []
    for s in sheets:
        g = saved_mapping(db, scope, target, s["rows"]) or {**guess_mapping(s["rows"]), "source": "guess"}
        out.append({"name": s["name"], "rows": s["rows"][:200], "total_rows": len(s["rows"]), "mapping": g})
    return {"sheets": out, "columns": [{"key": k, "title": COLUMN_TITLES[k]} for k in COLUMNS + ["value_zh"]]}


def _match(rows: list[PositionTechParam], no: Any, name: Any, synonyms: dict[str, str]) -> Optional[PositionTechParam]:
    """Сопоставление: по № и названию → по названию (с синонимами глоссария) → по №."""
    n_norm = _norm(name)
    n_syn = _norm(synonyms.get(n_norm, "")) if n_norm in synonyms else ""
    try:
        no_int = int(float(str(no).strip())) if str(no).strip() else None
    except ValueError:
        no_int = None
    code = str(no).strip() if no is not None else ""
    for r in rows:
        if no_int is not None and r.no == no_int and n_norm and _norm(r.name_ru) == n_norm:
            return r
    for r in rows:
        if n_norm and (_norm(r.name_ru) == n_norm or (n_syn and _norm(r.name_ru) == n_syn) or (r.name_zh and _norm(r.name_zh) == n_norm)):
            return r
    for r in rows:
        if code and r.code and r.code == code:
            return r
    for r in rows:
        if no_int is not None and r.no == no_int:
            return r
    return None


def apply_import(db: Session, pos: Position, path: str, filename: str, mapping: dict, user_id: Optional[str], target: str = "requirements",
                 selected_rows: Optional[list[int]] = None, add_unmatched: bool = True) -> dict:
    """Применяет выбранное сопоставление. target: requirements (файл клиента) | factory_answer (ответ завода, ZH→RU)."""
    sheets = read_table(path, filename)
    sheet = next((s for s in sheets if s["name"] == mapping.get("sheet")), sheets[0])
    cols: dict[str, int] = {k: int(v) for k, v in (mapping.get("columns") or {}).items() if v is not None and v != ""}
    first = int(mapping.get("first_row") or 0)
    last = int(mapping.get("last_row") or len(sheet["rows"]))
    if not pos.tech_param_rows:
        init_position_rows(db, pos, user_id)
    rows = list(pos.tech_param_rows)
    synonyms = glossary_map(db, "zh", "ru")
    matched, unmatched, added, conflicts = 0, [], 0, []
    zh_values: list[tuple[PositionTechParam, str, str]] = []
    max_no = max((r.no for r in rows), default=0)

    def cell(row: list, key: str) -> str:
        j = cols.get(key)
        if j is None or j >= len(row):
            return ""
        v = row[j]
        return "" if v is None else str(v).strip()

    for idx in range(first, min(last, len(sheet["rows"]))):
        if selected_rows is not None and idx not in selected_rows:
            continue
        row = sheet["rows"][idx]
        if not any(str(c).strip() for c in row if c is not None):
            continue
        name = cell(row, "name_ru")
        no = cell(row, "no")
        if not name and not no:
            continue
        r = _match(rows, no, name, synonyms)
        if r is None:
            if not add_unmatched or not name:
                unmatched.append({"row": idx, "no": no, "name": name})
                continue
            max_no += 1
            r = PositionTechParam(position_id=pos.id, no=max_no, name_ru=name if not has_cjk(name) else "", name_zh=name if has_cjk(name) else "",
                                  unit=cell(row, "unit"), source="client_import" if target == "requirements" else "factory_answer", filled_by=user_id)
            db.add(r)
            rows.append(r)
            added += 1
        else:
            matched += 1
        if target == "requirements":
            req = cell(row, "customer_requirement_ru") or cell(row, "value_ru")
            if req:
                if r.customer_requirement_ru and r.customer_requirement_ru != req:
                    conflicts.append({"no": r.no, "name": r.name_ru, "old": r.customer_requirement_ru, "new": req})
                r.customer_requirement_ru = req
            if cell(row, "unit") and not r.unit:
                r.unit = cell(row, "unit")
            if cell(row, "comment_ru"):
                r.comment_ru = cell(row, "comment_ru")
            r.source, r.filled_by = "client_import", user_id
        else:
            v_zh, v_ru, c = cell(row, "value_zh"), cell(row, "value_ru"), cell(row, "comment_ru")
            if v_ru and has_cjk(v_ru) and not v_zh:
                v_zh, v_ru = v_ru, ""
            if v_zh:
                r.value_zh = v_zh
            if v_ru:
                r.value_ru, r.translation_status = v_ru, "none"
            elif v_zh:
                zh_values.append((r, "value", v_zh))
            if c:
                if has_cjk(c):
                    r.comment_zh = c
                    zh_values.append((r, "comment", c))
                else:
                    r.comment_ru = c
            r.source, r.filled_by = "factory_answer", user_id
    # перевод ответа завода ZH→RU: глоссарий → переводчик
    if zh_values:
        tr = translate(db, [t for _, _, t in zh_values], "zh", "ru", kind="value", user_id=user_id)
        for (r, field, _), (text, status) in zip(zh_values, tr):
            if field == "value":
                r.value_ru, r.translation_status = text, status
            else:
                r.comment_ru = text or r.comment_ru
                if status == "machine":           # машинный перевод комментария тоже требует проверки человеком
                    r.translation_status = "machine"
    db.flush()
    db.refresh(pos)
    return {"matched": matched, "added": added, "unmatched": unmatched, "conflicts": conflicts, "rows": row_dicts(pos)}


# ------------------------------------------------------------------ запрос заводу

def build_request_xlsx(db: Session, pos: Position, factory: Optional[Factory], deal_number: str, user_id: Optional[str],
                       request_images: bool = True) -> tuple[str, list[dict]]:
    """Двуязычный файл запроса: № / параметр RU / 参数 / ед. / требования RU / 要求 / 数值 (пусто) / 备注. Строки «реализуется в России» не включаются.
    Возвращает путь и список строк с машинным переводом (на проверку)."""
    rows = [r for r in sorted(pos.tech_param_rows or [], key=lambda r: r.no) if not r.local_ru]
    names = translate(db, [r.name_ru for r in rows], "ru", "zh", "param", user_id)
    reqs = translate(db, [r.customer_requirement_ru for r in rows], "ru", "zh", "value", user_id)
    machine: list[dict] = []
    wb = Workbook()
    ws = wb.active
    ws.title = "Техданные 技术参数"
    ws["A1"] = f"Запрос технических характеристик / 技术参数询价 — {deal_number}"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Завод / 工厂: {(factory.name_zh or factory.name) if factory else ''}   Модель / 型号: {pos.designation}   Кол-во / 数量: {pos.qty}"
    ws["A3"] = "Просим заполнить столбцы «Значение / 数值» и «Комментарий / 备注» по каждому параметру с учётом требований заказчика. 请根据客户要求填写每项参数的数值和备注。"
    if request_images:
        ws["A4"] = "Просим приложить фото, рендеры и габаритные чертежи предлагаемой машины. 请附上所报机型的照片、效果图和外形尺寸图。"
    hdr = ["№", "Параметр", "参数", "Ед. изм. / 单位", "Требования заказчика", "客户要求", "Значение / 数值", "Комментарий / 备注"]
    for j, h in enumerate(hdr, 1):
        c = ws.cell(6, j, h)
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="FF3131")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, (r, (nz, ns), (rz, rs)) in enumerate(zip(rows, names, reqs), 7):
        name_zh = r.name_zh or nz
        req_zh = r.customer_requirement_zh or rz
        if r.name_zh != name_zh:
            r.name_zh = name_zh
        if r.customer_requirement_zh != req_zh:
            r.customer_requirement_zh = req_zh
        for j, v in enumerate([r.no, r.name_ru, name_zh, r.unit, r.customer_requirement_ru, req_zh, "", ""], 1):
            ws.cell(i, j, v)
        if ns == "machine" or rs == "machine":
            machine.append({"no": r.no, "name_ru": r.name_ru, "name_zh": name_zh, "requirement_zh": req_zh})
    for col, w in zip("ABCDEFGH", (6, 40, 30, 12, 30, 30, 24, 30)):
        ws.column_dimensions[col].width = w
    path = os.path.join(tempfile.mkdtemp(), f"{deal_number}_запрос_{(factory.name[:20] if factory else 'завод')}_{pos.model}.xlsx".replace("/", "-"))
    wb.save(path)
    return path, machine


def send_request(db: Session, deal, pos: Position, factory: Factory, file_path: str, image_paths: list[str], user_id: Optional[str],
                 via_wecom: bool = True, via_mail: bool = True) -> dict:
    """Отправка запроса заводу через WeCom (контакт из карточки завода) и e-mail; файл — в папку 01 сделки, запись в ленте."""
    from ..models import Attachment, FeedEntry
    from .deal_service import ensure_folders
    result: dict[str, Any] = {"wecom": None, "mail": None}
    text = f"Запрос технических характеристик по сделке {deal.number}: {pos.designation} × {pos.qty}. 技术参数询价，请填写附件并回复。"
    if via_wecom and factory.wecom_external_ids:
        try:
            result["wecom"] = adapters.messenger().send_to_external(factory.wecom_external_ids[0], text, deal.number)
        except Exception as e:
            result["wecom"] = f"ошибка: {e}"
    elif via_wecom:
        result["wecom"] = "не отправлено: в карточке завода нет контакта WeCom"
    # получатели — контакты завода по направлению «Заявки и КП»; если таких нет — все контакты с почтой
    contacts = factory.contacts or []
    rfq_contacts = [c for c in contacts if "rfq" in (c.get("roles") or [])]
    emails = [c.get("email") for c in (rfq_contacts or contacts) if c.get("email")]
    if via_mail and not emails:
        result["mail"] = "не отправлено: в карточке завода нет e-mail"
    if via_mail and emails:
        try:
            from ..adapters.mail import OutgoingMail
            result["mail"] = adapters.mail().send(OutgoingMail(to=emails, subject=f"Запрос технических характеристик / 技术参数询价 — {deal.number}",
                                                               body_html=f"<p>{text}</p>", attachments=[file_path] + image_paths))
        except Exception as e:
            result["mail"] = f"ошибка: {e}"
    try:
        ensure_folders(db, deal)
        stored = adapters.storage().upload(file_path, deal.storage_folder, "01_rfq", os.path.basename(file_path))
        db.add(Attachment(deal_id=deal.id, folder="01_rfq", entity="position", entity_id=pos.id, file_name=os.path.basename(file_path),
                          storage_path=stored.path, storage_url=stored.url, sha256=stored.sha256, size=stored.size, uploaded_by=user_id))
        result["file"] = stored.path
    except Exception as e:
        result["file"] = f"ошибка хранилища: {e}"
    db.add(FeedEntry(deal_id=deal.id, kind="wecom" if via_wecom else "mail_out", direction="out", counterparty=factory.name,
                     subject=f"Запрос заводу: {pos.designation}", body=text + (f" Вложения: {len(image_paths)} изобр." if image_paths else ""), user_id=user_id))
    return result


def fingerprint(rows: list[dict]) -> str:
    """Хеш набора характеристик (название + значение) — чтобы не плодить одинаковые исполнения в каталоге."""
    import hashlib
    key = "|".join(f"{_norm(r.get('name_ru'))}={_norm(r.get('value_ru'))}" for r in sorted(rows, key=lambda r: (r.get("no") or 0, r.get("name_ru") or "")) if r.get("value_ru"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
