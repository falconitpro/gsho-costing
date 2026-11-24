"""D-154: поручения ЗГД — полный цикл, права через API (не только интерфейс), руководители направлений и замещение."""
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
from tests.test_workplace import CD, FD, GD, H, MGR  # noqa: E402

B = "/api/management"


def _ids(c):
    us = c.get(f"{B}/assignees", headers=H(GD)).json()["users"]
    me = {u["name"]: u["id"] for u in us}
    byrole = {}
    for email in (CD, FD, MGR):
        byrole[email] = c.get("/api/auth/me", headers=H(email)).json()["id"]
    return me, byrole


def test_assignment_full_cycle_and_rights():
    seed()
    due = (date.today() + timedelta(days=5)).isoformat()
    with TestClient(app) as c:
        _, ids = _ids(c)
        cd, mgr = ids[CD], ids[MGR]
        # оргструктура: только ЗГД
        assert c.put(f"{B}/directions", headers=H(MGR), json={"heads": {"sales": cd}}).status_code == 403
        r = c.put(f"{B}/directions", headers=H(GD), json={"heads": {"sales": cd, "factory": None}})
        assert r.status_code == 200 and next(x for x in r.json()["items"] if x["key"] == "sales")["head_id"] == cd
        # создать — только ЗГД; срок в прошлом нельзя
        body = {"title": "Уточнить дату оплаты", "expected_result": "дата оплаты подтверждена клиентом письменно", "mode": "direct", "assignee_id": mgr, "due_on": due, "priority": "high", "source_key": "deal:x:receivable"}
        assert c.post(f"{B}/assignments", headers=H(MGR), json=body).status_code == 403
        assert c.post(f"{B}/assignments", headers=H(GD), json={**body, "due_on": "2020-01-01"}).status_code == 422
        a = c.post(f"{B}/assignments", headers=H(GD), json=body).json()
        aid = a["id"]
        assert a["status"] == "new" and a["head_id"] == cd and a["number"].startswith("П-")
        # видимость: руководитель видит, посторонний — нет
        assert c.get(f"{B}/assignments/{aid}", headers=H(FD)).status_code == 404
        h = c.get(f"{B}/assignments/{aid}", headers=H(CD)).json()
        assert h["my_role"] == ["head"] and h["actions"] == ["comment"]

        def act(who, **kw):
            return c.post(f"{B}/assignments/{aid}/action", headers=H(who), json=kw)
        assert act(MGR, action="submit", report="x").status_code == 403          # сначала принять
        assert act(MGR, action="accept").json()["status"] == "accepted"
        assert act(MGR, action="start").json()["status"] == "in_progress"
        assert act(MGR, action="block").status_code == 422                       # причина обязательна
        assert act(MGR, action="block", comment="клиент в отпуске").json()["status"] == "blocked"
        assert act(MGR, action="unblock").json()["status"] == "in_progress"
        assert act(MGR, action="submit").status_code == 422                      # отчёт обязателен
        assert act(MGR, action="submit", report="оплата 26.11").json()["status"] == "review"
        assert act(CD, action="approve").status_code == 403                      # руководитель не принимает прямое поручение
        assert act(MGR, action="approve").status_code == 403                     # исполнитель — тоже
        assert act(GD, action="return").status_code == 422
        assert act(GD, action="return", comment="нужен номер платёжки").json()["status"] == "rework"
        assert act(MGR, action="submit", report="п/п 123").json()["status"] == "review"
        new_due = (date.today() + timedelta(days=9)).isoformat()
        assert act(MGR, action="reschedule", due_on=new_due, comment="банк").status_code == 403
        r = act(GD, action="reschedule", due_on=new_due, comment="ждём банк").json()
        assert r["due_on"] == new_due and r["first_due_on"] == due and r["rescheduled"]
        done = act(GD, action="approve").json()
        assert done["status"] == "done" and done["actions"] == ["comment"]
        hist = [x["action"] for x in done["history"]]
        assert hist == ["create", "accept", "start", "block", "unblock", "submit", "return", "submit", "reschedule", "approve"]
        assert next(x for x in done["history"] if x["action"] == "reschedule")["data"] == {"from": due, "to": new_due}
        # уведомления: исполнитель получил «новое», автор — «сдал»
        notes = c.get("/api/notifications", headers=H(MGR)).json()
        assert any("П-" in (n.get("text") or "") for n in (notes.get("items") if isinstance(notes, dict) else notes))

        # поручение руководителю: только назначенному руководителю; передать исполнителю; исполнитель не принимает
        hb = {"title": "План продаж на декабрь", "expected_result": "план по месяцам утверждён", "mode": "head", "direction_key": "sales", "due_on": due}
        assert c.post(f"{B}/assignments", headers=H(GD), json={**hb, "assignee_id": mgr}).status_code == 422
        assert c.post(f"{B}/assignments", headers=H(GD), json={**hb, "direction_key": "factory", "assignee_id": cd}).status_code == 422
        b = c.post(f"{B}/assignments", headers=H(GD), json={**hb, "assignee_id": cd}).json()
        bid = b["id"]
        assert c.post(f"{B}/assignments/{bid}/action", headers=H(CD), json={"action": "delegate", "executor_id": mgr}).json()["executor_id"] == mgr
        assert c.post(f"{B}/assignments/{bid}/action", headers=H(MGR), json={"action": "accept"}).status_code == 403
        assert c.post(f"{B}/assignments/{bid}/action", headers=H(CD), json={"action": "accept"}).json()["status"] == "accepted"
        assert c.post(f"{B}/assignments/{bid}/action", headers=H(MGR), json={"action": "start"}).json()["status"] == "in_progress"
        assert c.post(f"{B}/assignments/{bid}/action", headers=H(GD), json={"action": "cancel"}).status_code == 422
        assert c.post(f"{B}/assignments/{bid}/action", headers=H(GD), json={"action": "cancel", "comment": "неактуально"}).json()["status"] == "cancelled"

        # реестры
        mine = [x["id"] for x in c.get(f"{B}/assignments?scope=mine&status=any", headers=H(MGR)).json()["items"]]
        assert aid in mine and bid in mine
        fd_ids = {x["id"] for x in c.get(f"{B}/assignments?scope=all&status=any", headers=H(FD)).json()["items"]}
        assert aid not in fd_ids and bid not in fd_ids
        team = [x["id"] for x in c.get(f"{B}/assignments?scope=team&status=any", headers=H(CD)).json()["items"]]
        assert aid in team

        # пульт: счётчики поручений, руководитель направления, сигнал с поручением
        ex = c.get("/api/workplace/executive", headers=H(GD)).json()["executive"]
        assert {"open", "overdue", "today", "review", "items"} <= set(ex["assignments"])
        sales = next(d for d in ex["directions"] if d["key"] == "sales")
        assert sales["head"] and sales["head"]["id"] == cd
        assert next(d for d in ex["directions"] if d["key"] == "factory")["head"] is None
        for k in ex["kpis"]:
            assert {"unit", "calculated_at", "period", "data_quality", "drilldown"} <= set(k) and k["calculated_at"]
        exec_k = next(k for k in ex["kpis"] if k["key"] == "exec")
        assert exec_k["unit"] == "этапов" and len(exec_k["extra"]) == 2
