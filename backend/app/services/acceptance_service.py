"""Приёмка машины на заводе: чек-лист по модели, испытания, замечания, решение и акт; серийный номер — машина в парк.

Чек-лист собирается из чек-листа модели каталога (если КД/ЗГД его задали) или типового, плюс строки по сделке:
соответствие техническим характеристикам (обязательные параметры и требования заказчика), комплектность ЗИП «в комплекте поставки»,
документация (руководство, каталог запчастей, схемы — из документов модели).
Окончательный расчёт с заводом по позиции — только когда все машины позиции приняты (с замечаниями — тоже).
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable, Mapping, Optional

DECISIONS = {"accepted": "принята", "accepted_remarks": "принята с замечаниями", "rejected": "не принята"}
RESULTS = {"ok": "соответствует", "fail": "не соответствует", "na": "не применимо"}
DOC_KINDS = {"manual": "Руководство по эксплуатации", "parts_catalog": "Каталог запасных частей", "drawing": "Чертежи", "hydraulic": "Гидравлическая схема",
             "electric": "Электрическая схема", "pneumatic": "Пневматическая схема", "scheme": "Прочие схемы", "certificate": "Сертификаты и декларации",
             "other": "Прочее"}

DEFAULT_GROUPS: list[tuple[str, list[str]]] = [
    ("Документация", ["Паспорт / формуляр машины", "Руководство по эксплуатации на русском языке", "Каталог запасных частей", "Сертификат / декларация соответствия"]),
    ("Внешний осмотр", ["Окраска и антикоррозионное покрытие без повреждений", "Сварные швы без дефектов", "Нет течей масла, топлива, охлаждающей жидкости",
                        "Таблички и маркировка (заводской номер, двигатель)", "Шины / гусеницы, колёсные диски", "Кабина: остекление, сиденье, ремни, зеркала"]),
    ("Комплектность", ["Инструмент и принадлежности", "Ключи, пломбы", "Опции по контракту установлены"]),
]
DEFAULT_TESTS = ["Пуск двигателя и работа на холостом ходу", "Гидравлическая система: давление, отсутствие течей", "Тормозная система (рабочий и стояночный)",
                 "Рулевое управление", "Электрооборудование, освещение, звуковые сигналы", "Рабочее оборудование (подъём, опускание, ковш / кузов)",
                 "Системы безопасности (пожаротушение, аварийный останов)", "Ходовые испытания"]


def _id() -> str:
    return uuid.uuid4().hex[:12]


def build_checklist(model_items: Iterable[Mapping[str, Any]] = (), tech_rows: Iterable[Mapping[str, Any]] = (),
                    zip_items: Iterable[Mapping[str, Any]] = (), doc_kinds: Iterable[str] = ()) -> list[dict]:
    """Чек-лист приёмки одной машины."""
    out: list[dict] = []
    custom = [dict(x) for x in model_items if str(x.get("item") or "").strip()]
    groups = [(g, [x["item"] for x in custom if (x.get("group") or "Проверки") == g]) for g in dict.fromkeys(x.get("group") or "Проверки" for x in custom)] \
        if custom else DEFAULT_GROUPS
    for g, items in groups:
        for it in items:
            out.append({"id": _id(), "group": g, "item": it, "expected": "", "result": "", "comment": ""})
    have_docs = set(doc_kinds)
    for k in ("manual", "parts_catalog", "hydraulic", "electric"):
        if k in have_docs:
            out.append({"id": _id(), "group": "Документация", "item": f"{DOC_KINDS[k]} — соответствует документу в каталоге", "expected": "", "result": "", "comment": ""})
    for r in tech_rows:
        name = str(r.get("name_ru") or "").strip()
        if not name or r.get("local_ru"):
            continue
        req = str(r.get("customer_requirement_ru") or "").strip()
        val = str(r.get("value_ru") or "").strip()
        if not (r.get("required") or req) or not (req or val):
            continue
        unit = str(r.get("unit") or "").split(" / ")[0].strip()
        out.append({"id": _id(), "group": "Соответствие техническим характеристикам", "item": name + (f", {unit}" if unit and unit != "—" else ""),
                    "expected": req or val, "result": "", "comment": ""})
    for z in zip_items:
        if (z.get("kind") or "supply") != "supply" or not str(z.get("name") or z.get("article") or "").strip():
            continue
        label = " ".join(x for x in (str(z.get("article") or ""), str(z.get("name") or "")) if x).strip()
        out.append({"id": _id(), "group": "Комплектность: ЗИП в комплекте поставки", "item": label,
                    "expected": f"{z.get('qty') or 1:g} {z.get('unit') or 'шт'}" if isinstance(z.get("qty") or 1, (int, float)) else str(z.get("qty")),
                    "result": "", "comment": ""})
    return out


def default_tests() -> list[dict]:
    return [{"id": _id(), "name": t, "result": "", "value": "", "note": ""} for t in DEFAULT_TESTS]


def open_remarks(remarks: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in remarks if str(r.get("text") or "").strip() and not r.get("fixed_on")]


def decision_error(decision: str, *, serial: str, checklist: Iterable[Mapping], tests: Iterable[Mapping], remarks: Iterable[Mapping],
                   comment: str = "") -> Optional[str]:
    """Почему решение нельзя принять (или None)."""
    if decision not in DECISIONS:
        return "Решение: принята, принята с замечаниями или не принята"
    cl, ts, op = list(checklist), list(tests), open_remarks(remarks)
    if decision == "rejected":
        return None if (comment.strip() or op) else "Укажите причину или замечания, по которым машина не принята"
    if not serial.strip():
        return "Укажите заводской номер машины"
    empty = [x["item"] for x in cl if not x.get("result")] + [x["name"] for x in ts if not x.get("result")]
    if empty:
        return f"Не отмечены пункты: {', '.join(empty[:5])}{' …' if len(empty) > 5 else ''}"
    fails = [x["item"] for x in cl if x.get("result") == "fail"] + [x["name"] for x in ts if x.get("result") == "fail"]
    if decision == "accepted":
        if op:
            return f"Есть неустранённые замечания ({len(op)}) — «принята с замечаниями» или устраните"
        if fails:
            return f"Есть несоответствия: {', '.join(fails[:5])} — оформите замечания"
    if decision == "accepted_remarks" and not op:
        return "Замечаний нет — выберите «принята»"
    return None


def render_act(path: str, *, deal_number: str, client: str, factory: str, model: str, unit: Mapping[str, Any]) -> str:
    """Акт приёмки машины на заводе (Word): номера, итоги проверок и испытаний, замечания, решение, подписи сторон."""
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Arial", Pt(10)
    act = unit.get("act") or {}
    doc.add_heading(f"Акт приёмки машины на заводе № {act.get('number') or '—'}", level=1)
    doc.add_paragraph(f"Дата: {act.get('date') or unit.get('inspected_on') or '—'}. Место: {unit.get('place') or factory}.")
    doc.add_paragraph(f"Сделка {deal_number}, покупатель — {client}. Поставщик — {factory}.")
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for k, v in (("Модель", model), ("Заводской номер машины", unit.get("serial")), ("Номер двигателя", unit.get("engine_no")), ("Номер рамы", unit.get("frame_no")),
                 *[(f"Агрегат: {c.get('name')}", c.get("serial")) for c in unit.get("components") or [] if c.get("name")]):
        row = t.add_row().cells
        row[0].text, row[1].text = str(k), str(v or "—")
    doc.add_heading("Проверки", level=2)
    t = doc.add_table(rows=1, cols=4)
    t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, ("Раздел", "Пункт", "Требование", "Результат")):
        c.text = h
    for x in unit.get("checklist") or []:
        row = t.add_row().cells
        row[0].text, row[1].text, row[2].text = x.get("group") or "", x.get("item") or "", x.get("expected") or ""
        row[3].text = RESULTS.get(x.get("result") or "", "—") + (f" — {x['comment']}" if x.get("comment") else "")
    doc.add_heading("Испытания", level=2)
    t = doc.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, ("Испытание", "Показатель", "Результат")):
        c.text = h
    for x in unit.get("tests") or []:
        row = t.add_row().cells
        row[0].text, row[1].text = x.get("name") or "", x.get("value") or ""
        row[2].text = RESULTS.get(x.get("result") or "", "—") + (f" — {x['note']}" if x.get("note") else "")
    rem = [r for r in unit.get("remarks") or [] if str(r.get("text") or "").strip()]
    doc.add_heading("Замечания", level=2)
    if rem:
        t = doc.add_table(rows=1, cols=4)
        t.style = "Table Grid"
        for c, h in zip(t.rows[0].cells, ("Замечание", "Ответственный", "Срок", "Устранено")):
            c.text = h
        for r in rem:
            row = t.add_row().cells
            row[0].text, row[1].text, row[2].text = r.get("text") or "", r.get("responsible") or "", str(r.get("due") or "")
            row[3].text = (str(r.get("fixed_on")) + (f" — {r['fixed_note']}" if r.get("fixed_note") else "")) if r.get("fixed_on") else "нет"
    else:
        doc.add_paragraph("Замечаний нет.")
    doc.add_heading("Решение", level=2)
    doc.add_paragraph(f"Машина {DECISIONS.get(unit.get('decision') or '', 'решение не принято')}." + (f" {unit['comment']}" if unit.get("comment") else ""))
    doc.add_paragraph("")
    doc.add_paragraph(f"От покупателя: ____________________ {unit.get('inspector') or ''}")
    doc.add_paragraph(f"От завода: ____________________ {unit.get('factory_rep') or ''}")
    doc.save(path)
    return path
