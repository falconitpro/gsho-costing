"""Парк машин: наработка, следующее ТО, состав ТО, строки комплекта."""
from datetime import date

from app.services.parts_fleet import avg_per_day, due_date, hours_now, kit_lines, next_to

K = [{"id": "250", "interval_hours": 250}, {"id": "500", "interval_hours": 500}, {"id": "1000", "interval_hours": 1000}]


def test_avg_and_now():
    r = [(date(2026, 10, 1), 1000.0), (date(2026, 10, 11), 1150.0)]
    assert avg_per_day(r) == 15
    assert avg_per_day([(date(2026, 10, 11), 300.0)], commissioned_on=date(2026, 9, 1)) == 7.5
    assert avg_per_day([], manual=12) == 12
    assert hours_now(r, 15, date(2026, 10, 21)) == 1300


def test_next_to():
    h, due = next_to(1300, K)
    assert h == 1500 and [k["id"] for k in due] == ["500", "250"]
    h, due = next_to(1500, K)
    assert h == 1750 and [k["id"] for k in due] == ["250"]
    h, due = next_to(1999, K)
    assert h == 2000 and [k["id"] for k in due] == ["1000", "500", "250"]
    assert next_to(10, []) is None
    assert due_date(1300, 1500, 20, date(2026, 11, 1)) == date(2026, 11, 11)
    assert due_date(1300, 1500, None, date(2026, 11, 1)) is None


def test_kit_lines():
    k = [{"lines": [{"part_id": "a", "article": "A", "qty": 2}, {"part_id": "b", "article": "B", "qty": 1}]}, {"lines": [{"part_id": "a", "article": "A", "qty": 1}]}]
    assert kit_lines(k) == [{"part_id": "a", "article": "A", "name": "", "qty": 3.0}, {"part_id": "b", "article": "B", "name": "", "qty": 1.0}]
