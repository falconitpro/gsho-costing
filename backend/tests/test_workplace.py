"""Рабочее место: сводка под роль, блоки по роли, личная раскладка и закрепление блоков ЗГД."""
from __future__ import annotations

import os
import tempfile

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

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, FD, LAW, LOG = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "lawyer@example.com", "logist@example.com"


def test_workplace_by_role_and_layout():
    seed()
    with TestClient(app) as c:
        m = c.get("/api/workplace", headers=H(MGR)).json()
        assert m["user"]["role"] == "manager" and m["user"]["greeting"].startswith("Добр")
        assert {k["title"] for k in m["kpis"]} >= {"Сделок в работе", "Просрочено задач"}
        assert len(m["today"]["week"]) == 7 and sum(1 for w in m["today"]["week"] if w["today"]) == 1
        assert "approvals" not in m["layout"]["order"] and "money" not in m["layout"]["order"]
        assert isinstance(m["tasks"], list) and isinstance(m["deals"], list) and isinstance(m["notifications"], list) and m["quick"]
        for r in m["deals"]:
            assert r["price_rub"] is None       # менеджер без финансовой аналитики не видит сумм
        # ЗГД: согласования, деньги, команда, внимание
        g = c.get("/api/workplace", headers=H(GD)).json()
        assert g["user"]["role"] == "gd" and g["layout"]["can_lock"] and g["money"] is not None and len(g["money"]["curve"]) == 8
        assert {"approvals", "money", "team", "attention", "legal"} <= set(g["layout"]["order"]) and g["legal"] is not None
        assert {k["title"] for k in g["kpis"]} >= {"Ждут решения", "Дебиторка просрочена"}
        # ФД: деньги есть, команды нет; юрист: юр. блок
        f = c.get("/api/workplace", headers=H(FD)).json()
        assert f["money"] is not None and "team" not in f["layout"]["order"] and f["fin"] is True
        law = c.get("/api/workplace", headers=H(LAW)).json()
        assert law["legal"] is not None and "legal" in law["layout"]["order"] and law["money"] is None
        lg = c.get("/api/workplace", headers=H(LOG)).json()
        assert lg["user"]["role"] == "logistician" and lg["money"] is None
        # личная раскладка: скрыть и переставить; чужие блоки отбрасываются
        order = list(reversed(m["layout"]["order"]))
        r = c.put("/api/workplace/layout", headers=H(MGR), json={"order": order + ["money"], "hidden": ["funnel", "approvals"]}).json()
        assert r["order"] == order and r["hidden"] == ["funnel"]
        m2 = c.get("/api/workplace", headers=H(MGR)).json()
        assert m2["layout"]["order"] == order and m2["layout"]["hidden"] == ["funnel"]
        # ЗГД закрепляет блок — менеджер больше не может его скрыть
        assert c.put("/api/workplace/role-blocks", headers=H(CD), json={"role": "manager", "required": ["funnel"]}).status_code == 403
        assert c.put("/api/workplace/role-blocks", headers=H(GD), json={"role": "manager", "required": ["funnel", "tasks", "nope"]}).json()["manager"] == ["funnel", "tasks"]
        m3 = c.get("/api/workplace", headers=H(MGR)).json()
        assert m3["layout"]["required"] == ["funnel", "tasks"] and m3["layout"]["hidden"] == []
        r = c.put("/api/workplace/layout", headers=H(MGR), json={"order": [], "hidden": ["funnel", "deadlines"]}).json()
        assert r["hidden"] == ["deadlines"]
        # ЗГД видит карту закреплений по ролям
        assert c.get("/api/workplace", headers=H(GD)).json()["layout"]["role_required"]["manager"] == ["funnel", "tasks"]
        # снять закрепление
        c.put("/api/workplace/role-blocks", headers=H(GD), json={"role": "manager", "required": []})
        c.put("/api/workplace/layout", headers=H(MGR), json={"order": [], "hidden": []})


def test_workplace_todo_feed_drafts_and_default_layout():
    """D-097: «Что сделать» — одна лента (задачи, решения, сроки, черновики без движения), по умолчанию — короткая раскладка роли."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.db import SessionLocal
    seed()
    with TestClient(app) as c:
        mgr_id = c.get("/api/auth/me", headers=H(MGR)).json()["id"]
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        did = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": "черновик"}).json()["id"]
        db = SessionLocal()
        # раскладка до D-097 сбрасывается на новую по умолчанию
        db.execute(text("update users set prefs = jsonb_build_object('workplace', jsonb_build_object('order', '[]'::jsonb, 'hidden', '[]'::jsonb)) where id = :u"), {"u": mgr_id})
        # 10 дней по московской дате (сервер считает «сегодня» по Москве): полдень UTC, чтобы тест не зависел от часа запуска
        msk_today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
        old = datetime.combine(msk_today - timedelta(days=10), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=12)
        db.execute(text("update deals set status = 'DRAFT', updated_at = :t where id = :d"), {"t": old, "d": did})
        db.commit()
        db.close()
        m = c.get("/api/workplace", headers=H(MGR)).json()
        lay = m["layout"]
        assert lay["order"][:2] == ["kpis", "todo"]
        assert {"focus", "tasks", "deadlines", "notifications", "briefing"} <= set(lay["hidden"]) and "todo" not in lay["hidden"] and "deals" not in lay["hidden"]
        drafts = [x for x in m["todo"] if x["kind"] == "draft"]
        assert drafts and drafts[0]["when"] == "10 дн. без движения" and "на расчёт или в архив" in drafts[0]["sub"] and drafts[0]["url"] == f"/deals/{did}"
        # без повторов: одна строка на задачу, задачи с id можно закрыть
        assert all(x["kind"] != "task" or x["task_id"] for x in m["todo"])
        assert len({(x["kind"], x["title"], x["url"]) for x in m["todo"]}) == len(m["todo"])
        # руководитель видит сводно черновики старше недели
        cd = c.get("/api/workplace", headers=H(CD)).json()
        assert any(a["title"].startswith("Черновиков без движения") for a in cd["attention"])
        assert "todo" in cd["layout"]["order"] and "attention" not in cd["layout"]["hidden"]
        # личная раскладка сохраняется с версией и больше не сбрасывается
        r = c.put("/api/workplace/layout", headers=H(MGR), json={"order": lay["order"], "hidden": ["funnel"]}).json()
        assert r["v"] == 2
        assert c.get("/api/workplace", headers=H(MGR)).json()["layout"]["hidden"] == ["funnel"]
