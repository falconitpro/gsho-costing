"""Реестр договоров (этап 4): лестница претензий (черновик на 5-й день просрочки), подпись КД / ЗГД, печатная форма,
отправка → неустойки «предъявлены», частичное удовлетворение → «признаны» пропорционально, входящая претензия."""
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
CD, GD, FD, LAW = "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com", "lawyer@example.com"
PDF = ("scan.pdf", b"%PDF-1.4 test", "application/pdf")


def test_claims_ladder_and_lifecycle():
    seed()
    today = date.today()
    with TestClient(app) as c:
        ent = c.get("/api/legal/contracts", headers=H(LAW)).json()["entities"][0]
        cl_ref = c.get("/api/refs/clients", headers=H(CD)).json()[0]
        k = c.post("/api/legal/contracts", headers=H(CD), json={
            "kind": "client_parts", "own_entity_id": ent["id"], "cp_kind": "client", "cp_id": cl_ref["id"], "subject": "запчасти — претензия", "amount": 1000000, "currency": "RUB",
            "signatory": {"name": "Иванов И. И.", "basis": "Устав"}, "signed_on": (today - timedelta(days=40)).isoformat(),
            "payment_terms": [{"share_pct": 100, "event": "contract", "offset_days": 10, "offset_kind": "calendar"}]}).json()
        cid = k["id"]
        c.post(f"/api/legal/contracts/{cid}/files", headers=H(LAW), files={"file": PDF}, data={"kind": "signed_scan"})
        assert c.post(f"/api/legal/contracts/{cid}/status", headers=H(LAW), json={"status": "active"}).json()["status"] == "active"

    # лестница: просрочка 30 дней → уведомление и черновик претензии (один раз)
    from app.db import SessionLocal
    from app.services import legal_claims
    with SessionLocal() as db:
        assert legal_claims.ladder(db, today) >= 2
        db.commit()
        legal_claims.ladder(db, today)
        db.commit()

    with TestClient(app) as c:
        mine = c.get(f"/api/legal/contracts/{cid}/claims", headers=H(LAW)).json()
        assert len(mine["items"]) == 1
        cl = mine["items"][0]
        assert cl["source"] == "auto" and cl["status"] == "draft" and cl["debt"] == 1000000 and cl["penalty"] == 30000 and cl["subject"] == "о нарушении срока оплаты"
        reg = c.get("/api/legal/claims?direction=outgoing", headers=H(GD)).json()
        assert any(x["id"] == cl["id"] for x in reg["items"]) and reg["summary"]["draft"] >= 1
        assert any(a.get("kind") == "claim" for a in c.get("/api/workplace", headers=H(LAW)).json()["approvals"])

        # на подпись → подписывает только КД или ЗГД; печатная форма сохраняется
        assert c.post(f"/api/legal/claims/{cl['id']}/to-sign", headers=H(LAW), json={}).json()["status"] == "signing"
        assert c.post(f"/api/legal/claims/{cl['id']}/sign", headers=H(LAW), json={}).status_code == 403
        x = c.post(f"/api/legal/claims/{cl['id']}/sign", headers=H(CD), json={}).json()
        assert x["status"] == "signed" and x["signer_role"] == "cd" and any(f["name"].endswith(".docx") for f in x["files"])
        r = c.get(f"/api/legal/claims/{cl['id']}/print?fmt=docx", headers=H(LAW))
        assert r.status_code == 200 and r.content[:2] == b"PK"

        # отправка → неустойка предъявлена, срок ответа по договору (30 дней)
        x = c.post(f"/api/legal/claims/{cl['id']}/send", headers=H(LAW), json={"sent_on": today.isoformat(), "sent_via": "post", "track": "RA123"}).json()
        assert x["status"] == "sent" and x["answer_due"] == (today + timedelta(days=30)).isoformat()
        pen = c.get(f"/api/legal/contracts/{cid}/obligations", headers=H(LAW)).json()["penalties"][0]
        assert pen["status"] == "claimed" and pen["amount"] == 30000

        # частично удовлетворена (половина) → неустойка признана на 15 000
        assert c.post(f"/api/legal/claims/{cl['id']}/answer", headers=H(LAW), json={"result": "partial"}).status_code == 422
        x = c.post(f"/api/legal/claims/{cl['id']}/answer", headers=H(LAW), json={"result": "partial", "amount": 515000, "comment": "гарантийное письмо"}).json()
        assert x["status"] == "answered" and x["result_amount"] == 515000
        pen = c.get(f"/api/legal/contracts/{cid}/obligations", headers=H(LAW)).json()["penalties"][0]
        assert pen["status"] == "recognized" and pen["amount"] == 15000
        assert c.post(f"/api/legal/claims/{cl['id']}/status", headers=H(LAW), json={"status": "court"}).status_code == 422
        assert c.post(f"/api/legal/claims/{cl['id']}/status", headers=H(LAW), json={"status": "closed"}).json()["status"] == "closed"

        # входящая претензия: к нам, ручная строка, срок ответа
        x = c.post(f"/api/legal/contracts/{cid}/claims", headers=H(LAW), json={"direction": "incoming", "number": "№ 45/11", "issued_on": today.isoformat(),
                                                                               "lines": [{"kind": "penalty", "title": "Штраф за недопоставку", "amount": 5000}]}).json()
        assert x["status"] == "received" and x["number"] == "№ 45/11" and x["total"] == 5000 and x["answer_due"]
        assert c.post(f"/api/legal/claims/{x['id']}/answer", headers=H(LAW), json={"result": "rejected", "comment": "поставка в срок"}).json()["result"] == "rejected"
        assert c.get("/api/legal/claims?direction=incoming", headers=H(FD)).json()["items"][0]["direction"] == "incoming"
