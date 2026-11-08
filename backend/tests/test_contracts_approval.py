"""Реестр договоров (этап 2): маршрут согласования (юрист → ФД, КД, ЗГД), заморозка условий, возврат на доработку, новый круг после изменений,
передача на подпись, регистрация подписанного, доп. соглашение меняет основной договор, напоминания о сроке действия."""
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

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, FD, LAW, LOG = "manager@example.com", "cd@example.com", "gd@example.com", "fd@example.com", "lawyer@example.com", "logist@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")


def test_contract_approval_route():
    seed()
    with TestClient(app) as c:
        ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
        fac = c.get("/api/refs/factories", headers=H(LAW)).json()[0]
        k = c.post("/api/legal/contracts", headers=H(LOG), json={
            "kind": "supplier", "own_entity_id": ent["id"], "cp_kind": "factory", "cp_id": fac["id"], "client_order": True, "subject": "запчасти под заказ клиента",
            "amount": 500000, "currency": "RUB", "signatory": {"name": "Иванов И. И.", "basis": "Устав"},
            "payment_terms": [{"share_pct": 100, "event": "contract", "offset_days": 5, "offset_kind": "working"}],
            "performance_terms": [{"code": "delivery", "event": "contract", "offset_days": 60, "offset_kind": "calendar"}]}).json()
        cid = k["id"]
        assert k["status"] == "draft" and k["route_preview"] == ["Юрист", "ФД", "КД", "ЗГД"]

        # на согласование: сначала юрист
        k = c.post(f"/api/legal/contracts/{cid}/submit", headers=H(LOG)).json()
        assert k["status"] == "legal_review" and k["pending"] == ["Юрист"] and len(k["approval"]["steps"]) == 4
        assert c.post(f"/api/legal/contracts/{cid}/visa", headers=H(FD), json={"decision": "approve"}).status_code == 403
        assert c.put(f"/api/legal/contracts/{cid}", headers=H(LOG), json={"amount": 90000}).status_code == 409     # условия заморожены
        assert c.put(f"/api/legal/contracts/{cid}", headers=H(LOG), json={"notes": "оригинал у логиста"}).status_code == 200
        # юрист правит цену до своей визы: маршрут пересобран (без ЗГД), круг тот же
        k = c.put(f"/api/legal/contracts/{cid}", headers=H(LAW), json={"amount": 90000}).json()
        assert k["status"] == "legal_review" and [s["code"] for s in k["approval"]["steps"]] == ["lawyer", "fd", "cd"] and k["approval"]["round"] == 1

        k = c.post(f"/api/legal/contracts/{cid}/visa", headers=H(LAW), json={"decision": "approve"}).json()
        assert k["status"] == "approval" and set(k["pending"]) == {"ФД", "КД"}
        assert any(x["id"] == cid for x in c.get("/api/legal/contracts-inbox", headers=H(FD)).json()["visas"])
        assert c.get("/api/workplace", headers=H(FD)).status_code == 200

        # ФД возвращает на доработку — только с комментарием
        assert c.post(f"/api/legal/contracts/{cid}/visa", headers=H(FD), json={"decision": "reject"}).status_code == 422
        k = c.post(f"/api/legal/contracts/{cid}/visa", headers=H(FD), json={"decision": "reject", "comment": "добавить аккредитив"}).json()
        assert k["status"] == "draft"
        assert any(x["id"] == cid for x in c.get("/api/legal/contracts-inbox", headers=H(LOG)).json()["drafts"])

        # второй круг: все визы → согласован
        k = c.post(f"/api/legal/contracts/{cid}/submit", headers=H(LOG)).json()
        assert k["approval"]["round"] == 2
        for who in (LAW, FD, CD):
            k = c.post(f"/api/legal/contracts/{cid}/visa", headers=H(who), json={"decision": "approve"}).json()
        assert k["status"] == "approved" and k["pending"] == []

        # изменение условий после виз — согласование заново, цена выше порога — нужен ЗГД
        k = c.put(f"/api/legal/contracts/{cid}", headers=H(LAW), json={"amount": 200000}).json()
        assert k["status"] == "legal_review" and k["approval"]["round"] == 3 and "gd" in [s["code"] for s in k["approval"]["steps"]]
        for who in (LAW, FD, CD, GD):
            k = c.post(f"/api/legal/contracts/{cid}/visa", headers=H(who), json={"decision": "approve"}).json()
        assert k["status"] == "approved"

        # на подпись → подписан (скан обязателен)
        k = c.post(f"/api/legal/contracts/{cid}/status", headers=H(LOG), json={"status": "signing"}).json()
        assert k["status"] == "signing"
        assert c.post(f"/api/legal/contracts/{cid}/status", headers=H(LOG), json={"status": "active"}).status_code == 422
        c.put(f"/api/legal/contracts/{cid}", headers=H(LOG), json={"signed_on": date.today().isoformat()})
        c.post(f"/api/legal/contracts/{cid}/files", headers=H(LOG), files={"file": PDF}, data={"kind": "signed_scan"})
        k = c.post(f"/api/legal/contracts/{cid}/status", headers=H(LOG), json={"status": "active"}).json()
        assert k["status"] == "active" and all(x["ok"] for x in k["sign_checklist"] if not x.get("optional"))

        # доп. соглашение: новая цена и срок действия переходят в основной договор
        new_to = (date.today() + timedelta(days=400)).isoformat()
        ds = c.post("/api/legal/contracts", headers=H(LOG), json={"parent_id": cid, "subject": "увеличение объёма", "amount": 250000, "valid_to": new_to}).json()
        assert {x["field"] for x in c.get(f"/api/legal/contracts/{ds['id']}", headers=H(LOG)).json()["amends"]} >= {"amount", "valid_to"}
        c.put(f"/api/legal/contracts/{ds['id']}", headers=H(LAW), json={"signed_on": date.today().isoformat()})
        c.post(f"/api/legal/contracts/{ds['id']}/files", headers=H(LAW), files={"file": PDF}, data={"kind": "signed_scan"})
        assert c.post(f"/api/legal/contracts/{ds['id']}/status", headers=H(LOG), json={"status": "active"}).status_code == 403   # мимо маршрута — только юрист / ЗГД
        assert c.post(f"/api/legal/contracts/{ds['id']}/status", headers=H(LAW), json={"status": "active"}).json()["status"] == "active"
        k = c.get(f"/api/legal/contracts/{cid}", headers=H(LAW)).json()
        assert k["amount"] == 250000 and k["valid_to"] == new_to and any(h["action"] == "amendment" for h in k["history"])

    # напоминания о сроке действия и автопролонгация
    from app.db import SessionLocal
    from app.models import Contract
    from app.services import legal_contracts as svc
    today = date.today()
    with SessionLocal() as db:
        x = db.get(Contract, cid)
        x.valid_to, x.notified, x.auto_renew = today + timedelta(days=5), [], False
        db.commit()
        assert svc.check_expiring(db, today) >= 1
        x = db.get(Contract, cid)
        assert {"expiring_7", "expiring_30"} <= set(x.notified)
        svc.check_expiring(db, today)
        assert x.notified.count("expiring_7") == 1
        x.valid_to, x.auto_renew = today - timedelta(days=1), True
        db.commit()
        svc.check_expiring(db, today)
        x = db.get(Contract, cid)
        assert x.valid_to > today and any(h["action"] == "renewed" for h in x.history)
        db.commit()
