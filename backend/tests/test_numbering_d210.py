"""D-210: номера обращений, нарядов и табельные — из атомарного счётчика: параллельные запросы получают разные номера,
счётчик продолжает уже выданные номера."""
import threading
import uuid

from sqlalchemy import text

from tests.test_api_flow import client  # noqa: F401


def test_next_seq_parallel_distinct(client):  # noqa: F811
    from app.db import SessionLocal
    from app.services.numbering import next_seq
    key = f"t{uuid.uuid4().hex[:8]}"
    got, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        try:
            with SessionLocal() as db:
                barrier.wait()
                for _ in range(5):
                    got.append(next_seq(db, key, 2026, 1))
                    db.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and sorted(got) == list(range(1, 41))


def test_counter_continues_existing_numbers(client):  # noqa: F811
    from app.db import SessionLocal
    from app.services.numbering import next_seq
    key = f"t{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        assert next_seq(db, key, 2026, 57) == 57       # первый раз — продолжает с наибольшего выданного + 1
        assert next_seq(db, key, 2026, 57) == 58
        assert next_seq(db, key, 2026, 10) == 59       # старое значение start не откатывает счётчик
        assert next_seq(db, key, 2027, 1) == 1         # новый год — с единицы
        db.rollback()


def test_service_and_hr_numbers_from_counter(client):  # noqa: F811
    from app.db import SessionLocal
    from app.models import ServiceRequest
    from app.services import hr_core, work_orders
    with SessionLocal() as db:
        a, b = work_orders.request_number(db), work_orders.request_number(db)
        assert a != b and a.startswith("СО-") and int(b.rsplit("-", 1)[1]) == int(a.rsplit("-", 1)[1]) + 1
        assert db.execute(text("SELECT count(*) FROM number_counters WHERE key = :k"), {"k": ServiceRequest.__tablename__}).scalar() == 1
        p1, p2 = hr_core.next_personnel_no(db), hr_core.next_personnel_no(db)
        assert p1 != p2 and p1.startswith("П-")
        db.rollback()
