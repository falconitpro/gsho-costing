"""Гарантийные претензии и распределение расходов партии."""
import os
import tempfile

from openpyxl import load_workbook

from app.services.parts_warranty import allocate, build_claim_xlsx, status_after_compensation


def test_status_after():
    assert status_after_compensation("accepted", [{"received_on": "2026-11-01"}]) == "compensated"
    assert status_after_compensation("partial", [{"received_on": "2026-11-01"}, {"received_on": None}]) == "partial"
    assert status_after_compensation("sent", [{"received_on": "x"}]) == "sent"
    assert status_after_compensation("accepted", []) == "accepted"


def test_allocate():
    lines = [{"id": "a", "weight": 10, "value": 100}, {"id": "b", "weight": 20, "value": 50}, {"id": "c", "weight": 0, "value": 10}]
    assert allocate(lines, 300) == {"a": 100.0, "b": 200.0, "c": 0.0}
    v = allocate(lines, 100, "value")
    assert sum(v.values()) == 100 and v["a"] > v["b"] > v["c"]
    assert allocate([{"id": "a"}, {"id": "b"}], 10) == {"a": 5.0, "b": 5.0}
    x = allocate([{"id": "a", "weight": 1}, {"id": "b", "weight": 1}, {"id": "c", "weight": 1}], 100)
    assert round(sum(x.values()), 2) == 100


def test_claim_xlsx():
    path = os.path.join(tempfile.mkdtemp(), "c.xlsx")
    build_claim_xlsx(path, number="ГП-2026-0001", supplier="Завод", client="Клиент", machine="LH410 зав. № 7", hours=1200, failure_on="01.11.2026",
                     description="течь", lines=[{"article": "A-1", "name": "Насос", "qty": 1}])
    rows = list(load_workbook(path).active.iter_rows(values_only=True))
    assert "保修索赔" in rows[0][0] and any(r[1] == "A-1" for r in rows)
