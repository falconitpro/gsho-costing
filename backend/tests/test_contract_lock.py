"""Номер и дата подписанного договора фиксируются; изменить может только ЗГД или КД отдельным действием с причиной (история хранится)."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date

import pytest

if not os.environ.get("DATABASE_URL"):
    pytest.skip("DATABASE_URL не задан — тест API на живой БД пропущен", allow_module_level=True)

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("AUTH_PROVIDER", "dev")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.core.workflow import DealStatus  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Deal  # noqa: E402
from scripts.seed import run as seed  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, GD, FD = "manager@example.com", "cd@example.com", "evgeniy@dobrov.pro", "fd@example.com"


def _set(deal_id, **kw):
    with SessionLocal() as db:
        d = db.get(Deal, deal_id)
        for k, v in kw.items():
            setattr(d, k, v)
        db.commit()


def test_signed_contract_is_locked():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"фиксация-{uuid.uuid4().hex[:4]}"}).json()
        _set(d["id"], status=DealStatus.CONTRACT)
        base = f"/api/deals/{d['id']}/execution"

        # до подписания менеджер вносит и правит свободно
        assert c.patch(f"{base}/contract", headers=H(MGR), json={"contract_number": "K-1"}).status_code == 200
        assert c.patch(f"{base}/contract", headers=H(MGR), json={"contract_number": "K-2"}).status_code == 200
        # подписание клиентом
        r = c.post(f"{base}/contract-step", headers=H(MGR), json={"code": "client_signed", "done": True, "on": "2026-11-01", "contract_number": "K-2"})
        assert r.status_code == 200, r.text
        st = c.get(f"{base}/stages", headers=H(MGR)).json()
        assert st["client_locked"] and not st["factory_locked"] and not st["can_amend"]

        # менеджер (и даже КД через обычное сохранение) номер и дату не меняет; те же значения — можно (форма отправляет всё)
        assert c.patch(f"{base}/contract", headers=H(MGR), json={"contract_number": "K-3"}).status_code == 409
        assert c.patch(f"{base}/contract", headers=H(CD), json={"contract_signed_at": "2026-11-02"}).status_code == 409
        assert c.patch(f"{base}/contract", headers=H(MGR), json={"contract_number": "K-2", "contract_signed_at": "2026-11-01", "factory_contract_number": "F-1"}).status_code == 200
        # повторное подписание и снятие отметки менеджером — нельзя
        assert c.post(f"{base}/contract-step", headers=H(MGR), json={"code": "client_signed", "done": True, "contract_number": "K-9"}).status_code == 409
        assert c.post(f"{base}/contract-step", headers=H(MGR), json={"code": "client_signed", "done": False, "comment": "ошибка"}).status_code == 403

        # отдельное действие: только ЗГД или КД, с причиной
        amend = {"side": "client", "number": "K-2/1", "signed_at": "2026-11-03", "reason": "доп. соглашение № 1"}
        assert c.post(f"{base}/contract-amend", headers=H(MGR), json=amend).status_code == 403
        assert c.post(f"{base}/contract-amend", headers=H(FD), json=amend).status_code == 403
        assert c.post(f"{base}/contract-amend", headers=H(CD), json={**amend, "reason": ""}).status_code == 422
        assert c.post(f"{base}/contract-amend", headers=H(CD), json={**amend, "side": "factory"}).status_code == 409   # с заводом ещё не подписан
        r = c.post(f"{base}/contract-amend", headers=H(CD), json=amend)
        assert r.status_code == 200, r.text
        st = c.get(f"{base}/stages", headers=H(CD)).json()
        assert st["contract_number"] == "K-2/1" and str(st["contract_signed_at"]) == "2026-11-03" and st["can_amend"]
        h = st["contract_history"][0]
        assert (h["old_number"], h["old_date"], h["number"], h["date"], h["reason"]) == ("K-2", "2026-11-01", "K-2/1", "2026-11-03", "доп. соглашение № 1")
        assert next(s for s in st["contract_steps"] if s["code"] == "client_signed")["on"] == "2026-11-03"

        # ЗГД снимает отметку с причиной — запись в истории
        _set(d["id"], factory_contract_signed_at=date(2026, 11, 4))
        assert c.post(f"{base}/contract-amend", headers=H(GD), json={"side": "factory", "number": "F-1", "signed_at": "2026-11-05", "reason": "дата по скану"}).status_code == 200
        assert c.post(f"{base}/contract-step", headers=H(GD), json={"code": "client_signed", "done": False, "comment": ""}).status_code == 422
        assert c.post(f"{base}/contract-step", headers=H(GD), json={"code": "client_signed", "done": False, "comment": "перезаключаем"}).status_code == 200
        st = c.get(f"{base}/stages", headers=H(GD)).json()
        assert not st["client_locked"] and st["factory_locked"]
        assert [x["action"] for x in st["contract_history"]] == ["unsigned", "amended", "amended"]
