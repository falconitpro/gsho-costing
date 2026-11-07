"""Контрагенты: аналитика в списке клиентов, наборы реквизитов и их снимок в реквизитах контракта сделки."""
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
MGR, CD, LOG = "manager@example.com", "cd@example.com", "logist@example.com"


def _set(deal_id, **kw):
    with SessionLocal() as db:
        d = db.get(Deal, deal_id)
        for k, v in kw.items():
            setattr(d, k, v)
        db.commit()


def test_client_list_stats_and_requisites_in_contract():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"реквизиты-{uuid.uuid4().hex[:4]}"}).json()

        # список со статистикой: у менеджера нет суммы выигранных, у КД — есть ключ
        rows = c.get("/api/refs/clients?stats=1", headers=H(MGR)).json()
        me = next(r for r in rows if r["id"] == cl["id"])
        assert me["stats"]["deals"] >= 1 and me["stats"]["open"] >= 1 and me["stats"]["sum_won_rub"] is None
        rows_cd = c.get("/api/refs/clients?stats=1", headers=H(CD)).json()
        assert next(r for r in rows_cd if r["id"] == cl["id"])["stats"]["sum_won_rub"] is not None

        # поставщики со статистикой: ГШО и запасные части раздельно; суммы по валютам — только finance_analytics
        fac = c.get("/api/refs/factories?stats=1", headers=H(MGR)).json()
        assert fac and all("gsho" in f["stats"] and "parts" in f["stats"] for f in fac)
        assert all(f["stats"]["gsho"]["sum_won"] is None and f["stats"]["parts"]["po_sum"] is None for f in fac)
        fac_cd = c.get("/api/refs/factories?stats=1", headers=H(CD)).json()
        assert all(isinstance(f["stats"]["gsho"]["sum_won"], dict) for f in fac_cd)
        # сделка с позицией завода попадает в статистику ГШО этого завода
        pos = c.post(f"/api/deals/{d['id']}/positions", headers=H(MGR), json={"factory_id": fac[0]["id"], "model": "T-1", "qty": 2, "unit_price": 1000, "currency": "CNY"})
        if pos.status_code in (200, 201):
            f0 = next(f for f in c.get("/api/refs/factories?stats=1", headers=H(CD)).json() if f["id"] == fac[0]["id"])
            assert f0["stats"]["gsho"]["deals"] >= 1 and f0["stats"]["gsho"]["open"] >= 1

        # наборы реквизитов: логист не редактирует; два набора, один по умолчанию
        url = f"/api/refs/clients/{cl['id']}/requisites"
        assert c.get(url, headers=H(LOG)).json()["from_card"]["inn"] == cl["inn"]
        assert c.put(url, headers=H(LOG), json={"items": []}).status_code == 403
        items = [{"title": "Головной офис", "is_default": True, "legal_name": "ООО «Клиент»", "inn": cl["inn"], "kpp": "111", "bank_name": "Банк", "account": "40702810000000000001", "signatory_name": "Иванов И. И."},
                 {"title": "Филиал", "is_default": False, "legal_name": "ООО «Клиент», филиал", "inn": cl["inn"], "kpp": "222"}]
        r = c.put(url, headers=H(MGR), json={"items": items})
        assert r.status_code == 200, r.text
        saved = r.json()["items"]
        assert len(saved) == 2 and saved[0]["is_default"] and saved[0]["id"] and not saved[1]["is_default"]
        assert c.put(url, headers=H(MGR), json={"items": [{**saved[0], "is_default": True}, {**saved[1], "is_default": True}]}).status_code == 422

        # реквизиты контракта: варианты = наборы + «из карточки»; выбор фиксирует снимок
        _set(d["id"], status=DealStatus.CONTRACT)
        ex = c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(MGR)).json()
        assert [o["id"] for o in ex["client_requisites_options"]] == [saved[0]["id"], saved[1]["id"]]   # юридические профили клиента
        assert ex["client_requisites"] == {}
        r = c.patch(f"/api/deals/{d['id']}/execution/contract", headers=H(MGR), json={"client_requisites_id": saved[1]["id"]})
        assert r.status_code == 200, r.text
        ex = c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(MGR)).json()
        assert ex["client_requisites_id"] == saved[1]["id"] and ex["client_requisites"]["kpp"] == "222"
        # карточку клиента потом поправили — снимок в договоре не изменился
        c.put(url, headers=H(MGR), json={"items": [saved[0], {**saved[1], "kpp": "333"}]})
        assert c.get(f"/api/deals/{d['id']}/execution/stages", headers=H(MGR)).json()["client_requisites"]["kpp"] == "222"
        assert c.patch(f"/api/deals/{d['id']}/execution/contract", headers=H(MGR), json={"client_requisites_id": "nope"}).status_code == 422
