"""Склад запчастей: отклонения при приёмке."""
from app.services.parts_stock import deviations_for


def test_deviations_for():
    assert deviations_for(4, 4, 0) == []
    assert deviations_for(4, 3, 0) == [("short", 1)]
    assert deviations_for(3, 1, 1, "damaged") == [("damaged", 1), ("short", 1)]
    assert deviations_for(3, 2, 1, "wrong") == [("wrong", 1)]
    assert deviations_for(2, 5, 0) == [("over", 3)]
