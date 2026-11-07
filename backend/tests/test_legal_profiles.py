"""Юридический блок, этап 1: профили контрагентов и компаний группы, банковские профили, права, история, мост в реквизиты контракта."""
from __future__ import annotations

import os
import tempfile
import uuid

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
MGR, CD, FD, LAW, LOG, OBS = "manager@example.com", "cd@example.com", "fd@example.com", "lawyer@example.com", "logist@example.com", "observer@example.com"


def test_legal_profiles():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        fac = c.get("/api/refs/factories", headers=H(MGR)).json()[0]

        # наблюдателю закрыто; логист читает, но не правит
        assert c.get(f"/api/legal/profiles?owner_kind=client&owner_id={cl['id']}", headers=H(OBS)).status_code == 403
        r = c.get(f"/api/legal/profiles?owner_kind=client&owner_id={cl['id']}", headers=H(LOG)).json()
        assert r["can_edit"] is False and len(r["items"]) == 1
        auto = r["items"][0]
        assert auto["kind"] == "ru_customer" and auto["is_default"] and auto["tax_number"] == cl["inn"] and auto["names"]["ru"]["full"]
        assert auto["history"][0]["action"] == "auto_created"

        # менеджер правит: история, сброс «проверен»
        r = c.put(f"/api/legal/profiles/{auto['id']}", headers=H(MGR), json={"signatory_name": "Иванов И. И.", "signatory_position": "Директор", "signatory_basis": "Устава", "kpp": "770101001"})
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["kpp"] == "770101001" and p["history"][0]["action"] == "update" and "kpp" in p["history"][0]["fields"]
        assert c.post(f"/api/legal/profiles/{auto['id']}/check", headers=H(MGR), json={"status": "checked"}).status_code == 403
        p = c.post(f"/api/legal/profiles/{auto['id']}/check", headers=H(LAW), json={"status": "checked", "comment": "ок"}).json()
        assert p["check_status"] == "checked" and p["checked_by_name"]
        p = c.put(f"/api/legal/profiles/{auto['id']}", headers=H(MGR), json={"legal_address": "Москва, Тверская, 1"}).json()
        assert p["check_status"] == "not_checked"            # реквизиты изменились — проверка заново

        # банк: добавить, подтвердить (ФД), изменить счёт → подтверждение снимается, архив
        assert c.post(f"/api/legal/profiles/{auto['id']}/banks", headers=H(LOG), json={"bank_name": "Банк"}).status_code == 403
        b = c.post(f"/api/legal/profiles/{auto['id']}/banks", headers=H(MGR), json={"title": "Основной", "currency": "rub", "bank_name": "АО «Банк»", "bik": "044525000", "account": "40702810000000000001"}).json()
        assert b["currency"] == "RUB" and not b["verified"]
        assert c.post(f"/api/legal/banks/{b['id']}/verify", headers=H(MGR)).status_code == 403
        b = c.post(f"/api/legal/banks/{b['id']}/verify", headers=H(FD)).json()
        assert b["verified"] and b["verified_by_name"]
        b = c.put(f"/api/legal/banks/{b['id']}", headers=H(MGR), json={"account": "40702810000000000002"}).json()
        assert not b["verified"] and b["history"][0]["fields"]["account"] == ["40702810000000000001", "40702810000000000002"]
        b = c.post(f"/api/legal/banks/{b['id']}/archive", headers=H(MGR)).json()
        assert b["status"] == "archived" and b["valid_to"]

        # второй профиль (филиал) и смена «по умолчанию»
        p2 = c.post("/api/legal/profiles", headers=H(MGR), json={"owner_kind": "client", "owner_id": cl["id"], "title": "Филиал", "kind": "ru_customer", "kpp": "770243001", "is_default": True}).json()
        items = c.get(f"/api/legal/profiles?owner_kind=client&owner_id={cl['id']}", headers=H(MGR)).json()["items"]
        assert [x["is_default"] for x in items] == [True, False] and items[0]["id"] == p2["id"]

        # мост в реквизиты контракта сделки: варианты = активные профили, по умолчанию первым; снимок берётся из профиля
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"юр-{uuid.uuid4().hex[:4]}"}).json()
        with SessionLocal() as db:
            db.get(Deal, d["id"]).status = DealStatus.CONTRACT
            db.commit()
        ex = c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(MGR)).json()
        assert [o["id"] for o in ex["client_requisites_options"]] == [p2["id"], auto["id"]]
        assert c.patch(f"/api/deals/{d['id']}/execution/contract", headers=H(MGR), json={"client_requisites_id": auto["id"]}).status_code == 200
        snap = c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(MGR)).json()["client_requisites"]
        assert snap["kpp"] == "770101001" and snap["signatory_name"] == "Иванов И. И." and snap["legal_address"] == "Москва, Тверская, 1"

        # поставщик: профиль из карточки завода (китайский завод, банк из bank_details при наличии)
        r = c.get(f"/api/legal/profiles?owner_kind=factory&owner_id={fac['id']}", headers=H(MGR)).json()
        assert r["items"][0]["kind"] in ("cn_factory", "foreign_supplier", "kg_company") and r["items"][0]["names"]["en"]["full"] == fac["name"]

        # карточка БЕРГ-ХАУС: компании группы с профилями; правит только КД/ЗГД/ФД/юрист/админ
        own = c.get("/api/legal/own", headers=H(MGR)).json()
        assert own["items"] and own["can_edit"] is False and all(e["profiles"] for e in own["items"])
        ru = next(e for e in own["items"] if e["sells_to_clients"])
        pid = ru["profiles"][0]["id"]
        assert c.put(f"/api/legal/profiles/{pid}", headers=H(MGR), json={"tax_number": "7700000000"}).status_code == 403
        assert c.put(f"/api/legal/profiles/{pid}", headers=H(FD), json={"tax_number": "7700000000", "names": {"ru": {"full": "ООО «БЕРГ-ХАУС»", "short": "БЕРГ-ХАУС"}, "en": {"full": "BERG-HAUS LLC", "short": ""}, "zh": {"full": "", "short": ""}}}).status_code == 200
        bk = c.post(f"/api/legal/profiles/{pid}/banks", headers=H(FD), json={"title": "Валютный USD", "currency": "USD", "bank_name": "Bank", "swift": "ABCDRUMM", "account": "40702840000000000001", "purpose": "расчёты с поставщиками"}).json()
        assert bk["currency"] == "USD"
        own = c.get("/api/legal/own", headers=H(CD)).json()
        assert own["can_edit"] and next(e for e in own["items"] if e["id"] == ru["id"])["profiles"][0]["names"]["en"]["full"] == "BERG-HAUS LLC"
