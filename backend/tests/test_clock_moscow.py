"""D-194: «сегодня» — по Москве везде (решение владельца 27.11.2026).

С 00:00 до 03:00 МСК дата по UTC — ещё вчерашняя. Проверяем перевод момента в дату, единый источник «сегодня»
и запрет на date.today() в коде приложения (храповик: новый код не должен брать дату по часам контейнера).
"""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.core import clock

APP = Path(__file__).resolve().parents[1] / "app"


def _freeze(monkeypatch, utc: datetime):
    monkeypatch.setattr(clock, "now", lambda: utc.astimezone(clock.MSK))


def test_today_and_msk_date_after_utc_evening(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 11, 26, 22, 30, tzinfo=timezone.utc))      # 01:30 27.11 по Москве
    assert clock.today() == date(2026, 11, 27)
    assert clock.msk_date(datetime(2026, 11, 26, 22, 30, tzinfo=timezone.utc)) == date(2026, 11, 27)
    assert clock.msk_date(datetime(2026, 11, 26, 22, 30)) == date(2026, 11, 27)      # без пояса — UTC
    assert clock.msk_date(datetime(2026, 11, 26, 20, 59, tzinfo=timezone.utc)) == date(2026, 11, 26)
    assert clock.msk_date(date(2026, 1, 2)) == date(2026, 1, 2) and clock.msk_date(None) is None
    from app.services.finance.cashview import moscow_today
    assert moscow_today() == date(2026, 11, 27)                                       # прежний помощник — тот же источник


def test_no_container_date_in_app():
    bad = []
    for f in APP.rglob("*.py"):
        if f.name == "clock.py":
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "date.today()" in code or re.search(r"datetime\.now\(timezone\.utc\)\.date\(\)", code):
                bad.append(f"{f.relative_to(APP)}:{i}")
    assert not bad, "дата по часам контейнера — используйте app.core.clock.today() / msk_date(): " + ", ".join(bad)


def test_assignment_due_today_moscow_accepted_after_utc_evening(monkeypatch):
    """В 01:30 МСК срок «сегодня» (27.11) — не в прошлом, хотя по UTC ещё 26.11."""
    from fastapi.testclient import TestClient

    from app.main import app
    from scripts.seed import run as seed
    from tests.test_api_flow import H, MGR
    seed()
    real = datetime.now(timezone.utc)
    utc_evening = datetime(real.year, real.month, real.day, 22, 30, tzinfo=timezone.utc)
    if utc_evening < real - timedelta(hours=1):
        utc_evening += timedelta(days=1)
    _freeze(monkeypatch, utc_evening)
    msk = clock.today()
    assert msk == utc_evening.date() + timedelta(days=1)
    with TestClient(app) as c:
        mgr = c.get("/api/auth/me", headers=H(MGR)).json()["id"]
        r = c.post("/api/management/assignments", headers=H("evgeniy@dobrov.pro"),
                   json={"title": "Срок по Москве", "assignee_id": mgr, "due_on": msk.isoformat(), "expected_result": "проверено"})
        assert r.status_code == 200, r.text
        assert r.json()["due_on"] == msk.isoformat()
