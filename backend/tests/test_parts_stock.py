"""Склад запчастей: отклонения при приёмке."""
from app.services.parts_stock import deviations_for


def test_deviations_for():
    assert deviations_for(4, 4, 0) == []
    assert deviations_for(4, 3, 0) == [("short", 1)]
    assert deviations_for(3, 1, 1, "damaged") == [("damaged", 1), ("short", 1)]
    assert deviations_for(3, 2, 1, "wrong") == [("wrong", 1)]
    assert deviations_for(2, 5, 0) == [("over", 3)]


def test_suggest_qty():
    from app.services.parts_stock import suggest_qty
    assert suggest_qty(10, 4, 0) == 6
    assert suggest_qty(10, 4, 6) == 0
    assert suggest_qty(10, 8, 0, moq=5) == 5
    assert suggest_qty(10, 3, 0, multiple=4) == 8
    assert suggest_qty(10, 12, 0) == 0
