"""D-156: справочные рубли по дебиторке, методика KPI, группы проблем, ближайшие сроки, ожидаемый результат, фильтры счётчиков."""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from tests.test_workplace import GD, H, MGR  # noqa: E402


def test_rub_reference_uses_latest_cbr_rate_not_after_today():
    seed()
    from app.db import SessionLocal
    from app.models import FxRate
    from app.services.executive import rub_reference
    today = date.today()
    with SessionLocal() as db:
        for on, rate in ((today - timedelta(days=3), 12.0), (today - timedelta(days=1), 12.5), (today + timedelta(days=1), 99.0)):
            if not db.query(FxRate).filter(FxRate.on == on, FxRate.currency == "CNY", FxRate.source == "cbr").first():
                db.add(FxRate(on=on, currency="CNY", rate=rate, source="cbr"))
            else:
                db.query(FxRate).filter(FxRate.on == on, FxRate.currency == "CNY", FxRate.source == "cbr").update({"rate": rate})
        db.query(FxRate).filter(FxRate.on == today, FxRate.currency == "CNY").delete()
        db.commit()
        r = rub_reference(db, [(1000, "CNY"), (500, "RUB")], today)
        assert r["amount"] == 13000.0 and r["rates"][0]["rate"] == 12.5 and r["rates"][0]["on"] == (today - timedelta(days=1)).isoformat()
        assert "по курсу ЦБ" in r["text"]
        assert rub_reference(db, [(100, "RUB")], today) is None                 # рубли — без справки
        assert rub_reference(db, [(100, "XYZ")], today) is None                 # нет курса — нет справки (не ноль)


def test_pult_v4_shape_and_assignment_filters():
    seed()
    today = date.today()
    with TestClient(app) as c:
        ex = c.get("/api/workplace/executive", headers=H(GD)).json()["executive"]
        for k in ex["kpis"]:
            assert k["method"] and k["drilldown"]["list"]                     # методика и список у каждого показателя
            assert k["drilldown"]["list"] not in ex["drill"] or ex["drill"][k["drilldown"]["list"]] is None or "rows" in ex["drill"][k["drilldown"]["list"]]
        for e in ex["escalations"]:
            assert e["group"] and (not e["key"].startswith("deal:") or e["group"] == ":".join(e["key"].split(":")[:2]))
        for dl in ex["deadlines"]:
            assert {"on", "title", "type", "who", "url", "urgency"} <= set(dl) and dl["urgency"] in ("overdue", "today", "soon", "later")
        assert [x["on"] for x in ex["deadlines"]] == sorted(x["on"] for x in ex["deadlines"])
        mgr = c.get("/api/auth/me", headers=H(MGR)).json()["id"]
        a = c.post("/api/management/assignments", headers=H(GD), json={"title": "Сегодня", "assignee_id": mgr, "due_on": today.isoformat(),
                                                                       "expected_result": "оплата разнесена, выписка приложена"}).json()
        assert a["expected_result"] == "оплата разнесена, выписка приложена"
        ids = lambda st: {x["id"] for x in c.get(f"/api/management/assignments?scope=all&status={st}", headers=H(GD)).json()["items"]}  # noqa: E731
        assert a["id"] in ids("today") and a["id"] in ids("inwork") and a["id"] not in ids("review")
        ex = c.get("/api/workplace/executive", headers=H(GD)).json()["executive"]
        s = ex["assignments"]
        assert s["today"] == len(ids("today")) and s["open"] == len(ids("inwork")) and s["review"] == len(ids("review")) and s["overdue"] == len(ids("overdue"))
        assert any(d["type"] == "поручение" and d["urgency"] == "today" for d in ex["deadlines"])
