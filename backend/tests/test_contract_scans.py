"""Сканы подписанных контрактов с клиентом и с заводом в карточке сделки."""
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

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from scripts.seed import run as seed  # noqa: E402
from datetime import date  # noqa: E402

from app.core.workflow import DealStatus  # noqa: E402
from app.models import Deal  # noqa: E402

H = lambda email: {"X-Dev-User": email}   # noqa: E731
MGR, CD, LOG = "manager@example.com", "cd@example.com", "logist@example.com"


def _set(deal_id, **kw):
    with SessionLocal() as db:
        d = db.get(Deal, deal_id)
        for k, v in kw.items():
            setattr(d, k, v)
        db.commit()


def test_contract_scans():
    seed()
    with TestClient(app) as c:
        cl = c.get("/api/refs/clients", headers=H(MGR)).json()[0]
        d = c.post("/api/deals", headers=H(MGR), json={"client_id": cl["id"], "site": f"скан-{uuid.uuid4().hex[:4]}"}).json()
        url = f"/api/deals/{d['id']}/contract-scans"
        _set(d["id"], status=DealStatus.CONTRACT)
        s = c.get(url, headers=H(MGR)).json()
        assert s["can_upload"] and not s["client"]["signed"] and s["client"]["files"] == []
        r = c.post(url, headers=H(MGR), files={"file": ("contract.pdf", b"%PDF client")}, data={"kind": "client"})
        assert r.status_code == 409                                                   # до отметки подписания — нельзя
        _set(d["id"], contract_number="K-1", contract_signed_at=date.today())
        assert c.post(url, headers=H(LOG), files={"file": ("x.pdf", b"x")}, data={"kind": "client"}).status_code == 403
        r = c.post(url, headers=H(MGR), files={"file": ("contract.pdf", b"%PDF client")}, data={"kind": "client"})
        assert r.status_code == 201, r.text
        s = r.json()
        assert [f["file_name"] for f in s["client"]["files"]] == ["contract.pdf"] and s["factory"]["files"] == []
        assert c.post(url, headers=H(CD), files={"file": ("f.pdf", b"%PDF f")}, data={"kind": "factory"}).status_code == 409
        _set(d["id"], factory_contract_signed_at=date.today())
        s = c.post(url, headers=H(CD), files={"file": ("factory.pdf", b"%PDF factory")}, data={"kind": "factory"}).json()
        assert [f["file_name"] for f in s["factory"]["files"]] == ["factory.pdf"]
        assert c.get(s["factory"]["files"][0]["url"], headers=H(MGR)).content == b"%PDF factory"
        docs = c.get(f"/api/deals/{d['id']}/documents", headers=H(CD)).json()["documents"]   # и во вкладке «Документы»
        assert {x["category"] for x in docs} >= {"contract_signed", "factory_contract_signed"}
