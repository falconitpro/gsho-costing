"""Заполнение DOCX-шаблонов с сохранением вёрстки (юридический блок, этап 2).

Плейсхолдеры: `{{имя}}` — в абзацах, таблицах, колонтитулах; имя может быть составным (`{{buyer.name_ru}}`).
Повторяемые строки таблиц: строка, в любой ячейке которой стоит маркер `{{#items}}`, повторяется для каждого элемента списка
`items`; внутри строки `{{name}}`, `{{qty}}` … — поля элемента (маркер удаляется). Вложенные списки не поддерживаются.

Word часто разбивает `{{имя}}` на несколько runs (проверка орфографии, смена форматирования) — текст абзаца собирается,
плейсхолдер подставляется, результат пишется в первый run (его форматирование сохраняется), остальные runs очищаются.
Незаполненные переменные остаются в тексте как есть и возвращаются списком — это блокирующая ошибка проверки пакета.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table, _Row
from docx.text.paragraph import Paragraph

VAR_RE = re.compile(r"\{\{\s*([#/]?[A-Za-z0-9_.\-\[\]]+)\s*\}\}")
LOOP_RE = re.compile(r"\{\{\s*#([A-Za-z0-9_]+)\s*\}\}")


@dataclass
class FillResult:
    replaced: int = 0
    unresolved: list[str] = field(default_factory=list)      # имена переменных, оставшихся незаполненными
    loops: dict[str, int] = field(default_factory=dict)      # список → число строк
    leftovers: list[str] = field(default_factory=list)       # служебные теги, оставшиеся в документе ({{#…}} без данных и т. п.)


# ------------------------------------------------------------------ обход документа

def _iter_paragraphs(doc: Document) -> Iterable[Paragraph]:
    """Все абзацы: тело, таблицы (рекурсивно), колонтитулы всех секций."""
    def walk_tables(tables):
        for t in tables:
            for row in t.rows:
                for cell in row.cells:
                    yield from cell.paragraphs
                    yield from walk_tables(cell.tables)
    yield from doc.paragraphs
    yield from walk_tables(doc.tables)
    for s in doc.sections:
        for part in (s.header, s.footer, s.first_page_header, s.first_page_footer, s.even_page_header, s.even_page_footer):
            if part is None:
                continue
            yield from part.paragraphs
            yield from walk_tables(part.tables)


def _iter_tables(doc: Document) -> Iterable[Table]:
    def walk(tables):
        for t in tables:
            yield t
            for row in t.rows:
                for cell in row.cells:
                    yield from walk(cell.tables)
    yield from walk(doc.tables)
    for s in doc.sections:
        for part in (s.header, s.footer, s.first_page_header, s.first_page_footer):
            if part is not None:
                yield from walk(part.tables)


def scan_variables(path: str) -> dict[str, Any]:
    """Переменные шаблона: обычные и списки (маркеры {{#name}}) с полями внутри повторяемых строк."""
    doc = Document(path)
    plain: set[str] = set()
    loops: dict[str, set[str]] = {}
    loop_rows: set[int] = set()
    for t in _iter_tables(doc):
        for row in t.rows:
            text = " ".join(c.text for c in row.cells)
            m = LOOP_RE.search(text)
            if m:
                loop_rows.add(id(row._tr))
                fields = {v for v in VAR_RE.findall(text) if not v.startswith(("#", "/"))}
                loops.setdefault(m.group(1), set()).update(fields)
    for p in _iter_paragraphs(doc):
        tr = p._p.getparent().getparent() if p._p.getparent() is not None else None
        in_loop = tr is not None and tr.tag == qn("w:tr") and id(tr) in loop_rows
        for v in VAR_RE.findall(p.text):
            if v.startswith(("#", "/")):
                continue
            if not in_loop:
                plain.add(v)
    return {"variables": sorted(plain), "loops": {k: sorted(v) for k, v in loops.items()}}


# ------------------------------------------------------------------ подстановка

def _lookup(data: dict, name: str) -> Any:
    cur: Any = data
    for part in name.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float):
        s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
        return s[:-3] if s.endswith(",00") else s
    return str(v)


def _replace_in_paragraph(p: Paragraph, data: dict, res: FillResult, strict_missing: bool = True) -> None:
    """Собирает текст абзаца из runs, заменяет плейсхолдеры, пишет результат в первый run каждого вхождения."""
    if "{{" not in p.text:
        return
    runs = p.runs
    if not runs:
        return
    full = "".join(r.text for r in runs)
    # позиции runs в сводном тексте
    bounds = []
    pos = 0
    for r in runs:
        bounds.append((pos, pos + len(r.text)))
        pos += len(r.text)
    out = full
    # идём с конца, чтобы индексы не сдвигались
    for m in reversed(list(VAR_RE.finditer(full))):
        name = m.group(1)
        if name.startswith(("#", "/")):
            continue
        val = _lookup(data, name)
        if val is None:
            if strict_missing and name not in res.unresolved:
                res.unresolved.append(name)
            continue
        out = out[:m.start()] + _fmt(val) + out[m.end():]
        res.replaced += 1
    if out == full:
        return
    # перераспределяем: первый run получает весь текст, остальные очищаются (форматирование первого run сохраняется)
    # чтобы не терять форматирование частей без плейсхолдеров, сохраняем runs, не затронутые заменой
    touched = [m for m in VAR_RE.finditer(full) if not m.group(1).startswith(("#", "/")) and _lookup(data, m.group(1)) is not None]
    first_touch, last_touch = min(m.start() for m in touched), max(m.end() for m in touched)
    start_idx = len([1 for (a, b) in bounds if b <= first_touch])          # runs целиком до первого плейсхолдера — не трогаем
    tail_idx = next((i for i, (a, b) in enumerate(bounds) if a >= last_touch), len(runs))   # runs целиком после последнего — тоже
    prefix_len = bounds[start_idx - 1][1] if start_idx else 0
    suffix_len = len(full) - (bounds[tail_idx][0] if tail_idx < len(runs) else len(full))
    runs[start_idx].text = out[prefix_len:len(out) - suffix_len]
    for r in runs[start_idx + 1:tail_idx]:
        r.text = ""


def _row_text(row: _Row) -> str:
    return " ".join(c.text for c in row.cells)


def _clone_row(row: _Row) -> _Row:
    new_tr = copy.deepcopy(row._tr)
    row._tr.addnext(new_tr)
    return _Row(new_tr, row._parent)


def _strip_marker(row: _Row, name: str) -> None:
    for cell in row.cells:
        for p in cell.paragraphs:
            if f"#{name}" in p.text:
                for r in p.runs:
                    if "{{" in r.text and f"#{name}" in r.text:
                        r.text = LOOP_RE.sub("", r.text)
                if f"#{name}" in p.text:        # маркер разбит на runs — склеиваем
                    t = LOOP_RE.sub("", p.text)
                    for i, r in enumerate(p.runs):
                        r.text = t if i == 0 else ""


def _expand_loops(doc: Document, data: dict, res: FillResult) -> None:
    for t in list(_iter_tables(doc)):
        for row in list(t.rows):
            m = LOOP_RE.search(_row_text(row))
            if not m:
                continue
            name = m.group(1)
            items = data.get(name)
            if not isinstance(items, list):
                res.leftovers.append(f"#{name}")
                items = []
            res.loops[name] = len(items)
            if not items:
                row._tr.getparent().remove(row._tr)
                continue
            _strip_marker(row, name)
            template_tr = copy.deepcopy(row._tr)
            last = row
            for i, item in enumerate(items):
                if i == 0:
                    target = row
                else:
                    new_tr = copy.deepcopy(template_tr)
                    last._tr.addnext(new_tr)
                    target = _Row(new_tr, row._parent)
                scope = {**data, **(item if isinstance(item, dict) else {"value": item}), "n": i + 1}
                for cell in target.cells:
                    for p in cell.paragraphs:
                        _replace_in_paragraph(p, scope, res)
                last = target


def fill_docx(src: str, dst: str, data: dict) -> FillResult:
    """Заполняет шаблон данными и сохраняет в dst. data: {имя: значение | dict | list[dict]}."""
    doc = Document(src)
    res = FillResult()
    _expand_loops(doc, data, res)
    for p in _iter_paragraphs(doc):
        _replace_in_paragraph(p, data, res)
    # повторный проход по колонтитулам/таблицам уже включён в _iter_paragraphs; проверяем остатки служебных тегов
    for p in _iter_paragraphs(doc):
        for v in VAR_RE.findall(p.text):
            if v.startswith(("#", "/")) and v not in res.leftovers:
                res.leftovers.append(v)
    res.unresolved = sorted(set(res.unresolved))
    doc.save(dst)
    return res


def document_text(path: str) -> str:
    """Весь текст документа (тело, таблицы, колонтитулы) — для проверок стоп-слов и остатков переменных."""
    doc = Document(path)
    return "\n".join(p.text for p in _iter_paragraphs(doc))
