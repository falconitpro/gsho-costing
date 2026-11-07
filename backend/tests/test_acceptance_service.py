"""Приёмка на заводе: чек-лист, правила решения, акт."""
import os
import tempfile

from docx import Document

from app.services.acceptance_service import build_checklist, decision_error, default_tests, render_act


def test_checklist():
    cl = build_checklist(tech_rows=[{"name_ru": "Грузоподъёмность", "unit": "т / t", "value_ru": "10", "required": True},
                                    {"name_ru": "Цвет", "customer_requirement_ru": "жёлтый", "value_ru": "жёлтый"},
                                    {"name_ru": "Без требований", "value_ru": "1"}, {"name_ru": "Пожаротушение", "required": True, "value_ru": "да", "local_ru": True}],
                         zip_items=[{"name": "Фильтр", "qty": 2, "unit": "шт", "kind": "supply"}, {"name": "Масло", "kind": "to"}], doc_kinds=["manual"])
    items = [(x["group"], x["item"], x["expected"]) for x in cl]
    assert ("Соответствие техническим характеристикам", "Грузоподъёмность, т", "10") in items
    assert ("Соответствие техническим характеристикам", "Цвет", "жёлтый") in items
    assert not any(i[1].startswith("Без требований") or i[1].startswith("Пожаротушение") for i in items)
    assert ("Комплектность: ЗИП в комплекте поставки", "Фильтр", "2 шт") in items and not any("Масло" in i[1] for i in items)
    assert any(i[0] == "Документация" and "Руководство" in i[1] and "каталоге" in i[1] for i in items)
    own = build_checklist(model_items=[{"group": "Кузов", "item": "Футеровка"}, {"item": "Прочее"}])
    assert [(x["group"], x["item"]) for x in own] == [("Кузов", "Футеровка"), ("Проверки", "Прочее")]


def test_decision_rules():
    cl = [{"item": "a", "result": "ok"}, {"item": "b", "result": "fail"}]
    ts = [{"name": "t", "result": "ok"}]
    assert "заводской номер" in decision_error("accepted", serial="", checklist=cl, tests=ts, remarks=[])
    assert "несоответствия" in decision_error("accepted", serial="S1", checklist=cl, tests=ts, remarks=[])
    rem = [{"text": "царапина", "fixed_on": None}]
    assert "неустранённые" in decision_error("accepted", serial="S1", checklist=cl, tests=ts, remarks=rem)
    assert decision_error("accepted_remarks", serial="S1", checklist=cl, tests=ts, remarks=rem) is None
    assert decision_error("accepted", serial="S1", checklist=[{"item": "a", "result": "ok"}], tests=ts, remarks=[{"text": "x", "fixed_on": "2026-11-01"}]) is None
    assert "Не отмечены" in decision_error("accepted", serial="S1", checklist=[{"item": "a", "result": ""}], tests=ts, remarks=[])
    assert decision_error("rejected", serial="", checklist=[], tests=[], remarks=[], comment="течь") is None
    assert decision_error("rejected", serial="", checklist=[], tests=[], remarks=[]) is not None
    assert len(default_tests()) >= 6


def test_act():
    path = os.path.join(tempfile.mkdtemp(), "act.docx")
    render_act(path, deal_number="2026-001", client="Клиент", factory="Завод", model="LH410",
               unit={"serial": "SN1", "checklist": [{"group": "g", "item": "i", "result": "ok"}], "tests": [], "remarks": [], "decision": "accepted",
                     "act": {"number": "1", "date": "2026-11-07"}})
    text = "\n".join(p.text for p in Document(path).paragraphs)
    assert "Акт приёмки" in text and "принята" in text
