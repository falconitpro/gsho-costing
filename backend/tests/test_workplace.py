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
MGR, CD, GD, FD, LAW, LOG = "manager@example.com", "cd@example.com", "gd@example.com", "fd@example.com", "lawyer@example.com", "logist@example.com"


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
